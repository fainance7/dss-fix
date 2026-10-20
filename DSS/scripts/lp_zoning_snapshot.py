"""Снимки зонирования вариантов ЛП (V1–V3) по полному разрешению растра.

Доли функциональных зон по области и 14 районам для привязки вариантов к сценариям
(backend/app/api/v1/landscape_link.py читает их из кэша `zoning-<версия>-<id>.json`).

Почему отдельный скрипт, а не titiler: обзоры COG вариантов построены кубической
интерполяцией и дают несуществующие классы, поэтому считать надо по полному
разрешению, а этого не выдерживает прод-titiler (один воркер, лимит памяти 1 ГБ —
воркер падает, вместе с ним пропадают тайлы карт). Здесь растр читается блоками
по 512 строк, память — десятки мегабайт.

Запуск на проде (cron раз в 30 минут, ничего не делает, если снимки уже есть):
  docker run --rm --network dss_dss-net \
    -v dss_cog_data:/data/cogs -v /srv/dss/backend/app/data/districts.geojson:/districts.geojson:ro \
    -v /srv/dss/scripts/lp_zoning_snapshot.py:/job.py:ro \
    -e GIS_URL=http://172.17.0.1:8080 --entrypoint python ghcr.io/developmentseed/titiler:2.0.5 /job.py
"""

import json
import os
import re
import sys
import urllib.request

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom
from rasterio.windows import Window, transform as window_transform

COG_DIR = os.environ.get("COG_DIR", "/data/cogs")
GIS_URL = os.environ.get("GIS_URL", "http://172.17.0.1:8080").rstrip("/")
DISTRICTS = os.environ.get("DISTRICTS", "/districts.geojson")
VERSION = os.environ.get("ZONING_VERSION", "fullres1")  # = landscape_link._ZONING_VERSION
VARIANTS = {
    "lp-v1-conservative": "V1 — природоохранный",
    "lp-v2-balanced": "V2 — сбалансированный",
    "lp-v3-development": "V3 — хозяйственно-ориентированный",
}
BLOCK = 512


def get(path: str) -> bytes:
    with urllib.request.urlopen(GIS_URL + path, timeout=60) as r:
        return r.read()


def parse_legend(xml: str) -> list[dict]:
    out = []
    for entry in re.findall(r"<(?:\w+:)?ColorMapEntry\b[^>]*>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', entry))
        try:
            value = int(float(attrs["quantity"]))
        except (KeyError, ValueError):
            continue
        out.append({"value": value, "label": attrs.get("label") or str(value), "color": attrs.get("color")})
    return out


def zoning(path: str, legend: list[dict], features: list[dict]) -> tuple[dict, list[dict]]:
    classes = sorted(e["value"] for e in legend)
    nc = max(classes) + 1
    with rasterio.open(path) as src:
        shapes = [(transform_geom("EPSG:4326", src.crs, f["geometry"]), i + 1) for i, f in enumerate(features)]
        nd = len(features) + 1
        counts = np.zeros((nd, nc), np.int64)
        for row in range(0, src.height, BLOCK):
            win = Window(0, row, src.width, min(BLOCK, src.height - row))
            cls = src.read(1, window=win).astype(np.int64)
            dist = rasterize(shapes, out_shape=cls.shape, transform=window_transform(win, src.transform),
                             fill=0, dtype="uint8").astype(np.int64)
            ok = (cls >= 0) & (cls < nc) & (dist > 0)
            counts += np.bincount(dist[ok] * nc + cls[ok], minlength=nd * nc).reshape(nd, nc)

    def pct(row: np.ndarray) -> dict:
        s = sum(int(row[c]) for c in classes)
        return {str(c): round(int(row[c]) / s * 100, 1) for c in classes if row[c]} if s else {}

    districts = [{"kato": f["properties"].get("kato"), "name": f["properties"].get("name"), "pct": pct(counts[i + 1])}
                 for i, f in enumerate(features)]
    return pct(counts[1:].sum(axis=0)), districts


def main() -> int:
    features = json.load(open(DISTRICTS, encoding="utf-8"))["features"]
    results = json.loads(get("/landscape/results"))
    done = 0
    for item in results:
        group = item.get("group")
        latest = item.get("latest") or {}
        if group not in VARIANTS or latest.get("status") != "ready":
            continue
        did = str(latest["id"])
        out = os.path.join(COG_DIR, f"zoning-{VERSION}-{did}.json")
        cog = os.path.join(COG_DIR, f"{did}.tif")
        if os.path.exists(out):
            continue
        if not os.path.exists(cog):
            print(f"{group}: COG {did} ещё не залит в том — пропуск до следующего запуска", file=sys.stderr)
            continue
        legend = parse_legend(get(item["sld_url"]).decode("utf-8")) if item.get("sld_url") else []
        if not legend:
            print(f"{group}: у варианта нет легенды классов — пропуск", file=sys.stderr)
            continue
        oblast, districts = zoning(cog, legend, features)
        snapshot = {
            "variant": group, "label": VARIANTS[group], "dataset_id": did,
            "result_updated_at": latest.get("updated_at"), "legend": legend,
            "oblast_pct": oblast, "districts": districts, "calc_version": VERSION,
            "method": ("Доли пикселей классов варианта внутри официальных границ районов по полному "
                       "разрешению растра; вне классов легенды и NoData не учитываются."),
        }
        tmp = out + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False)
        os.replace(tmp, out)
        print(f"{group}: снимок {did} — {oblast}")
        done += 1
    print(f"новых снимков: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
