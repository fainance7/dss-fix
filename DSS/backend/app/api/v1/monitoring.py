import time
from datetime import datetime
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rbac import require_admin, require_roles
from app.core.cache import cache_get, cache_set, get_cache
from app.database import get_db
from app.models.data import DataAnnotation, DataCategory, DataLayer, DataSource, SpatialData
from app.models.user import User
from app.schemas.data import SHDMetrics

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

CACHE_KEY = 'monitoring:sdh'
CACHE_TTL = 60  # 60 seconds


# ── Системный мониторинг (ФFE.7): статус служб, размер БД, история прогнозов ──

class ServiceStatus(BaseModel):
    name: str
    status: str            # 'up' | 'down'
    latency_ms: Optional[float] = None
    detail: Optional[str] = None


class SystemMetrics(BaseModel):
    services: list[ServiceStatus]
    db_size_bytes: int
    db_size_pretty: str
    spatial_table_bytes: int


class ForecastRun(BaseModel):
    id: UUID
    indicator_id: Optional[UUID]
    region_id: Optional[UUID]
    forecast_date: Optional[str]
    status: str
    created_at: Optional[datetime]


async def _ping_db(db: AsyncSession) -> ServiceStatus:
    t0 = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        return ServiceStatus(name="PostgreSQL", status="up", latency_ms=round((time.perf_counter() - t0) * 1000, 1))
    except Exception as e:
        return ServiceStatus(name="PostgreSQL", status="down", detail=str(e)[:200])


async def _ping_redis() -> ServiceStatus:
    t0 = time.perf_counter()
    r = await get_cache()
    if r is None:
        return ServiceStatus(name="Redis", status="down", detail="unavailable")
    try:
        await r.ping()
        return ServiceStatus(name="Redis", status="up", latency_ms=round((time.perf_counter() - t0) * 1000, 1))
    except Exception as e:
        return ServiceStatus(name="Redis", status="down", detail=str(e)[:200])


async def _ping_http(name: str, url: str) -> ServiceStatus:
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        latency = round((time.perf_counter() - t0) * 1000, 1)
        if resp.status_code < 500:
            return ServiceStatus(name=name, status="up", latency_ms=latency)
        return ServiceStatus(name=name, status="down", latency_ms=latency, detail=f"HTTP {resp.status_code}")
    except Exception as e:
        return ServiceStatus(name=name, status="down", detail=str(e)[:200])


@router.get("/system", response_model=SystemMetrics)
async def system_metrics(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin()),
):
    """Статус служб + размер БД. Только ADM (ТЗ ФFE.7)."""
    services = [
        ServiceStatus(name="BackEnd API", status="up"),
        await _ping_db(db),
        await _ping_redis(),
        await _ping_http("AI-сервис", f"{settings.AI_SERVICE_URL}/health"),
        await _ping_http("GIS-сервис", f"{settings.GIS_SERVICE_URL}/health"),
        await _ping_http("TiTiler (растры)", f"{settings.TITILER_URL}/healthz"),
    ]
    db_size = (await db.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
    db_pretty = (await db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))).scalar_one()
    try:
        spatial_bytes = (await db.execute(text("SELECT pg_total_relation_size('spatial_data')"))).scalar_one()
    except Exception:
        spatial_bytes = 0
    return SystemMetrics(
        services=services,
        db_size_bytes=int(db_size),
        db_size_pretty=str(db_pretty),
        spatial_table_bytes=int(spatial_bytes),
    )


