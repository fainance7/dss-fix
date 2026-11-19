from fastapi import FastAPI, HTTPException, Request, Security, Depends
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import os
import secrets
import urllib.parse
from routers import files, triggers, dss, agromap, legal_entity
from routers.upload import router as upload_router
from routers.upload_parser import router as upload_parser_router
from routers.dss_login import router as dss_login_router
from auth_dss import require_dss_session, NotAuthenticated
from security import BodySizeLimitMiddleware, SecurityHeadersMiddleware, log_event

API_KEY        = os.environ.get("API_SECRET_KEY")
if not API_KEY:
    raise RuntimeError("API_SECRET_KEY environment variable must be set to a non-empty value")

api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(request: Request, key: str = Security(api_key_header)):
    # У-14: сравнение за постоянное время
    if not key or not secrets.compare_digest(key.encode(), API_KEY.encode()):
        log_event("api_key_denied", request)
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return key

# Документация API (/docs, /redoc, /openapi.json) в рабочем режиме не публикуется (ПТ-02).
_DOCS = os.environ.get("ETL_ENABLE_DOCS", "false").strip().lower() == "true"
app = FastAPI(title="aldispetro.kz ETL API", version="1.0.0",
              docs_url="/docs" if _DOCS else None, redoc_url="/redoc" if _DOCS else None,
              openapi_url="/openapi.json" if _DOCS else None)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request, exc: NotAuthenticated):
    next_q = urllib.parse.quote(exc.next_path or "/upload/")
    return RedirectResponse(url=f"/upload/login?next={next_q}", status_code=303)

# Р-13: служебные маршруты интерфейса загрузки — только с действующей сессией DSS
# (раньше были доступны без аутентификации).
_dss_session = [Depends(require_dss_session)]
app.include_router(files.router,    prefix="/files",   tags=["Files"], dependencies=_dss_session)
app.include_router(dss.router,      prefix="/dss",     tags=["DSS Intake"], dependencies=_dss_session)
app.include_router(agromap.router,  prefix="/agromap", tags=["Agromap"], dependencies=_dss_session)
app.include_router(legal_entity.router, prefix="/legal", tags=["Legal Entity"], dependencies=_dss_session)
app.include_router(triggers.router, prefix="/trigger", tags=["Triggers"],
                   dependencies=[Depends(verify_api_key)])
app.include_router(dss_login_router)
app.include_router(upload_router, dependencies=[Depends(require_dss_session)])
app.include_router(upload_parser_router, prefix="/upload", tags=["Upload Parser"],
                   dependencies=[Depends(verify_api_key)])

@app.get("/health")
def health():
    return {"status": "ok"}
