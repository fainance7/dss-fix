"""Карты из каталога геоданных на дашбордах направлений.

Дашборды видят все роли, включая акиматы, а общий прокси /gis/* и растровые
ручки каталога открыты только экспертам и загрузчикам. Поэтому для дашбордов
сделан отдельный роутер только на чтение и только для утверждённого перечня
слоёв (LAYERS): браузер обращается к слою по ключу, а не по id набора, и не может
получить произвольный набор каталога.

Набор ищется в каталоге по точному названию. Если набор перезагрузили (новый
UUID, та же группа и название), берётся самый поздний: по end_date, затем по
created_at — так же, как /dataset-groups/{id}/latest в самом каталоге.

Скорость:
- перечень наборов каталога кэшируется на 10 минут;
- GeoJSON векторного слоя один раз скачивается, упрощается (shapely, допуск
  задан в LAYERS), координаты округляются до ~1 м, служебные атрибуты площади и
  длины отбрасываются; результат хранится в памяти и на диске до изменения набора
  (ключ кэша — id набора и его updated_at), ответ сжимается GZip-middleware;
- растры показываются тайлами через titiler, как в каталоге и ЛП (gis_raster):
  COG заливается в том один раз, повторный показ мгновенный.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

import httpx
import shapely
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from shapely.geometry import mapping, shape

from app.api.v1 import gis_raster
from app.config import settings
from app.deps import get_current_active_user

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dashboard-geo",
    tags=["dashboard-geo"],
    dependencies=[Depends(get_current_active_user)],
)


@dataclass(frozen=True)
class Layer:
    title: str
    kind: str  # "vector" | "raster"
    dataset_name: str
    description: str = ""
    # vector
    simplify: float = 0.0  # допуск упрощения в градусах (0 — не упрощать)
    popup: tuple[str, ...] = ()  # атрибуты для подсказки по щелчку
    category_prop: str | None = None  # раскраска по значениям атрибута, если в каталоге нет SLD
    # raster
    colormap_name: str = "viridis"  # непрерывная шкала, если в каталоге нет SLD
    ramp: tuple[str, ...] = ()  # цвета классов для целочисленного растра без SLD
    class_labels: tuple[str, ...] = ()  # подписи классов 1..N, если их смысл известен
    unit: str = ""
    extra: dict = field(default_factory=dict)


_LP = "Северо-Казахстанской области"

LAYERS: dict[str, Layer] = {
    # ── Векторные слои ──────────────────────────────────────────────────────
    "settlements": Layer(
        "Населённые пункты", "vector", "Населенные пункты",
        "Контуры сельских и городских населённых пунктов области",
        simplify=0.0001, popup=("name", "adm2"),
    ),
    "animals-rare": Layer(
        "Редкие и охраняемые виды животных", "vector", "Животные",
        "Места встреч: пеликан, журавль-красавка, стрепет, беркут, орлан-белохвост и др.",
        popup=("Наиме",), category_prop="Наиме",
    ),
    "animals-game": Layer(
        "Охотничье-промысловые и прочие виды животных", "vector", "Животные занесенные в красную книгу",
        "Места встреч: тетерев, марал, лисица, волк, кабан и др.",
        popup=("Наиме",), category_prop="Наиме",
    ),
    "roads": Layer("Автомобильные дороги", "vector", "Автодороги", simplify=0.0002),
    "lakes": Layer(
        "Озёра", "vector", "Озера 2", "Крупные озёра области",
        simplify=0.0003, popup=("Name",),
    ),
    "rivers": Layer("Реки", "vector", "Реки", simplify=0.0003, popup=("name", "categoria")),
    "salt-marshes": Layer("Солончаки", "vector", "Солончаки", simplify=0.0002, popup=("adm2",)),
    "meteo-stations": Layer("Метеостанции", "vector", "Метеостанции", popup=("name",)),
    "agro-zoning": Layer(
        "Агроклиматические зоны", "vector",
        "Агроклиматическое зонирование по тепло- и влагообеспеченности (2003–2024 гг.)",
        "Зоны по тепло- и влагообеспеченности, средние условия 2003–2024 гг.",
        simplify=0.0005, popup=("Agroclimat",),
    ),
    "moisture-zones": Layer(
        "Зоны влагообеспеченности", "vector",
        "Влагообеспеченность вегетационного периода (2003–2024 гг.)",
        "Средние условия 2003–2024 гг.", simplify=0.0005, popup=("usloviya",),
    ),
    "drought-zones": Layer(
        "Зоны засушливости (ГТК)", "vector",
        "Засушливость вегетационного периода на основе ГТК (2003–2024 гг.)",
        "Средние условия 2003–2024 гг.", simplify=0.0005, popup=("usloviya",),
    ),
    "precip-veg-zones": Layer(
        "Осадки вегетационного периода", "vector",
        "Сумма осадков за вегетационный период (2003–2024 гг.)",
        "Средние условия 2003–2024 гг.", simplify=0.0005, popup=("osad_veg",),
    ),
    "precip-cold-zones": Layer(
        "Осадки холодного периода", "vector",
        "Сумма осадков за холодный период года (2003–2024 гг.)",
        "Средние условия 2003–2024 гг.", simplify=0.0005, popup=("osad_xol",),
    ),
    # ── Растры: экология ────────────────────────────────────────────────────
    "biodiversity": Layer(
        "Значимость для биоразнообразия", "raster",
        f"Карта значимости биотопных функций и поддержания биоразнообразия {_LP}",
        "Входной слой ландшафтного планирования",
    ),
    "oopt": Layer(
        "Особо охраняемые природные территории", "raster",
        f"Реклассифицированная карта ООПТ (Карта значимости биотопных функций и поддержания биоразнообразия {_LP})",
        "Исходный слой для оценки биоразнообразия",
        ramp=("#e5f5e0", "#a1d99b", "#41ab5d", "#006d2c", "#00441b"),
        class_labels=("очень низкая значимость", "низкая", "средняя", "высокая", "очень высокая значимость"),
    ),
    "air-no2": Layer("Диоксид азота (NO₂)", "raster", f"Карта концентрации диоксида азота (NO₂) в {_LP}", "Спутниковые данные", colormap_name="ylorrd", unit="моль/м²"),
    "air-so2": Layer("Диоксид серы (SO₂)", "raster", f"Карта концентрации диоксида серы (SO₂) в {_LP}", "Спутниковые данные", colormap_name="ylorrd", unit="моль/м²"),
    "air-co": Layer("Оксид углерода (CO)", "raster", f"Карта концентрации оксида углерода (CO) в {_LP}", "Спутниковые данные", colormap_name="ylorrd", unit="моль/м²"),
    "air-o3": Layer("Озон (O₃)", "raster", f"Карта концентрации озона (O₃) в {_LP}", "Спутниковые данные", colormap_name="ylorrd", unit="моль/м²"),
    "air-ch4": Layer("Метан (CH₄)", "raster", f"Карта концентрации метана (CH₄) в {_LP}", "Спутниковые данные", colormap_name="ylorrd", unit="млрд⁻¹ (ppb)"),
    "air-aer": Layer("Аэрозольный индекс", "raster", f"Карта аэрозольного индекса (AER_AI) в {_LP}", "Спутниковые данные", colormap_name="ylorrd", unit="безразмерный индекс"),
    # ── Растры: земельные ресурсы ───────────────────────────────────────────
    "soil-salinity": Layer("Засолённость почв", "raster", f"Карта засоленности почв {_LP}"),
    "slope-arable": Layer("Крутизна склонов (пашня)", "raster", "Карта крутизны склонов для оценки пригодности пахотных земель"),
    "slope-pasture": Layer("Крутизна склонов (пастбища)", "raster", "Карта крутизны склонов для пастбищного использования земель"),
    "ls-factor": Layer("LS-фактор (эрозионный рельеф)", "raster", f"Карта распределения LS-фактора {_LP}", colormap_name="ylorbr"),
    "ndvi-arable": Layer("Продуктивность пашни (NDVI, 2014–2024)", "raster",
                         "Карта классы продуктивности пахотных земель по значениям NDVI (2014-2024 гг.)", colormap_name="rdylgn",
                         ramp=("#d73027", "#fc8d59", "#fee08b", "#91cf60", "#1a9850")),
    "ndvi-pasture": Layer("Продуктивность пастбищ (NDVI, 2014–2024)", "raster",
                          "Карта продуктивность пастбищ под естественной растительностью на основе NDVI (2014-2024 гг.)",
                          colormap_name="rdylgn", ramp=("#d73027", "#fc8d59", "#fee08b", "#91cf60", "#1a9850")),
    # ── Растры: пригодность территории (входы ландшафтного планирования) ────
    "suit-crop": Layer("Богарное земледелие", "raster", f"Карта пригодности пахотных земель для богарного земледелия {_LP}"),
    "suit-irrigation": Layer("Орошаемое земледелие", "raster", f"Карта пригодности земель для орошаемого земледелия {_LP}"),
    "suit-pasture": Layer("Пастбища", "raster", f"Карта пригодности пастбищ {_LP}"),
    "suit-snow": Layer("Накопление снеготалых вод", "raster", f"Карта пригодности территории для накопления снеготалых вод {_LP}"),
    "suit-tourism": Layer("Рекреация и туризм", "raster", f"Карта рекреационно-туристической пригодности территории {_LP}"),
}

# Служебные атрибуты геометрии из ArcGIS — на карте не нужны, а весят много.
_DROP_PROPS = {"shape_area", "shape_leng", "shape_le_1", "objectid"}

# ── Клиент и перечень наборов каталога ──────────────────────────────────────

_client: httpx.AsyncClient | None = None
_CATALOG_TTL = 600.0
_catalog: dict = {"at": 0.0, "by_name": {}}
_catalog_lock = asyncio.Lock()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.GIS_SERVICE_URL.rstrip("/"),
            headers=settings.gis_headers,
            timeout=httpx.Timeout(settings.GIS_SERVICE_TIMEOUT, read=120.0),
        )
    return _client


async def _upstream_get(path: str, **kwargs) -> httpx.Response:
    try:
        resp = await _get_client().get(path, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise HTTPException(status_code=503, detail="Каталог геоданных недоступен") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка обращения к каталогу геоданных: {exc}") from exc
    return resp


def _sort_key(d: dict) -> tuple[str, str]:
    return (d.get("end_date") or "", d.get("created_at") or "")


async def _load_catalog() -> dict[str, dict]:
    by_name: dict[str, dict] = {}
    page = 1
    while True:
        resp = await _upstream_get("/datasets", params={"page": page, "page_size": 100, "status": "ready"})
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail="Каталог геоданных не отдал перечень наборов")
        body = resp.json()
        for d in body.get("data") or []:
            name = (d.get("name") or "").strip()
            cur = by_name.get(name)
            if cur is None or _sort_key(d) > _sort_key(cur):
                by_name[name] = d
        total_pages = body.get("total_pages") or 1
        if page >= total_pages or page >= 50:
            break
        page += 1
    return by_name


async def _catalog_by_name() -> dict[str, dict]:
    if time.monotonic() - _catalog["at"] < _CATALOG_TTL and _catalog["by_name"]:
        return _catalog["by_name"]
    async with _catalog_lock:
        if time.monotonic() - _catalog["at"] < _CATALOG_TTL and _catalog["by_name"]:
            return _catalog["by_name"]
        try:
            _catalog["by_name"] = await _load_catalog()
            _catalog["at"] = time.monotonic()
        except HTTPException:
            # Каталог недоступен — продолжаем на последнем удачном перечне, если он есть.
            if not _catalog["by_name"]:
                raise
            log.warning("каталог геоданных недоступен, используется кэш перечня")
    return _catalog["by_name"]


def _layer(key: str) -> Layer:
    layer = LAYERS.get(key)
    if layer is None:
        raise HTTPException(status_code=404, detail="Неизвестный слой")
    return layer


async def _resolve(key: str) -> tuple[Layer, dict]:
    layer = _layer(key)
    ds = (await _catalog_by_name()).get(layer.dataset_name)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Набор «{layer.dataset_name}» не найден в каталоге")
    if ds.get("file_type", "").startswith("vector") != (layer.kind == "vector"):
        raise HTTPException(status_code=409, detail="Тип набора в каталоге не совпадает с ожидаемым")
    return layer, ds


async def _raster_id(key: str) -> str:
    layer, ds = await _resolve(key)
    if layer.kind != "raster":
        raise HTTPException(status_code=400, detail="Слой не растровый")
    return str(ds["id"])


# ── Описание слоёв ──────────────────────────────────────────────────────────

_sld_cache: dict[str, tuple[float, str | None]] = {}

# UTF-8, однажды прочитанный как cp1251 и снова сохранённый в UTF-8 («Код» → «РљРѕРґ»).
# Такие фрагменты встречаются в SLD каталога; восстанавливаем каждый по отдельности
# и только если обратное преобразование даёт корректный UTF-8.
_MOJIBAKE = re.compile(r"(?:[РС][\u0400-\u045f\u0490\u0491\u2013-\u203a\u00a0-\u00bf]){2,}")


_SLD_TEXT = re.compile(r"(Literal>|Title>)([^<]*)(<)")


def _squash(value: str) -> str:
    """Схлопывает пробелы и переносы строк: в атрибутах и в SLD одна и та же
    категория бывает записана с переносом в разных местах."""
    return " ".join(value.split())


def _normalize_sld(text: str) -> str:
    return _SLD_TEXT.sub(lambda m: m.group(1) + _squash(m.group(2)) + m.group(3), text)


def _fix_mojibake(text: str) -> str:
    def repl(m: re.Match) -> str:
        try:
            return m.group(0).encode("cp1251").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return m.group(0)

    return _MOJIBAKE.sub(repl, text)
_SLD_TTL = 3600.0


async def _sld_text(dataset_id: str) -> str | None:
    hit = _sld_cache.get(dataset_id)
    if hit and time.monotonic() - hit[0] < _SLD_TTL:
        return hit[1]
    resp = await _upstream_get(f"/datasets/{dataset_id}/sld")
    text = None
    if resp.status_code == 200 and resp.content.strip():
        # Кодировку не угадываем: каталог отдаёт SLD в UTF-8 без charset в заголовке,
        # а автоопределение httpx на коротких русских текстах путает её с cp1251.
        try:
            text = resp.content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = resp.content.decode("cp1251", errors="replace")
        text = _normalize_sld(_fix_mojibake(text))
    _sld_cache[dataset_id] = (time.monotonic(), text)
    return text


@router.get("/layers")
async def list_layers(keys: str = Query(..., description="ключи слоёв через запятую")):
    """Описание запрошенных слоёв. Недоступный слой возвращается с полем error,
    чтобы остальные слои блока всё равно показались."""
    out = []
    for key in [k.strip() for k in keys.split(",") if k.strip()][:20]:
        layer = _layer(key)
        item = {
            "key": key,
            "title": layer.title,
            "kind": layer.kind,
            "description": layer.description,
            "popup": list(layer.popup),
            "category_prop": layer.category_prop,
            "colormap_name": layer.colormap_name,
            "ramp": list(layer.ramp),
            "class_labels": list(layer.class_labels),
            "unit": layer.unit,
        }
        try:
            _, ds = await _resolve(key)
            sld = await _sld_text(str(ds["id"]))
            item.update(
                dataset_id=str(ds["id"]),
                dataset_name=ds.get("name"),
                start_date=ds.get("start_date"),
                end_date=ds.get("end_date"),
                has_sld=sld is not None,
                size_bytes=ds.get("size_bytes"),
            )
        except HTTPException as exc:
            item["error"] = exc.detail
        out.append(item)
    return out


@router.get("/layers/{key}/sld")
async def layer_sld(key: str):
    _, ds = await _resolve(key)
    text = await _sld_text(str(ds["id"]))
    if text is None:
        raise HTTPException(status_code=404, detail="Стиль для слоя не задан")
    return Response(content=text, media_type="application/xml", headers={"Cache-Control": "private, max-age=3600"})


# ── Векторные слои: подготовка GeoJSON ──────────────────────────────────────

_geo_mem: dict[str, bytes] = {}
_geo_locks: dict[str, asyncio.Lock] = {}
_CACHE_VERSION = "v2"


def _cache_dir() -> str:
    return os.path.join(settings.COG_DIR, "dashboard-geo-cache")


def _cache_name(key: str, ds: dict) -> str:
    stamp = (ds.get("updated_at") or "").replace(":", "").replace("-", "").replace(".", "")
    return f"{_CACHE_VERSION}-{key}-{ds['id']}-{stamp}.geojson"


def _prepare(raw: bytes, tolerance: float) -> bytes:
    """Упрощение геометрии, округление координат, отбрасывание служебных атрибутов."""
    src = json.loads(raw)
    features = []
    for f in src.get("features") or []:
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            g = shape(geom)
            if tolerance > 0 and g.geom_type != "Point" and g.geom_type != "MultiPoint":
                g = g.simplify(tolerance, preserve_topology=True)
            g = shapely.set_precision(g, 0.00001)
        except Exception:  # noqa: BLE001 — битую геометрию пропускаем, не ломая слой
            continue
        if g.is_empty:
            continue
        props = {
            k: (_squash(v) if isinstance(v, str) else v)
            for k, v in (f.get("properties") or {}).items()
            if k.lower() not in _DROP_PROPS
        }
        features.append({"type": "Feature", "geometry": mapping(g), "properties": props})
    out = {"type": "FeatureCollection", "features": features}
    return json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def _vector_bytes(key: str) -> bytes:
    layer, ds = await _resolve(key)
    if layer.kind != "vector":
        raise HTTPException(status_code=400, detail="Слой не векторный")
    name = _cache_name(key, ds)
    if name in _geo_mem:
        return _geo_mem[name]
    lock = _geo_locks.setdefault(name, asyncio.Lock())
    async with lock:
        if name in _geo_mem:
            return _geo_mem[name]
        path = os.path.join(_cache_dir(), name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            resp = await _upstream_get(f"/datasets/{ds['id']}.geojson")
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail="Каталог геоданных не отдал геометрию слоя")
            data = await run_in_threadpool(_prepare, resp.content, layer.simplify)
            try:
                os.makedirs(_cache_dir(), exist_ok=True)
                # прежние версии этого слоя (в т. ч. в старом формате кэша) больше не нужны
                stale = re.compile(rf"^v\d+-{re.escape(key)}-[0-9a-f]{{8}}-")
                for old in os.listdir(_cache_dir()):
                    if stale.match(old) and old != name:
                        os.remove(os.path.join(_cache_dir(), old))
                tmp = path + ".part"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            except OSError:
                log.warning("не удалось сохранить кэш слоя %s на диск", key)
        for old in [k for k in _geo_mem if k.startswith(f"{_CACHE_VERSION}-{key}-")]:
            _geo_mem.pop(old, None)
        _geo_mem[name] = data
        return data


@router.get("/layers/{key}/geojson")
async def layer_geojson(key: str):
    data = await _vector_bytes(key)
    return Response(
        content=data,
        media_type="application/geo+json",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# ── Растровые слои: через titiler (как в каталоге и ЛП) ─────────────────────


@router.post("/layers/{key}/ingest", status_code=202)
async def raster_ingest(key: str):
    return gis_raster.start_ingest(await _raster_id(key))


@router.get("/layers/{key}/ingest/status")
async def raster_ingest_status(key: str):
    return gis_raster.ingest_status(await _raster_id(key))


@router.get("/layers/{key}/statistics")
async def raster_statistics(key: str):
    return await gis_raster.get_statistics(await _raster_id(key))


@router.get("/layers/{key}/point")
async def raster_point(key: str, lon: float = Query(...), lat: float = Query(...)):
    return await gis_raster.get_point(await _raster_id(key), lon, lat)


# Прозрачный тайл 1×1 для областей вне растра: titiler отвечает на них 404, из-за
# чего браузер пишет ошибки в консоль и запрашивает тайл повторно.
_EMPTY_TILE = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=")


@router.get("/layers/{key}/tiles/{z}/{x}/{y}.png")
async def raster_tile(
    key: str,
    z: int,
    x: int,
    y: int,
    colormap_name: str | None = Query(None),
    colormap: str | None = Query(None, description="JSON {значение: цвет} для классов"),
    rescale: str | None = Query(None, description="min,max"),
):
    dataset_id = await _raster_id(key)
    try:
        return await gis_raster.get_tile(
            dataset_id, z, x, y,
            colormap_name=colormap_name or (None if colormap else "viridis"),
            rescale=rescale, colormap=colormap,
        )
    except HTTPException as exc:
        if exc.status_code != 404 or exc.detail == "COG растра не загружен":
            raise
        return Response(content=_EMPTY_TILE, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
