"""Sub-app /intake-portal — отдельный swagger для смежной команды.

Подключает существующие роутеры (read-only справочники + data_intake)
плюс новый JSON-batch роутер. Авторизация — по X-API-Key, через подмену
``get_current_active_user`` в ``dependency_overrides``.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.v1 import (
    data_intake,
    directions,
    indicators,
    intake_batch,
    kato,
    meteo_stations,
    regions,
    units,
)
from app.config import settings
from app.core.api_key_auth import get_user_by_api_key
from app.deps import get_current_active_user

intake_portal = FastAPI(
    title="DSS Intake Portal",
    description=(
        "API для смежной команды загрузки исторических данных.\n\n"
        "**Авторизация:** HTTP-header `X-API-Key: <ваш-ключ>`.\n\n"
        "Получение ключа — у администратора DSS."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

intake_portal.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (
    directions.router,
    indicators.router,
    units.router,
    regions.router,
    kato.router,
    meteo_stations.router,  # справочник станций для декадных климатических данных (station_id)
    data_intake.router,
    intake_batch.router,
):
    intake_portal.include_router(r, prefix="/api/v1")

# Подмена JWT-авторизации на X-API-Key.
# Работает на всех endpoint-ах подключённых роутеров, потому что
# ``get_current_active_user`` всюду используется через ``Depends(...)``.
# ``require_roles(...)`` тоже опирается на ``get_current_active_user``,
# поэтому RBAC остаётся валидным.
intake_portal.dependency_overrides[get_current_active_user] = get_user_by_api_key


def _custom_openapi():
    """Заставляет swagger sub-app использовать securityScheme X-API-Key."""
    if intake_portal.openapi_schema:
        return intake_portal.openapi_schema
    schema = get_openapi(
        title=intake_portal.title,
        version=intake_portal.version,
        description=intake_portal.description,
        routes=intake_portal.routes,
    )
    schema["servers"] = [{"url": "/intake-portal"}]
    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }
    for path in schema["paths"].values():
        for op in path.values():
            if isinstance(op, dict):
                op["security"] = [{"ApiKeyAuth": []}]
    intake_portal.openapi_schema = schema
    return schema


intake_portal.openapi = _custom_openapi
