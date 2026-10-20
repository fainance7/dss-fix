"""Пересечение вариантов ЛП (V1–V3) с входными и интегральными слоями — для
многокритериального сравнения вариантов (scripts/research/lp_compare_build.py).

Все растры ЛП лежат на одной сетке (30,92 м, EPSG:32642, 22219 × 14977), поэтому
пересечение считается попиксельно по полному разрешению, без перепроецирования.
Обзоры COG не используются: у растров классов они построены кубической интерполяцией.

Выход (JSON): cross[слой][район][вариант]["<класс варианта>-<класс слоя>"] = га.

Запуск на проде (данные только на чтение):
  docker run --rm -v dss_cog_data:/c:ro -v /srv/dss/backend/app/data/districts.geojson:/districts.geojson:ro \
    -v $PWD:/w --entrypoint python ghcr.io/developmentseed/titiler:2.0.5 /w/lp_compare_crosstab.py
"""

import json
import os
import sys

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom
from rasterio.windows import Window, transform as window_transform

COGS = os.environ.get("COGS", "/c")
DISTRICTS = os.environ.get("DISTRICTS", "/districts.geojson")
OUT = os.environ.get("OUT", "/w/lp_compare_crosstab.json")
BLOCK = 256

VARIANTS = {
    "V1": "444297ba-0a08-401e-ade8-d7c170f7aaca",
    "V2": "19817b4a-f1b4-4b95-85b1-88f05ed570d5",
    "V3": "195dde92-0361-4224-8a63-d560f684ef7c",
}
# Слои: код → (dataset_id, подпись)
LAYERS = {
    "lp-z-integrated": ("1d9a80c4", "Интегральная природная значимость"),
    "lp-u-integrated": ("9f543c29", "Интегральная уязвимость"),
    "lp-p-crop": ("f64e0620", "Пригодность для богарного земледелия"),
}
NC = 10  # классы 0..9; NoData и прочее отбрасываются


def full_id(prefix: str) -> str:
    hits = [f for f in os.listdir(COGS) if f.startswith(prefix) and f.endswith(".tif")]
    if len(hits) != 1:
        raise SystemExit(f"COG {prefix}: найдено {len(hits)}")
    return hits[0][:-4]


def main() -> int:
    features = json.load(open(DISTRICTS, encoding="utf-8"))["features"]
    layer_ids = {code: full_id(p) for code, (p, _) in LAYERS.items()}
    srcs = {k: rasterio.open(os.path.join(COGS, f"{v}.tif")) for k, v in {**VARIANTS, **layer_ids}.items()}
    ref = srcs["V1"]
    for k, s in srcs.items():
        if (s.width, s.height, s.transform, s.crs) != (ref.width, ref.height, ref.transform, ref.crs):
            raise SystemExit(f"{k}: сетка отличается от V1")
    pixel_ha = abs(ref.transform.a * ref.transform.e) / 10_000
    shapes = [(transform_geom("EPSG:4326", ref.crs, f["geometry"]), i + 1) for i, f in enumerate(features)]
    nd = len(features) + 1
    # counts[layer][variant] — массив (район, класс варианта, класс слоя)
    counts = {l: {v: np.zeros((nd, NC, NC), np.int64) for v in VARIANTS} for l in LAYERS}
    vtotal = {v: np.zeros((nd, NC), np.int64) for v in VARIANTS}

    for row in range(0, ref.height, BLOCK):
        win = Window(0, row, ref.width, min(BLOCK, ref.height - row))
        dist = rasterize(shapes, out_shape=(win.height, win.width), transform=window_transform(win, ref.transform),
                         fill=0, dtype="uint8").astype(np.int64)
        lay = {l: srcs[l].read(1, window=win).astype(np.int64) for l in LAYERS}
        for v in VARIANTS:
            vc = srcs[v].read(1, window=win).astype(np.int64)
            okv = (vc >= 0) & (vc < NC) & (dist > 0)
            vtotal[v] += np.bincount(dist[okv] * NC + vc[okv], minlength=nd * NC).reshape(nd, NC)
            for l, lc in lay.items():
                ok = okv & (lc >= 0) & (lc < NC)
                idx = (dist[ok] * NC + vc[ok]) * NC + lc[ok]
                counts[l][v] += np.bincount(idx, minlength=nd * NC * NC).reshape(nd, NC, NC)
        if row % (BLOCK * 8) == 0:
            print(f"строка {row}/{ref.height}", file=sys.stderr)

    names = [f"{f['properties'].get('name')} ({f['properties'].get('kato')})" for f in features]

    def ha(x: int) -> float:
        return round(x * pixel_ha, 1)

    cross = {}
    for l in LAYERS:
        cross[l] = {}
        for i, name in enumerate(names):
            cross[l][name] = {}
            for v in VARIANTS:
                arr = counts[l][v][i + 1]
                cross[l][name][v] = {f"{a}-{b}": ha(arr[a, b]) for a in range(NC) for b in range(NC) if arr[a, b]}
    variant_total = {
        name: {v: {str(c): ha(vtotal[v][i + 1][c]) for c in range(NC) if vtotal[v][i + 1][c]} for v in VARIANTS}
        for i, name in enumerate(names)
    }
    out = {
        "pixel_ha": pixel_ha,
        "variants": VARIANTS,
        "layers": {k: {"dataset_id": layer_ids[k], "label": lbl} for k, (_, lbl) in LAYERS.items()},
        "districts": names,
        "variant_total": variant_total,
        "cross": cross,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print(f"записано: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
