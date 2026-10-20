"""Заливка COG-файлов растровых датасетов GIS на локальный том (для titiler).

GIS API отдаёт `GET /datasets/{id}.cog` (готовый COG), но БЕЗ HTTP range, поэтому
удалённое чтение тайлером неэффективно. Скачиваем COG-файлы разово в COG_DIR, а
titiler читает их локально (overview'ы → тайлы дешёвые).

Идемпотентно: уже скачанные файлы пропускаются (по размеру не проверяем — только
наличие; для перезагрузки удалить файл). На проде GIS_SERVICE_URL резолвится через
extra_hosts (dssgis.dwh.kz → внутренний LB).

Запуск в контейнере backend:
    docker exec dss-backend python scripts/ingest_gis_cogs.py
    docker exec dss-backend python scripts/ingest_gis_cogs.py --only-small     # ≤ 50 МБ оригинала
    docker exec dss-backend python scripts/ingest_gis_cogs.py --ids <id1> <id2>
"""
from __future__ import annotations

import argparse
import os
import sys

# Позволяет запускать как `python scripts/ingest_gis_cogs.py` (добавляем /app в путь).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.config import settings

SMALL_LIMIT_BYTES = 50 * 1024 * 1024  # порог original size для --only-small


def _list_rasters(client: httpx.Client) -> list[dict]:
    rasters: list[dict] = []
    page = 1
    while True:
        r = client.get("/datasets", params={"file_type": "raster", "page": page, "page_size": 100})
        r.raise_for_status()
        body = r.json()
        rasters.extend(body.get("data", []))
        if page >= body.get("total_pages", 1):
            break
        page += 1
    return rasters


def _download_cog(client: httpx.Client, ds_id: str, dest: str) -> int:
    tmp = dest + ".part"
    total = 0
    with client.stream("GET", f"/datasets/{ds_id}.cog") as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.read()[:200]!r}")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
                total += len(chunk)
    os.replace(tmp, dest)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest GIS raster COGs to COG_DIR")
    ap.add_argument("--ids", nargs="*", help="только эти dataset id")
    ap.add_argument("--only-small", action="store_true", help="только растры ≤ 50 МБ (по original size)")
    ap.add_argument("--force", action="store_true", help="перекачать даже если файл есть")
    args = ap.parse_args()

    os.makedirs(settings.COG_DIR, exist_ok=True)
    base = settings.GIS_SERVICE_URL.rstrip("/")
    # Долгий таймаут: гиганты по 1.3 ГБ качаются минутами.
    timeout = httpx.Timeout(30.0, read=600.0)

    with httpx.Client(base_url=base, timeout=timeout) as client:
        rasters = _list_rasters(client)
        if args.ids:
            wanted = set(args.ids)
            rasters = [d for d in rasters if d["id"] in wanted]
        if args.only_small:
            rasters = [d for d in rasters if (d.get("size_bytes") or 0) <= SMALL_LIMIT_BYTES]

        print(f"Растров к обработке: {len(rasters)} (COG_DIR={settings.COG_DIR})")
        ok = skipped = failed = 0
        for d in rasters:
            ds_id = d["id"]
            name = (d.get("name") or "")[:50]
            dest = os.path.join(settings.COG_DIR, f"{ds_id}.tif")
            if os.path.exists(dest) and not args.force:
                print(f"  = skip {ds_id}  {name}")
                skipped += 1
                continue
            try:
                mb = _download_cog(client, ds_id, dest) / 1e6
                print(f"  + done {ds_id}  {mb:8.1f} МБ  {name}")
                ok += 1
            except Exception as exc:  # noqa: BLE001 — логируем и продолжаем
                print(f"  ! FAIL {ds_id}  {name}: {exc}")
                failed += 1

        print(f"Итого: скачано {ok}, пропущено {skipped}, ошибок {failed}")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
