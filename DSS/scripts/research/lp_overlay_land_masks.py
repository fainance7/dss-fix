"""Наложение вариантов функционального зонирования ЛП (V1–V3) на маски категорий земель.

Маски прислала смежная команда 01.10.2026 (партия 2026-10-02_lp): растры 30 м, EPSG:32642,
значение пикселя — номер объекта исходного шейпфайла, поэтому «покрыто маской» = не NoData.
ООПТ передана только шейпфайлом — растеризуется здесь (нужен pyshp).

Все слои приводятся к сетке растров V1–V3 (≈30,9 м, EPSG:32642) методом ближайшего соседа,
районы — растеризацией границ из frontend/public/geo/districts.geojson (связь по КАТО).

Выход (JSON):
- layers[слой][район][вариант][класс] — площадь, га (слои считаются независимо, могут пересекаться);
- exclusive[район][категория][вариант][класс] — взаимоисключающее покрытие по приоритету
  вода > населённые пункты > дороги > ООПТ > лес > сельхозугодья (2025) > прочее;
- agri_by_year[район][год] — площадь маски сельхозугодий, га.

Запуск (на проде, данные только на чтение):
  docker run --rm -v <партия>:/d:ro -v <cog volume>:/cogs:ro -v districts.geojson:/districts.geojson:ro \
    -v $PWD:/w --entrypoint python ghcr.io/developmentseed/titiler:latest /w/lp_overlay_land_masks.py
"""

import json
import os
import sys

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom
from rasterio.windows import Window

D = os.environ.get("MASKS", "/d")
COGS = os.environ.get("COGS", "/cogs")
OUT = os.environ.get("OUT", "/w/lp_overlay_land_masks.json")

VARIANTS = {
    "V1": "444297ba-0a08-401e-ade8-d7c170f7aaca",
    "V2": "19817b4a-f1b4-4b95-85b1-88f05ed570d5",
    "V3": "195dde92-0361-4224-8a63-d560f684ef7c",
}
CLASSES = list(range(0, 9))  # 0..8; -9999 = NoData варианта, считается отдельно
LAYERS = {
    "water_lakes": "Zemli_v_sostave_vodnogo_fonda_Ozera.tif",
    "water_rivers": "Zemli_v_sostave_vodnogo_fonda_Reka.tif",
    "settlements": "Zemli_gorodov_poselkov_i_sel.tif",
    "roads": "Zemli_promyshlennosti_i_inogo_naznacheniya_Avtodoroga.tif",
    "forest": "Zemli_v_sostave_lesnogo_fonda.tif",
    **{f"agri_{y}": f"Zemli_dlya_selskogo_khozyaystva_{y}.tif" for y in range(2021, 2026)},
}
# Приоритет для взаимоисключающего покрытия (первый — главный).
EXCLUSIVE = ["water", "settlements", "roads", "oopt", "forest", "agri", "other"]
BLOCK = 512


def read_shp_polygons(path: str) -> list[dict]:
    """Минимальный разбор полигонов Shapefile (типы 5/15/25) без внешних библиотек:
    все кольца записи — один Polygon (первое — внешний контур, остальные — дыры)."""
    import struct
    data = open(path, "rb").read()
    pos, out = 100, []
    while pos + 8 <= len(data):
        _, length = struct.unpack(">ii", data[pos:pos + 8])
        rec = data[pos + 8:pos + 8 + length * 2]
        pos += 8 + length * 2
        stype = struct.unpack("<i", rec[:4])[0]
        if stype not in (5, 15, 25):
            continue
        nparts, npoints = struct.unpack("<ii", rec[36:44])
        parts = list(struct.unpack(f"<{nparts}i", rec[44:44 + 4 * nparts])) + [npoints]
        base = 44 + 4 * nparts
        pts = struct.unpack(f"<{2 * npoints}d", rec[base:base + 16 * npoints])
        rings = [[(pts[2 * i], pts[2 * i + 1]) for i in range(parts[k], parts[k + 1])] for k in range(nparts)]
        out.append({"type": "Polygon", "coordinates": rings})
    return out


def read_oopt(grid) -> np.ndarray | None:
    shp = os.path.join(D, "Zemli_OOPT.shp")
    if not os.path.exists(shp):
        return None
    crs = "EPSG:4326"
    prj = shp[:-4] + ".prj"
    if os.path.exists(prj) and "UTM" in open(prj).read():
        crs = "EPSG:32642"
    shapes = [transform_geom(crs, "EPSG:32642", g) for g in read_shp_polygons(shp)]
    print(f"ООПТ: {len(shapes)} полигонов, {crs}", file=sys.stderr)
    return rasterize(((g, 1) for g in shapes), out_shape=(grid.height, grid.width),
                     transform=grid.transform, fill=0, dtype="uint8")


