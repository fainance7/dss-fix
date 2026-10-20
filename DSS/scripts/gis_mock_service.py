"""Локальный мок подсистемы приёма/хранения геоданных (контракт v1.0.0).

УСТАРЕЛ. Реальный сервис dssgis.dwh.kz сдан и имеет ДРУГОЙ контракт (нет /files,
/jobs, /results; импорт через POST /datasets + POST /datasets/{id}/mapping). Dev
переключён на реальный адрес (GIS_SERVICE_URL=https://dssgis.dwh.kz в .env). Этот
мок оставлен для истории/офлайна под старый контракт и фронтом больше не используется.

Назначение (историческое) — прогнать визард /dashboard/data-intake/gis целиком, пока
реальный сервис был в разработке. Состояния файлов и задач меняются во времени
(детерминированно, по затраченным секундам), чтобы фронт реально опрашивал статус.

Запуск (порт совпадает с дефолтным GIS_SERVICE_URL=http://localhost:8100):
    backend/.venv/bin/python -m uvicorn scripts.gis_mock_service:app --port 8100

Поведение для тестов:
- имя файла содержит "fail"      → валидация завершится статусом failed;
- file_type=raster или vector    → шаг выбора колонки КАТО пропускается;
- file_type=vector_with_table    → нужно зафиксировать колонку КАТО до job.
Тайминги короткие: файл становится ready через ~4 c, задача done через ~12 c.
"""

import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="GIS mock", version="1.0.0")

FILE_READY_AFTER = 4.0      # секунд до готовности файла
JOB_DURATION = 12.0         # секунд на обработку задачи

# In-memory хранилища
DATASETS: dict[str, dict] = {}
CATEGORIES: dict[str, dict] = {}
FILES: dict[str, dict] = {}
JOBS: dict[str, dict] = {}

