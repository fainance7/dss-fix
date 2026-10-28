"""Ландшафтное планирование: прокси к специализированным ручкам /landscape/* GIS-сервиса.

Расчёт ЛП (13 входных растров → интегральные слои → конфликты → варианты
зонирования V1/V2/V3) целиком выполняет GIS-сервис смежной команды. Этот роутер
только даёт странице DSS контракт из ТЗ «Требования к странице Ландшафтное
планирование DSS» (28.09.2026) под ролевой моделью DSS:

- чтение (состояние входов, результаты, запуски, SLD, растры на карте) — любой
  активный пользователь, включая акиматы;
- замена входного растра и запуск перерасчёта — только admin и data_uploader;
- замена возможна только в один из 13 фиксированных слотов, имя датасета
  подставляется утверждённое, тело запуска — только {} или {"from_stage": 0};
- растровые ручки (тайлы/статистика/скачивание) отдают только датасеты,
  входящие в ЛП (latest входов и результатов), а не любой датасет каталога.

COG показываются через тот же titiler, что и в каталоге ГИС (gis_raster):
GIS-сервис не поддерживает Range-запросы и CORS, поэтому растр заливается в
локальный том и режется на тайлы у нас.
"""

import re
import time
import uuid
from datetime import date
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.api.v1 import gis_raster
from app.config import settings
from app.core.rbac import require_roles
from app.deps import get_current_active_user

router = APIRouter(prefix="/landscape", tags=["landscape"])

_READ = [Depends(get_current_active_user)]
_WRITE = [Depends(require_roles("admin", "data_uploader"))]

# slot_code → утверждённое название датасета (поле name при замене). Источник —
# техприложение «Технические данные 13 входных слоев.xlsx», лист UPLOAD_ENDPOINTS_13.
SLOT_NAMES: dict[str, str] = {
    "lp-z-biodiversity": "Карта значимости биотопных функций и поддержания биоразнообразия Северо-Казахстанской области",
    "lp-z-lcr": "Карта значимости локального климатического регулирования Северо-Казахстанской области",
    "lp-z-soil-protection": "Карта почвозащитной значимости территории Северо-Казахстанской области",
    "lp-z-wr": "Карта водорегулирующей значимости территории Северо-Казахстанской области",
    "lp-u-surf-water": "Карта интегральной уязвимости поверхностных вод Северо-Казахстанской области",
    "lp-u-soil": "Карта интегральной уязвимости почв Северо-Казахстанской области",
    "lp-u-crop": "Карта интегральной уязвимости посевов Северо-Казахстанской области",
    "lp-u-veg": "Карта интегральной уязвимости естественной растительности Северо-Казахстанской области",
    "lp-p-irrigation": "Карта пригодности земель для орошаемого земледелия Северо-Казахстанской области",
    "lp-p-snow": "Карта пригодности территории для накопления снеготалых вод Северо-Казахстанской области",
    "lp-p-crop": "Карта пригодности пахотных земель для богарного земледелия Северо-Казахстанской области",
    "lp-p-pasture": "Карта пригодности пастбищ Северо-Казахстанской области",
    "lp-p-tourism": "Карта рекреационно-туристической пригодности территории Северо-Казахстанской области",
}

_TIFF_EXT = (".tif", ".tiff")
_SLD_URL_RE = re.compile(r"^/(dataset-groups|datasets)/([0-9a-fA-F-]{36})/sld$")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Клиент к GIS-сервису. Долгий write/read-таймаут — загрузка GeoTIFF в десятки МБ."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.GIS_SERVICE_URL.rstrip("/"),
            headers=settings.gis_headers,
            timeout=httpx.Timeout(settings.GIS_SERVICE_TIMEOUT, read=300.0, write=300.0),
        )
    return _client


async def _upstream(method: str, path: str, **kwargs) -> httpx.Response:
    """Запрос к GIS-сервису; ошибки апстрима нормализуются в HTTPException с detail."""
    try:
        resp = await _get_client().request(method, path, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise HTTPException(status_code=503, detail="ГИС-сервис недоступен") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка обращения к ГИС-сервису: {exc}") from exc
    if resp.status_code >= 400:
        detail = resp.text
        try:
            data = resp.json()
            detail = data.get("message") or data.get("detail") or data.get("error") or detail
        except (ValueError, AttributeError):
            pass
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp


def _passthrough(resp: httpx.Response) -> Response:
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )


# ── Множество датасетов/групп ЛП (для ограничения растровых ручек) ────────────

_ALLOWED_TTL = 60.0
_allowed: dict = {"at": 0.0, "datasets": set(), "groups": set()}


async def _refresh_allowed() -> None:
    datasets: set[str] = set()
    groups: set[str] = set()
    for path in ("/landscape/slots", "/landscape/results"):
        for item in (await _upstream("GET", path)).json() or []:
            if item.get("group_id"):
                groups.add(str(item["group_id"]))
            latest = item.get("latest") or {}
            if latest.get("id"):
                datasets.add(str(latest["id"]))
    _allowed.update(at=time.monotonic(), datasets=datasets, groups=groups)


async def _ensure_allowed(kind: Literal["datasets", "groups"], value: str) -> None:
    """403, если id не относится к ЛП. При промахе кэш обновляется один раз:
    после замены входа или перерасчёта latest.id меняется."""
    fresh = time.monotonic() - _allowed["at"] < _ALLOWED_TTL
    if fresh and value in _allowed[kind]:
        return
    await _refresh_allowed()
    if value not in _allowed[kind]:
        raise HTTPException(status_code=403, detail="Датасет не относится к ландшафтному планированию")


async def _allowed_dataset(dataset_id: uuid.UUID) -> str:
    did = str(dataset_id)
    await _ensure_allowed("datasets", did)
    return did