def main() -> None:
    v_src = {k: rasterio.open(f"{COGS}/{v}.tif") for k, v in VARIANTS.items()}
    grid = v_src["V1"]
    H, W = grid.height, grid.width
    pix_ha = abs(grid.transform.a * grid.transform.e) / 10_000

    fc = json.load(open("/districts.geojson"))
    names, shapes = [], []
    for i, f in enumerate(fc["features"], start=1):
        names.append((f["properties"]["kato"], f["properties"]["name_ru"]))
        shapes.append((transform_geom("EPSG:4326", "EPSG:32642", f["geometry"]), i))
    district = rasterize(shapes, out_shape=(H, W), transform=grid.transform, fill=0, dtype="uint8")
    nd = len(names) + 1  # 0 = вне районов

    oopt = read_oopt(grid)

    vrts = {}
    for key, fname in LAYERS.items():
        src = rasterio.open(os.path.join(D, fname))
        vrts[key] = (src, WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=W, height=H,
                                    resampling=Resampling.nearest))

    nc = len(CLASSES) + 1  # последний индекс — NoData варианта
    layer_keys = list(LAYERS) + (["oopt"] if oopt is not None else [])
    cnt_layer = {k: np.zeros((len(VARIANTS), nd, nc), np.int64) for k in layer_keys}
    cnt_excl = np.zeros((len(VARIANTS), nd, len(EXCLUSIVE), nc), np.int64)
    cnt_variant_all = np.zeros((len(VARIANTS), nd, nc), np.int64)

    def present(key: str, win: Window) -> np.ndarray:
        src, vrt = vrts[key]
        a = vrt.read(1, window=win)
        nodata = src.nodata
        if np.issubdtype(a.dtype, np.floating):
            m = np.isfinite(a)
            if nodata is not None and np.isfinite(nodata):
                m &= a != nodata
            return m
        return a != nodata if nodata is not None else np.ones_like(a, bool)

    for row in range(0, H, BLOCK):
        h = min(BLOCK, H - row)
        win = Window(0, row, W, h)
        dist = district[row:row + h].astype(np.int64)
        cls = []
        for k in VARIANTS:
            c = v_src[k].read(1, window=win).astype(np.int64)
            c = np.where((c >= 0) & (c <= 8), c, nc - 1)
            cls.append(c)
        masks = {k: present(k, win) for k in LAYERS}
        if oopt is not None:
            masks["oopt"] = oopt[row:row + h].astype(bool)

        # взаимоисключающая категория пикселя
        cat = np.full(dist.shape, EXCLUSIVE.index("other"), np.int64)
        excl_src = {
            "water": masks["water_lakes"] | masks["water_rivers"],
            "settlements": masks["settlements"],
            "roads": masks["roads"],
            "oopt": masks.get("oopt", np.zeros_like(dist, bool)),
            "forest": masks["forest"],
            "agri": masks["agri_2025"],
        }
        for name in reversed(EXCLUSIVE[:-1]):  # младший приоритет пишется первым
            cat[excl_src[name]] = EXCLUSIVE.index(name)

        for vi in range(len(VARIANTS)):
            key_vd = dist * nc + cls[vi]
            cnt_variant_all[vi] += np.bincount(key_vd.ravel(), minlength=nd * nc).reshape(nd, nc)
            for lk, m in masks.items():
                cnt_layer[lk][vi] += np.bincount(key_vd[m].ravel(), minlength=nd * nc).reshape(nd, nc)
            key_x = (dist * len(EXCLUSIVE) + cat) * nc + cls[vi]
            cnt_excl[vi] += np.bincount(key_x.ravel(), minlength=nd * len(EXCLUSIVE) * nc).reshape(
                nd, len(EXCLUSIVE), nc)
        print(f"{row + h}/{H}", end="\r", file=sys.stderr)

    cls_names = [str(c) for c in CLASSES] + ["nodata"]
    dnames = ["вне районов"] + [f"{n} ({k})" for k, n in names]

    def to_ha(arr):
        return {cls_names[i]: round(float(arr[i]) * pix_ha, 1) for i in range(nc) if arr[i]}

    out = {
        "pixel_ha": pix_ha,
        "grid": {"crs": "EPSG:32642", "width": W, "height": H, "transform": list(grid.transform)[:6]},
        "districts": dnames,
        "oopt_included": oopt is not None,
        "variant_total": {d: {v: to_ha(cnt_variant_all[vi, di]) for vi, v in enumerate(VARIANTS)}
                          for di, d in enumerate(dnames)},
        "layers": {lk: {d: {v: to_ha(cnt_layer[lk][vi, di]) for vi, v in enumerate(VARIANTS)}
                        for di, d in enumerate(dnames)} for lk in layer_keys},
        "exclusive": {d: {cat: {v: to_ha(cnt_excl[vi, di, ci]) for vi, v in enumerate(VARIANTS)}
                          for ci, cat in enumerate(EXCLUSIVE)} for di, d in enumerate(dnames)},
    }
    # Площадь маски сельхозугодий по годам: по V1 — сумма по всем классам, включая NoData варианта.
    out["agri_by_year"] = {
        d: {y: round(float(cnt_layer[f"agri_{y}"][0, di].sum()) * pix_ha, 1) for y in range(2021, 2026)}
        for di, d in enumerate(dnames)}
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"\nготово: {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
