"""Прокси растровых тайлов GIS через TiTiler.

Растровые датасеты GIS — одноканальные float COG. Их COG-файлы заливаются в
settings.COG_DIR (общий том cog_data) двумя путями: разово скриптом
scripts/ingest_gis_cogs.py (bulk-backfill) и по требованию через POST .../ingest
(после загрузки растра через UI). TiTiler читает их локально и отдаёт тайлы с
colormap+rescale. Этот роутер:
- скрывает titiler за DSS-RBAC (те же роли, что и GIS-прокси);
- подставляет локальный путь COG по id (браузер знает только dataset id);
- отдаёт statistics/info/point для легенды, авто-rescale и значения по клику;
- инджестит COG растра из GIS-сервиса в том по требованию.

Тайлы грузит сам maplibre (вне axios), поэтому фронт добавляет Authorization через
maplibre transformRequest.
"""

import asyncio
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import settings
from app.core.rbac import require_roles

router = APIRouter(
    prefix="/gis/raster",
    tags=["gis-raster"],
    dependencies=[Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader"))],
)

_client: httpx.AsyncClient | None = None
_gis_client_inst: httpx.AsyncClient | None = None
# Кэш статистики по датасету (min/max меняются редко).
_stats_cache: dict[str, dict] = {}
# Состояние инжеста COG по датасету: {status: running|done|error, downloaded, total, error}.
_ingest_state: dict[str, dict] = {}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.TITILER_URL.rstrip("/"), timeout=60.0)
    return _client


def _get_gis_client() -> httpx.AsyncClient:
    """Клиент к GIS-сервису (для стрима .cog). Долгий read-таймаут: гиганты качаются минутами."""
    global _gis_client_inst
    if _gis_client_inst is None:
        _gis_client_inst = httpx.AsyncClient(
            base_url=settings.GIS_SERVICE_URL.rstrip("/"),
            timeout=httpx.Timeout(30.0, read=600.0),
        )
    return _gis_client_inst


def _validate_id(dataset_id: str) -> None:
    if "/" in dataset_id or "\\" in dataset_id or ".." in dataset_id:
        raise HTTPException(status_code=400, detail="Некорректный id")


def _cog_file(dataset_id: str) -> str:
    """Путь COG-файла по id (без проверки наличия). Имя формируется как '{id}.tif'."""
    _validate_id(dataset_id)
    return os.path.join(settings.COG_DIR, f"{dataset_id}.tif")


def _cog_path(dataset_id: str) -> str:
    """Локальный путь COG-файла; 404 если не залит (ingest не запускали)."""
    path = _cog_file(dataset_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="COG растра не загружен")
    return path


async def _download_cog(dataset_id: str) -> None:
    """Стримит GET /datasets/{id}.cog из GIS-сервиса в COG_DIR/{id}.tif (атомарно)."""
    dest = _cog_file(dataset_id)
    tmp = dest + ".part"
    client = _get_gis_client()
    state = _ingest_state[dataset_id]
    try:
        os.makedirs(settings.COG_DIR, exist_ok=True)
        async with client.stream("GET", f"/datasets/{dataset_id}.cog") as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:200]
                raise RuntimeError(f"HTTP {resp.status_code}: {body!r}")
            state["total"] = int(resp.headers.get("content-length") or 0) or None
            with open(tmp, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    state["downloaded"] += len(chunk)
        os.replace(tmp, dest)
        _stats_cache.pop(dataset_id, None)  # инвалидировать устаревшую статистику
        state["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — фиксируем в состоянии, отдаём фронту
        state["status"] = "error"
        state["error"] = str(exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


async def _titiler_get(path: str, params: dict) -> httpx.Response:
    client = _get_client()
    try:
        return await client.get(path, params=params)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise HTTPException(status_code=503, detail="Тайл-сервис недоступен") from exc


# ── Общие операции (переиспользуются роутером ландшафтного планирования) ─────


async def get_info(dataset_id: str) -> dict:
    url = _cog_path(dataset_id)
    r = await _titiler_get("/cog/info", {"url": url})
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"titiler info: {r.text[:200]}")
    return r.json()


async def get_statistics(dataset_id: str) -> dict:
    if dataset_id in _stats_cache:
        return _stats_cache[dataset_id]
    url = _cog_path(dataset_id)
    r = await _titiler_get("/cog/statistics", {"url": url})
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"titiler statistics: {r.text[:200]}")
    data = r.json()
    _stats_cache[dataset_id] = data
    return data


async def get_point(dataset_id: str, lon: float, lat: float) -> dict:
    url = _cog_path(dataset_id)
    r = await _titiler_get(f"/cog/point/{lon},{lat}", {"url": url})
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"titiler point: {r.text[:200]}")
    return r.json()