COLUMNS_FIXTURE = [
    {"name": "KATO_CODE", "data_type": "string", "nullable": False,
     "sample_values": ["391410000", "391810000", "392010000"]},
    {"name": "area_ha", "data_type": "float", "nullable": True,
     "sample_values": [12.4, 88.1, 5.0]},
    {"name": "land_use", "data_type": "string", "nullable": True,
     "sample_values": ["пашня", "пастбище", "лес"]},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ─────────────────────────── Датасеты / категории ───────────────────────────
@app.post("/datasets", status_code=201)
async def create_dataset(body: dict):
    if not body.get("name"):
        raise HTTPException(422, "field 'name' is required")
    obj = {"id": _new_id(), "name": body["name"],
           "description": body.get("description"), "created_at": _now(), "updated_at": _now()}
    DATASETS[obj["id"]] = obj
    return obj


@app.get("/datasets")
async def list_datasets(page: int = 1, page_size: int = 50):
    items = list(DATASETS.values())
    return {"items": items, "total": len(items), "page": page, "page_size": page_size}


@app.post("/categories", status_code=201)
async def create_category(body: dict):
    if not body.get("name"):
        raise HTTPException(422, "field 'name' is required")
    obj = {"id": _new_id(), "name": body["name"], "description": body.get("description")}
    CATEGORIES[obj["id"]] = obj
    return obj


@app.get("/categories")
async def list_categories(page: int = 1, page_size: int = 50):
    items = list(CATEGORIES.values())
    return {"items": items, "total": len(items), "page": page, "page_size": page_size}


# ───────────────────────────────── Файлы ────────────────────────────────────
def _file_view(f: dict) -> dict:
    """Вычисляем актуальный статус валидации по затраченному времени."""
    elapsed = time.monotonic() - f["_created"]
    if f["_force_fail"]:
        status = "validating" if elapsed < FILE_READY_AFTER else "failed"
    elif elapsed < FILE_READY_AFTER / 2:
        status = "pending"
    elif elapsed < FILE_READY_AFTER:
        status = "validating"
    else:
        status = "ready"

    ready = status == "ready"
    return {
        "id": f["id"],
        "filename": f["filename"],
        "file_type": f["file_type"],
        "validation_status": status,
        "crs": "EPSG:4326" if ready else None,
        "feature_count": 1240 if ready and f["file_type"] != "raster" else None,
        "kato_column": f["kato_column"],
        "validation_error": "Неподдерживаемая система координат (нужны EPSG:4326 или EPSG:32642)"
        if status == "failed" else None,
    }


@app.post("/files", status_code=202)
async def upload_file(file: UploadFile = File(...), file_type: str = Form(...)):
    if file_type not in ("vector_with_table", "vector", "raster"):
        raise HTTPException(400, f"unsupported file_type '{file_type}'")
    fid = _new_id()
    FILES[fid] = {
        "id": fid,
        "filename": file.filename,
        "file_type": file_type,
        "kato_column": None,
        "_created": time.monotonic(),
        "_force_fail": "fail" in (file.filename or "").lower(),
    }
    return _file_view(FILES[fid])


@app.get("/files/{file_id}")
async def get_file(file_id: str):
    f = FILES.get(file_id)
    if not f:
        raise HTTPException(404, "file not found")
    return _file_view(f)


@app.get("/files/{file_id}/columns")
async def get_columns(file_id: str):
    f = FILES.get(file_id)
    if not f:
        raise HTTPException(404, "file not found")
    if _file_view(f)["validation_status"] != "ready":
        raise HTTPException(409, "file is not ready")
    return {"columns": COLUMNS_FIXTURE}


@app.put("/files/{file_id}/kato-column")
async def set_kato(file_id: str, body: dict):
    f = FILES.get(file_id)
    if not f:
        raise HTTPException(404, "file not found")
    col = body.get("column_name")
    if col not in {c["name"] for c in COLUMNS_FIXTURE}:
        raise HTTPException(422, f"column '{col}' not found in table")
    f["kato_column"] = col
    return _file_view(f)


# ───────────────────────────────── Задачи ───────────────────────────────────
def _job_view(j: dict) -> dict:
    if j["_cancelled"]:
        return {"id": j["id"], "status": "cancelled", "progress": j["_progress_at_cancel"], "error": None,
                "completed_at": None}
    elapsed = time.monotonic() - j["_created"]
    progress = min(100, int(elapsed / JOB_DURATION * 100))
    if progress >= 100:
        status, completed = "done", j.get("_completed_at") or _now()
        j["_completed_at"] = completed
    elif progress == 0:
        status, completed = "pending", None
    else:
        status, completed = "processing", None
    return {"id": j["id"], "status": status, "progress": progress, "error": None,
            "completed_at": completed}


@app.post("/jobs", status_code=202)
async def create_job(body: dict):
    for field in ("file_id", "dataset_id", "category_id", "reference_date"):
        if not body.get(field):
            raise HTTPException(422, f"field '{field}' is required")
    f = FILES.get(body["file_id"])
    if not f:
        raise HTTPException(404, "file not found")
    if _file_view(f)["validation_status"] != "ready":
        raise HTTPException(409, "file is not ready")
    if f["file_type"] == "vector_with_table" and not f["kato_column"]:
        raise HTTPException(409, "kato column is not set")
    jid = _new_id()
    JOBS[jid] = {
        "id": jid,
        "file_id": body["file_id"],
        "dataset_id": body["dataset_id"],
        "category_id": body["category_id"],
        "reference_date": body["reference_date"],
        "_created": time.monotonic(),
        "_cancelled": False,
        "_progress_at_cancel": 0,
    }
    return _job_view(JOBS[jid])


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return _job_view(j)


@app.delete("/jobs/{job_id}", status_code=204)
async def cancel_job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    view = _job_view(j)
    if view["status"] in ("done", "failed", "cancelled"):
        raise HTTPException(409, "job already finished")
    j["_cancelled"] = True
    j["_progress_at_cancel"] = view["progress"]
    return Response(status_code=204)


@app.get("/jobs/{job_id}/result")
async def get_result(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    if _job_view(j)["status"] != "done":
        raise HTTPException(409, "job is not done yet")
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [[[69.4, 54.8], [69.6, 54.8],
                                                              [69.6, 54.9], [69.4, 54.9], [69.4, 54.8]]]},
             "properties": {"category_id": j["category_id"], "zone": "agro-1", "kato": "391410000"}},
        ],
    }
    return JSONResponse(content=fc, media_type="application/geo+json")


@app.get("/results")
async def list_results(status: str | None = None, dataset_id: str | None = None,
                       category_id: str | None = None, reference_date_from: str | None = None,
                       reference_date_to: str | None = None, page: int = 1, page_size: int = 50):
    items = []
    for j in JOBS.values():
        v = _job_view(j)
        if status and v["status"] != status:
            continue
        if dataset_id and j["dataset_id"] != dataset_id:
            continue
        if category_id and j["category_id"] != category_id:
            continue
        items.append({
            "id": j["id"], "status": v["status"], "dataset_id": j["dataset_id"],
            "category_id": j["category_id"], "reference_date": j["reference_date"],
            "result_url": f"/jobs/{j['id']}/result" if v["status"] == "done" else None,
        })
    return {"items": items, "total": len(items), "page": page, "page_size": page_size}


@app.get("/health")
async def health():
    return {"status": "ok"}


# Ошибки в формате контракта {code, message}
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code,
                        content={"code": exc.status_code, "message": exc.detail})