@router.get("/forecasts", response_model=list[ForecastRun])
async def forecast_history(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin()),
):
    """История последних запусков прогнозов (таблица forecasts из ai-service). Только ADM."""
    limit = max(1, min(limit, 100))
    rows = (await db.execute(text(
        """
        SELECT id, indicator_id, region_id, forecast_date, status, created_at
        FROM forecasts
        ORDER BY created_at DESC
        LIMIT :limit
        """
    ), {"limit": limit})).mappings().all()
    return [
        ForecastRun(
            id=r["id"],
            indicator_id=r["indicator_id"],
            region_id=r["region_id"],
            forecast_date=str(r["forecast_date"]) if r["forecast_date"] else None,
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.get("/sdh", response_model=SHDMetrics)
async def sdh_metrics(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin", "exp_dir", "exp_ver")),
):
    """Метрики Системы Хранения Данных."""
    cached = await cache_get(CACHE_KEY)
    if cached is not None:
        return SHDMetrics(**cached)

    async def count(model, *conditions):
        q = select(func.count()).select_from(model)
        for c in conditions:
            q = q.where(c)
        return (await db.execute(q)).scalar_one()

    metrics = SHDMetrics(
        total_sources=await count(DataSource),
        active_sources=await count(DataSource, DataSource.is_active.is_(True)),
        total_categories=await count(DataCategory),
        total_layers=await count(DataLayer),
        active_layers=await count(DataLayer, DataLayer.status == "active"),
        total_spatial_records=await count(SpatialData),
        pending_validation=await count(SpatialData, SpatialData.validation_status == "pending"),
        approved_records=await count(SpatialData, SpatialData.validation_status == "approved"),
        rejected_records=await count(SpatialData, SpatialData.validation_status == "rejected"),
        total_annotations=await count(DataAnnotation),
    )
    await cache_set(CACHE_KEY, metrics.model_dump(), CACHE_TTL)
    return metrics


# ── Ресурсы сервера и журнал ошибок модулей (ФFE.7) ──────────────────────────

# Запросы к Prometheus сервера. Метрики узла — node-exporter, контейнеров —
# docker-stats-exporter (метка name = имя контейнера).
_PROMQL = {
    "cpu": '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100',
    "memory": "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100",
    "disk": '(1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100',
}
_CONTAINER_CPU = 'dockerstats_cpu_usage_ratio{name=~"dss-.*"}'
_CONTAINER_MEM = 'dockerstats_memory_usage_bytes{name=~"dss-.*"}'


async def _prom(client: httpx.AsyncClient, path: str, params: dict) -> list:
    r = await client.get(f"{settings.PROMETHEUS_URL.rstrip('/')}/api/v1/{path}", params=params)
    r.raise_for_status()
    return r.json()["data"]["result"]


@router.get("/resources")
async def resources(
    hours: int = 24,
    _: User = Depends(require_admin()),
):
    """Загрузка ЦП, памяти и диска сервера, нагрузка контейнеров Системы и графики
    за последние `hours` часов (из Prometheus сервера). Только ADM."""
    if not settings.PROMETHEUS_URL:
        return {"available": False, "detail": "Сервер метрик не подключён"}
    hours = max(1, min(hours, 24 * 30))
    end = time.time()
    step = max(60, hours * 3600 // 120)  # ~120 точек на график
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            current = {}
            series = {}
            for key, q in _PROMQL.items():
                res = await _prom(client, "query", {"query": q})
                current[key] = round(float(res[0]["value"][1]), 1) if res else None
                rng = await _prom(client, "query_range",
                                  {"query": q, "start": end - hours * 3600, "end": end, "step": step})
                series[key] = [[int(float(t)), round(float(v), 1)] for t, v in (rng[0]["values"] if rng else [])]
            cpu = {x["metric"].get("name"): float(x["value"][1]) for x in await _prom(client, "query", {"query": _CONTAINER_CPU})}
            mem = {x["metric"].get("name"): float(x["value"][1]) for x in await _prom(client, "query", {"query": _CONTAINER_MEM})}
    except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
        return {"available": False, "detail": f"Сервер метрик недоступен: {type(exc).__name__}"}
    containers = sorted(
        ({"name": n, "cpu_percent": round(cpu.get(n, 0.0) * 100, 1),
          "memory_mb": round(mem.get(n, 0.0) / 2**20)} for n in set(cpu) | set(mem)),
        key=lambda c: c["name"],
    )
    return {"available": True, "hours": hours, "current": current, "series": series, "containers": containers}


@router.get("/errors")
async def error_log(
    module: str = "backend",
    limit: int = 100,
    _: User = Depends(require_admin()),
):
    """Последние ошибки модуля (backend — BackEnd и СХД, ai-service — Модуль ИИ). Только ADM."""
    import json

    if module not in ("backend", "ai-service"):
        return []
    redis = await get_cache()
    if redis is None:
        return []
    raw = await redis.lrange(f"errlog:{module}", 0, max(1, min(limit, 500)) - 1)
    out = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except ValueError:
            continue
    return out