async def get_tile(
    dataset_id: str,
    z: int,
    x: int,
    y: int,
    colormap_name: str | None = "viridis",
    rescale: str | None = None,
    nodata: str | None = None,
    colormap: str | None = None,
) -> Response:
    """PNG-тайл. colormap (JSON {значение: цвет}) — дискретная раскраска классов,
    имеет приоритет над colormap_name (titiler не принимает оба сразу)."""
    url = _cog_path(dataset_id)
    params: dict = {"url": url}
    if colormap:
        params["colormap"] = colormap
    elif colormap_name:
        params["colormap_name"] = colormap_name
    if rescale:
        params["rescale"] = rescale
    if nodata is not None:
        params["nodata"] = nodata
    r = await _titiler_get(f"/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png", params)
    if r.status_code >= 400:
        # Пустые тайлы вне области titiler отдаёт 404 — пробрасываем как есть.
        raise HTTPException(status_code=r.status_code, detail="tile error")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "image/png"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


def start_ingest(dataset_id: str, force: bool = False) -> dict:
    """Идемпотентно запускает фоновую заливку COG; возвращает текущее состояние."""
    dest = _cog_file(dataset_id)
    if os.path.exists(dest) and not force:
        _ingest_state[dataset_id] = {"status": "done", "downloaded": 0, "total": None, "error": None}
        return {"status": "done"}
    cur = _ingest_state.get(dataset_id)
    if cur and cur.get("status") == "running":
        return {"status": "running", **cur}
    _ingest_state[dataset_id] = {"status": "running", "downloaded": 0, "total": None, "error": None}
    asyncio.create_task(_download_cog(dataset_id))
    return {"status": "running"}


def ingest_status(dataset_id: str) -> dict:
    """Состояние инжеста COG. Если задачи не было — выводим из наличия файла."""
    state = _ingest_state.get(dataset_id)
    if state is not None:
        return state
    exists = os.path.exists(_cog_file(dataset_id))
    return {
        "status": "done" if exists else "absent",
        "downloaded": 0,
        "total": None,
        "error": None,
    }


def cog_path(dataset_id: str) -> str:
    """Локальный путь залитого COG (404, если ingest не запускали)."""
    return _cog_path(dataset_id)


# ── Эндпоинты ────────────────────────────────────────────────────────────────


@router.get("/{dataset_id}/info")
async def raster_info(dataset_id: str):
    return await get_info(dataset_id)


@router.get("/{dataset_id}/statistics")
async def raster_statistics(dataset_id: str):
    return await get_statistics(dataset_id)


@router.get("/{dataset_id}/point")
async def raster_point(
    dataset_id: str,
    lon: float = Query(...),
    lat: float = Query(...),
):
    return await get_point(dataset_id, lon, lat)


@router.get("/{dataset_id}/tiles/{z}/{x}/{y}.png")
async def raster_tile(
    dataset_id: str,
    z: int,
    x: int,
    y: int,
    colormap_name: str = Query("viridis"),
    rescale: str | None = Query(None, description="min,max"),
    nodata: str | None = Query(None),
):
    return await get_tile(dataset_id, z, x, y, colormap_name=colormap_name, rescale=rescale, nodata=nodata)


@router.post("/{dataset_id}/ingest", status_code=202)
async def raster_ingest(dataset_id: str, force: bool = Query(False)):
    """Забрать COG растра из GIS-сервиса в том titiler (по требованию, после UI-загрузки).

    Идемпотентно: если файл уже есть и не force — сразу done. Иначе запускает фоновую
    задачу стрима .cog; прогресс опрашивается через GET .../ingest/status.
    """
    return start_ingest(dataset_id, force)


@router.get("/{dataset_id}/ingest/status")
async def raster_ingest_status(dataset_id: str):
    return ingest_status(dataset_id)
