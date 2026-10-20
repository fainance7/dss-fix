import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.core.rbac import require_admin
from app.core.cache import get_cache
from app.database import get_db
from app.models.user import User
from app.services.intake_quality import DataQualitySettings

router = APIRouter(prefix="/settings", tags=["settings"])

SETTINGS_KEY = "system:settings"


class DataSourceSettings(BaseModel):
    id: str
    name: str
    url: str
    api_key: str
    refresh_interval_hours: int
    enabled: bool
    last_check: Optional[str] = None
    status: Optional[str] = None


class BackupSettings(BaseModel):
    schedule_cron: str = "0 3 * * *"
    retention_days: int = 30
    last_backup: Optional[str] = None


class SystemSettings(BaseModel):
    data_sources: list[DataSourceSettings] = []
    backup: BackupSettings = BackupSettings()
    session_timeout_minutes: int = 60
    # Пороги контроля качества партий приёма данных (app/services/intake_quality.py)
    data_quality: DataQualitySettings = DataQualitySettings()


DEFAULT_SETTINGS = SystemSettings(
    data_sources=[
        DataSourceSettings(id="ais_gzk", name="АИС ГЗК (SmartBridge)", url="", api_key="", refresh_interval_hours=24, enabled=False),
        DataSourceSettings(id="taldau", name="БНС (Taldau)", url="https://taldau.stat.gov.kz/api", api_key="", refresh_interval_hours=168, enabled=False),
        DataSourceSettings(id="kazhydromet", name="Казгидромет", url="", api_key="", refresh_interval_hours=6, enabled=False),
        DataSourceSettings(id="kazvodkhoz", name="Казводхоз", url="", api_key="", refresh_interval_hours=24, enabled=False),
    ]
)


@router.get("", response_model=SystemSettings)
async def get_settings(
    _: User = Depends(require_admin()),
):
    redis = await get_cache()
    if redis:
        raw = await redis.get(SETTINGS_KEY)
        if raw:
            return SystemSettings.model_validate_json(raw)
    return DEFAULT_SETTINGS


@router.put("", response_model=SystemSettings)
async def update_settings(
    body: SystemSettings,
    _: User = Depends(require_admin()),
):
    redis = await get_cache()
    if redis:
        await redis.set(SETTINGS_KEY, body.model_dump_json())
    return body


def _check_source_url(url: str) -> None:
    """M-2: проверка источника не должна ходить во внутреннюю сеть (SSRF) и отправлять
    ключ API на чужой адрес по открытому каналу. Разрешены только HTTPS-адреса,
    все IP которых — публичные."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise HTTPException(status_code=422, detail="Допускаются только адреса https://")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise HTTPException(status_code=502, detail="Адрес источника не найден")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise HTTPException(status_code=422, detail="Адрес источника указывает во внутреннюю сеть")


@router.post("/test-source/{source_id}")
async def test_source(
    source_id: str,
    _: User = Depends(require_admin()),
):
    """Проверить доступность источника данных."""
    import httpx
    redis = await get_cache()
    raw = (await redis.get(SETTINGS_KEY)) if redis else None
    settings = SystemSettings.model_validate_json(raw) if raw else DEFAULT_SETTINGS
    src = next((s for s in settings.data_sources if s.id == source_id), None)
    if src is None:
        raise HTTPException(status_code=404, detail="Источник не найден")
    if not src.url:
        raise HTTPException(status_code=422, detail="URL не задан")
    _check_source_url(src.url)
    try:
        # Переадресации не выполняются: иначе проверку адреса можно обойти редиректом.
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            resp = await client.get(src.url, headers={"Authorization": f"Bearer {src.api_key}"} if src.api_key else {})
            if resp.status_code < 500:
                return {"status": "ok", "http_status": resp.status_code}
            raise HTTPException(status_code=502, detail=f"Источник вернул {resp.status_code}")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Соединение отклонено")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Таймаут соединения")


BASEBACKUP_DIR = "/basebackups"  # каталог базовых копий сервера, смонтирован только на чтение


@router.get("/backup/status")
async def backup_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin()),
):
    """Фактическое состояние резервного копирования (ФХД.5).

    Копирование выполняет сервер, а не приложение: непрерывное архивирование журналов
    транзакций PostgreSQL и ежедневная базовая копия по расписанию (02:30). Здесь —
    только чтение их состояния, без запуска команд (L-2).
    """
    import os
    from datetime import datetime, timezone

    row = (await db.execute(text(
        "SELECT archived_count, last_archived_time, failed_count, last_failed_time "
        "FROM pg_stat_archiver"
    ))).mappings().first() or {}
    mode = (await db.execute(text("SHOW archive_mode"))).scalar()

    bases = []
    if os.path.isdir(BASEBACKUP_DIR):
        for name in os.listdir(BASEBACKUP_DIR):
            path = os.path.join(BASEBACKUP_DIR, name)
            if name.startswith("base_") and os.path.isdir(path):
                bases.append(datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc))
    last_base = max(bases) if bases else None
    age_h = (datetime.now(timezone.utc) - last_base).total_seconds() / 3600 if last_base else None
    return {
        "archive_mode": mode,
        "wal_archived_count": row.get("archived_count"),
        "wal_last_archived_at": row.get("last_archived_time"),
        "wal_failed_count": row.get("failed_count"),
        "wal_last_failed_at": row.get("last_failed_time"),
        "base_backups": len(bases),
        "base_last_at": last_base,
        "base_age_hours": round(age_h, 1) if age_h is not None else None,
        # копия старше полутора суток — ночное задание не отработало
        "base_ok": age_h is not None and age_h < 36,
        "schedule": "ежедневно в 02:30 (Алматы); хранение базовых копий — 7 дней, журналов — 8 дней",
        "basebackup_dir_mounted": os.path.isdir(BASEBACKUP_DIR),
    }
