import logging
import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    dashboard_summaries,
    forecasts,
    geo_context,
    integration,
    landscape_impact,
    models,
    scenarios,
    tasks,
)
from app.api.v1.integration import verify_internal_key
from app.services.scheduler import setup_scheduler

# CR-1: ai-service публичен (vhost dssai) — все прикладные роутеры требуют X-AI-Key.
# Легитимный путь идёт через backend-прокси (он инжектит ключ); прямой доступ
# из интернета без ключа получает 403.
_INTERNAL = [Depends(verify_internal_key)]
_IS_PROD = os.getenv("ENVIRONMENT", "development") == "production"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Ошибки модуля — в журнал страницы мониторинга (ФFE.7).
from app.config import settings as _settings  # noqa: E402
from app.services.error_log import install as _install_error_log  # noqa: E402

_install_error_log(_settings.REDIS_URL, "ai-service")

app = FastAPI(
    title="DSS AI Service",
    description="AI-микросервис системы поддержки принятия решений — прогнозирование и сценарный анализ",
    version="1.0.0",
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    openapi_url=None if _IS_PROD else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    # localhost-origins разрешены только вне production (повторный аудит, M-1).
    allow_origins=[
        "https://dss.dwh.kz",
        "https://dssapi.dwh.kz",
    ] + ([] if _IS_PROD else [
        "http://localhost:3000",
        "http://localhost:3010",
        "http://localhost:8000",
    ]),
    allow_credentials=True,
    # M-1: explicit method + header lists instead of wildcards. Wildcards with
    # allow_credentials=True can be abused if an origin is misconfigured; an
    # explicit list is the OWASP-recommended pattern.
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-AI-Key", "X-Request-ID"],
)

app.include_router(forecasts.router, prefix="/api/v1", dependencies=_INTERNAL)
app.include_router(scenarios.router, prefix="/api/v1", dependencies=_INTERNAL)
app.include_router(models.router, prefix="/api/v1", dependencies=_INTERNAL)
app.include_router(tasks.router, prefix="/api/v1", dependencies=_INTERNAL)
app.include_router(integration.router, prefix="/api/v1")  # уже проверяет X-AI-Key внутри
app.include_router(geo_context.router, prefix="/api/v1", dependencies=_INTERNAL)
app.include_router(landscape_impact.router, prefix="/api/v1", dependencies=_INTERNAL)
app.include_router(dashboard_summaries.router, prefix="/api/v1", dependencies=_INTERNAL)

# Планировщик фоновых задач
setup_scheduler(app)


@app.on_event("startup")
async def _reset_orphaned_narratives() -> None:
    """Сброс нарративов, зависших в 'running' после прошлого рестарта."""
    from app.api.v1.scenarios import reset_orphaned_narratives
    try:
        await reset_orphaned_narratives()
    except Exception as e:  # noqa: BLE001 — старт сервиса не должен падать из-за этого
        logger.warning("Не удалось сбросить зависшие нарративы: %s", e)


@app.on_event("startup")
async def _release_orphaned_summaries() -> None:
    """Выводы дашбордов, зависшие в 'running' после прошлого рестарта."""
    try:
        await dashboard_summaries.release_orphaned_summaries()
    except Exception as e:  # noqa: BLE001 — старт сервиса не должен падать из-за этого
        logger.warning("Не удалось освободить зависшие выводы дашбордов: %s", e)


@app.get("/health", tags=["system"])
async def health():
    """Проверка работоспособности сервиса (включая локальный LLM)."""
    from app.services.llm_client import check_health as llm_check
    llm = await llm_check()
    return {
        "status": "ok",
        "service": "ai",
        "llm": {
            "ready": llm.ready,
            "base_url": llm.base_url,
            "model": llm.model,
            "available_models": llm.available_models,
            "error": llm.error,
        },
    }
