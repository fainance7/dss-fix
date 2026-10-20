from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import os
import urllib.parse
from routers import files, triggers, dss, agromap, legal_entity
from routers.upload import router as upload_router
from routers.upload_parser import router as upload_parser_router
from routers.dss_login import router as dss_login_router
from auth_dss import require_dss_session, NotAuthenticated

API_KEY        = os.environ.get("API_SECRET_KEY")
if not API_KEY:
    raise RuntimeError("API_SECRET_KEY environment variable must be set to a non-empty value")

api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(key: str = Security(api_key_header)):
    if not key or key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return key

app = FastAPI(title="aldispetro.kz ETL API", version="1.0.0")

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request, exc: NotAuthenticated):
    next_q = urllib.parse.quote(exc.next_path or "/upload/")
    return RedirectResponse(url=f"/upload/login?next={next_q}", status_code=303)

app.include_router(files.router,    prefix="/files",   tags=["Files"])
app.include_router(dss.router,      prefix="/dss",     tags=["DSS Intake"])
app.include_router(agromap.router,  prefix="/agromap", tags=["Agromap"])
app.include_router(legal_entity.router, prefix="/legal", tags=["Legal Entity"])
app.include_router(triggers.router, prefix="/trigger", tags=["Triggers"],
                   dependencies=[Depends(verify_api_key)])
app.include_router(dss_login_router)
app.include_router(upload_router, dependencies=[Depends(require_dss_session)])
app.include_router(upload_parser_router, prefix="/upload", tags=["Upload Parser"],
                   dependencies=[Depends(verify_api_key)])

@app.get("/health")
def health():
    return {"status": "ok"}
