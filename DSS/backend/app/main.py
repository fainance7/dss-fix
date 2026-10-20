from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1 import auth, users, data_sources, data_categories, data_layers, spatial_data, monitoring, regions, shapefiles, kato, directions, units, economic_sectors, indicators, indicator_values, indicator_norms, data_intake, intake_mappings, remote_tables, api_keys, gis, gis_links, gis_raster, landscape, landscape_link, dashboard_geo, ai, meteo_stations, annotations, audit_log, climate
from app.api.v1 import settings as settings_api
from app.config import settings
from app.core.error_log import install as install_error_log
from app.intake_portal import intake_portal

# Ошибки модуля — в журнал страницы мониторинга (ФFE.7).
install_error_log(settings.REDIS_URL, 'backend')

# Интерактивная документация API открыта только вне прода (M-5: разведка API).
_IS_PROD = settings.ENVIRONMENT == 'production'

app = FastAPI(
    title='DSS API',
    description='Decision Support System — REST API',
    version='1.0.0',
    docs_url=None if _IS_PROD else '/docs',
    redoc_url=None if _IS_PROD else '/redoc',
    openapi_url=None if _IS_PROD else '/openapi.json',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Сжатие ответов API (на проде Caddy/uvicorn отдают несжато). Большие JSON/GeoJSON
# (границы районов ~124 КБ → ~36 КБ, списки значений ~5–10×) — главная причина
# медленной загрузки данных дашбордов. minimum_size — не жать мелочь.
app.add_middleware(GZipMiddleware, minimum_size=500)


# Заголовки безопасности ответов API (аудит безопасности, L-4). HSTS — только в
# проде: браузер запомнит, что к домену можно обращаться лишь по HTTPS.
_SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Content-Security-Policy': "frame-ancestors 'none'",
}
if _IS_PROD:
    _SECURITY_HEADERS['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'


@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response

app.include_router(auth.router, prefix='/api/v1')
app.include_router(users.router, prefix='/api/v1')
app.include_router(data_sources.router, prefix='/api/v1')
app.include_router(data_categories.router, prefix='/api/v1')
app.include_router(data_layers.router, prefix='/api/v1')
app.include_router(spatial_data.router, prefix='/api/v1')
app.include_router(monitoring.router, prefix='/api/v1')
app.include_router(regions.router, prefix='/api/v1')
app.include_router(shapefiles.router, prefix='/api/v1')
app.include_router(kato.router, prefix='/api/v1')
app.include_router(directions.router, prefix='/api/v1')
app.include_router(units.router, prefix='/api/v1')
app.include_router(economic_sectors.router, prefix='/api/v1')
app.include_router(meteo_stations.router, prefix='/api/v1')
app.include_router(indicators.router, prefix='/api/v1')
app.include_router(indicator_values.router, prefix='/api/v1')
app.include_router(indicator_norms.router, prefix='/api/v1')
app.include_router(data_intake.router, prefix='/api/v1')
app.include_router(intake_mappings.router, prefix='/api/v1')
app.include_router(remote_tables.router, prefix='/api/v1')
app.include_router(api_keys.router, prefix='/api/v1')
# gis_raster ДО gis: у gis.py есть catch-all /gis/{path:path}, который иначе
# перехватывал бы /gis/raster/* и форвардил в GIS-сервис (404). Порядок важен.
app.include_router(gis_raster.router, prefix='/api/v1')
app.include_router(gis_links.router, prefix='/api/v1')
app.include_router(gis.router, prefix='/api/v1')
app.include_router(landscape.router, prefix='/api/v1')
app.include_router(landscape_link.router, prefix='/api/v1')
app.include_router(dashboard_geo.router, prefix='/api/v1')
app.include_router(climate.router, prefix='/api/v1')
app.include_router(ai.router, prefix='/api/v1')
app.include_router(annotations.router, prefix='/api/v1')
app.include_router(audit_log.router, prefix='/api/v1')
app.include_router(settings_api.router, prefix='/api/v1')

# Sub-app для смежной команды: отдельный swagger + авторизация по X-API-Key.
app.mount('/intake-portal', intake_portal)


@app.get('/health')
async def health():
    return {'status': 'ok'}