# ── Чтение ────────────────────────────────────────────────────────────────────


@router.get("/slots", dependencies=_READ)
async def list_slots():
    return _passthrough(await _upstream("GET", "/landscape/slots"))


@router.get("/results", dependencies=_READ)
async def list_results():
    return _passthrough(await _upstream("GET", "/landscape/results"))


@router.get("/runs", dependencies=_READ)
async def list_runs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return _passthrough(
        await _upstream("GET", "/landscape/runs", params={"page": page, "page_size": page_size})
    )


@router.get("/runs/{run_id}", dependencies=_READ)
async def get_run(run_id: uuid.UUID):
    return _passthrough(await _upstream("GET", f"/landscape/runs/{run_id}"))


@router.get("/sld", dependencies=_READ)
async def get_sld(url: str = Query(..., description="sld_url из /landscape/results")):
    m = _SLD_URL_RE.match(url)
    if not m:
        raise HTTPException(status_code=400, detail="Некорректный sld_url")
    kind, oid = m.groups()
    await _ensure_allowed("groups" if kind == "dataset-groups" else "datasets", oid.lower())
    resp = await _upstream("GET", url)
    return Response(content=resp.content, media_type="application/xml")


# ── Растры ЛП на карте (через titiler, как в каталоге ГИС) ────────────────────


@router.post("/raster/{dataset_id}/ingest", status_code=202, dependencies=_READ)
async def raster_ingest(dataset_id: uuid.UUID):
    return gis_raster.start_ingest(await _allowed_dataset(dataset_id))


@router.get("/raster/{dataset_id}/ingest/status", dependencies=_READ)
async def raster_ingest_status(dataset_id: uuid.UUID):
    return gis_raster.ingest_status(await _allowed_dataset(dataset_id))


@router.get("/raster/{dataset_id}/info", dependencies=_READ)
async def raster_info(dataset_id: uuid.UUID):
    return await gis_raster.get_info(await _allowed_dataset(dataset_id))


@router.get("/raster/{dataset_id}/statistics", dependencies=_READ)
async def raster_statistics(dataset_id: uuid.UUID):
    return await gis_raster.get_statistics(await _allowed_dataset(dataset_id))


@router.get("/raster/{dataset_id}/point", dependencies=_READ)
async def raster_point(dataset_id: uuid.UUID, lon: float = Query(...), lat: float = Query(...)):
    return await gis_raster.get_point(await _allowed_dataset(dataset_id), lon, lat)


@router.get("/raster/{dataset_id}/tiles/{z}/{x}/{y}.png", dependencies=_READ)
async def raster_tile(
    dataset_id: uuid.UUID,
    z: int,
    x: int,
    y: int,
    colormap_name: str | None = Query(None),
    colormap: str | None = Query(None, description="JSON {значение: цвет} для классов"),
    rescale: str | None = Query(None, description="min,max"),
    nodata: str | None = Query(None),
):
    return await gis_raster.get_tile(
        await _allowed_dataset(dataset_id), z, x, y,
        colormap_name=colormap_name or (None if colormap else "viridis"),
        rescale=rescale, nodata=nodata, colormap=colormap,
    )


@router.get("/raster/{dataset_id}/download", dependencies=_READ)
async def raster_download(dataset_id: uuid.UUID, filename: str | None = Query(None)):
    """Скачать залитый COG (GeoTIFF). Перед этим страница вызывает ingest."""
    did = await _allowed_dataset(dataset_id)
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", (filename or did)).strip() or did
    if not safe.lower().endswith(_TIFF_EXT):
        safe += ".tif"
    return FileResponse(gis_raster.cog_path(did), media_type="image/tiff", filename=safe)


# ── Правка: замена входа и перерасчёт ─────────────────────────────────────────


@router.post("/slots/{code}", status_code=201, dependencies=_WRITE)
async def replace_slot(
    code: str,
    file: UploadFile = File(...),
    start_date: date = Form(...),
    end_date: date = Form(...),
):
    """Заменить входной GeoTIFF слота. Имя датасета — утверждённое для слота,
    пользователь его не задаёт (ТЗ §7.1).

    Период обязателен: актуальную версию внутри группы сервер выбирает по
    end_date (при равенстве — по created_at), поэтому файл без периода может
    не стать текущим входом («Доработка … SLD, замена, перерасчёт», §2.1)."""
    if code not in SLOT_NAMES:
        raise HTTPException(status_code=404, detail="Неизвестный входной слой ЛП")
    if not (file.filename or "").lower().endswith(_TIFF_EXT):
        raise HTTPException(status_code=400, detail="Нужен файл GeoTIFF (.tif/.tiff)")
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="Дата начала периода позже даты окончания")
    data = {
        "name": SLOT_NAMES[code],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    resp = await _upstream(
        "POST",
        f"/landscape/slots/{code}",
        data=data,
        files={"file": (file.filename, file.file, file.content_type or "image/tiff")},
    )
    return _passthrough(resp)


@router.get("/datasets/{dataset_id}/status", dependencies=_WRITE)
async def dataset_status(dataset_id: uuid.UUID):
    return _passthrough(await _upstream("GET", f"/datasets/{dataset_id}/status"))


class RunCreate(BaseModel):
    """Тело запуска: {} — обычный перерасчёт, {"from_stage": 0} — полный."""

    model_config = ConfigDict(extra="forbid")
    from_stage: Literal[0] | None = None


@router.post("/runs", status_code=201, dependencies=_WRITE)
async def create_run(body: RunCreate):
    payload = {"from_stage": 0} if body.from_stage == 0 else {}
    return _passthrough(await _upstream("POST", "/landscape/runs", json=payload))
