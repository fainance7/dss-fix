# app/routers/dss.py
"""
Отправка готовых DSS-файлов в Intake Portal (dssapi.dwh.kz).

Портал принимает партию в два шага: сначала загрузка файла (создаётся
черновик со статусом pending и отчётом валидации), затем отдельное
подтверждение (commit), после которого строки попадают в базу DSS.

Здесь реализован ТОЛЬКО первый шаг. Подтверждения нет намеренно: партия,
загруженная через этот роутер, физически не может попасть в DSS без
действий человека в самом портале. Причина — цена ошибки: файл содержит
десятки тысяч строк, и неверная партия в производственной системе
исправляется сложнее, чем повторная загрузка.

DSS_DRY_RUN=true — режим проверки интерфейса: запрос к порталу не
выполняется вообще, возвращается заглушка. Нужен потому, что даже
загрузка черновика оставляет запись в истории портала, а удалять записи
роль data_uploader не может (проверено: DELETE отдаёт 403 с требованием
роли admin/exp_ver).
"""
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()

ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "/app/artifacts"))
INTAKE_URL = os.environ.get("DSS_INTAKE_URL", "").rstrip("/")
INTAKE_KEY = os.environ.get("DSS_INTAKE_KEY", "")
DRY_RUN = os.environ.get("DSS_DRY_RUN", "true").lower() in ("1", "true", "yes")

UPLOAD_TIMEOUT = 180  # файлы под мегабайт, валидация на стороне портала не мгновенная


def _api(path: str) -> str:
    return f"{INTAKE_URL}/api/v1/data-intake{path}"


@router.get("/status")
def dss_status():
    """Готовность отправки: настройки, режим, доступность портала."""
    ready = bool(INTAKE_URL and INTAKE_KEY)
    info = {
        "configured": ready,
        "dry_run": DRY_RUN,
        "url": INTAKE_URL or None,
        "key_present": bool(INTAKE_KEY),
    }
    if not ready or DRY_RUN:
        return info

    try:
        r = httpx.get(_api("/jobs"), headers={"X-API-Key": INTAKE_KEY}, timeout=15)
        info["portal_http"] = r.status_code
        info["portal_ok"] = r.status_code == 200
    except Exception as e:
        info["portal_ok"] = False
        info["error"] = str(e)[:200]
    return info


@router.post("/check/{filename}")
def dss_check(filename: str):
    """
    Загружает готовый файл в Intake Portal и возвращает отчёт валидации.

    Партия остаётся черновиком (pending). Импорт не выполняется.
    """
    # Защита от выхода за пределы каталога артефактов: имя приходит из
    # браузера, и без проверки "../../etc/passwd" ушло бы во внешнюю систему.
    safe_name = Path(filename).name
    filepath = ARTIFACTS_DIR / safe_name
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(404, f"Файл не найден: {safe_name}")

    size_kb = round(filepath.stat().st_size / 1024, 1)

    if DRY_RUN:
        return {
            "dry_run": True,
            "filename": safe_name,
            "size_kb": size_kb,
            "message": ("Сухой прогон: запрос в DSS НЕ отправлен. "
                        "Чтобы включить реальную отправку, задайте "
                        "DSS_DRY_RUN=false в .env и пересоздайте контейнер."),
        }

    if not (INTAKE_URL and INTAKE_KEY):
        raise HTTPException(500, "DSS_INTAKE_URL или DSS_INTAKE_KEY не заданы в .env")

    try:
        with open(filepath, "rb") as fh:
            resp = httpx.post(
                _api("/excel"),
                headers={"X-API-Key": INTAKE_KEY},
                files={"file": (safe_name, fh,
                                "application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet")},
                timeout=UPLOAD_TIMEOUT,
            )
    except Exception as e:
        raise HTTPException(502, f"Не удалось связаться с DSS: {str(e)[:200]}")

    if resp.status_code != 200:
        raise HTTPException(
            502, f"DSS ответил HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    job = data.get("job", data)
    summary = job.get("validation_summary") or {}

    return {
        "dry_run": False,
        "filename": safe_name,
        "size_kb": size_kb,
        "job_id": job.get("id"),
        "status": job.get("status"),
        "rows_total": job.get("rows_total"),
        "rows_ok": job.get("rows_ok"),
        "rows_invalid": job.get("rows_invalid"),
        "rows_imported": job.get("rows_imported"),
        "top_errors": summary.get("top_errors", [])[:15],
        "note": ("Партия создана как ЧЕРНОВИК. В базу DSS данные не попали — "
                 "для импорта нужно подтверждение в самом портале."),
    }
