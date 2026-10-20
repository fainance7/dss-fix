import httpx
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router  = APIRouter()
# H-5: Airflow pass — only from env, fail-fast. No insecure defaults.
AF_URL  = os.environ.get("AIRFLOW_URL", "http://airflow-webserver:8080")
AF_USER = os.environ.get("AIRFLOW_USER", "admin")
AF_PASS = os.environ.get("AIRFLOW_ADMIN_PASSWORD")
if not AF_PASS:
    *** RuntimeError("AIRFLOW_ADMIN_PASSWORD must be set (audit H-5: no insecure default)")

class TriggerRequest(BaseModel):
    conf: dict = {}

def _trigger_dag(dag_id: str, conf: dict = {}):
    url  = f"{AF_URL}/api/v1/dags/{dag_id}/dagRuns"
    resp = httpx.post(url, json={"conf": conf},
                      auth=(AF_USER, AF_PASS), timeout=10)
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Airflow error: {resp.text}")
    return resp.json()

@router.post("/sync-metadata")
def trigger_sync_metadata():
    return _trigger_dag("dag_sync_metadata")

@router.post("/extract-weather")
def trigger_weather(body: TriggerRequest):
    return _trigger_dag("dag_extract_weather", body.conf)

@router.post("/build-excel")
def trigger_build_excel(body: TriggerRequest):
    return _trigger_dag("dag_build_excel", body.conf)
