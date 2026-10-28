"""Одноразовый бэкфилл: перенос локальных описаний карт в dssgis.

Ранее описания гео-датасетов хранились в локальной таблице `gis_dataset_meta`
(override), т.к. внешний dssgis не давал их редактировать. Теперь dssgis
поддерживает `PATCH /datasets/{id}` — переносим описания в источник правды и после
этого таблицу можно дропнуть (migration 028).

Запуск (внутри backend-контейнера, где доступны app.* и GIS_SERVICE_URL):
    PYTHONPATH=/app python scripts/migrate_gis_dataset_meta_to_dssgis.py

Идемпотентен: повторный прогон просто снова выставит те же описания. Не удаляет
строки — удаление таблицы делает отдельная SQL-миграция после проверки.
"""
import asyncio

import httpx
from sqlalchemy import text

from app.config import settings
from app.database import AsyncSessionLocal


async def main() -> None:
    base = settings.GIS_SERVICE_URL.rstrip("/")
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT gis_dataset_id, description FROM gis_dataset_meta"
        ))).fetchall()

    if not rows:
        print("gis_dataset_meta пуста — переносить нечего.")
        return

    print(f"К переносу: {len(rows)} строк. GIS={base}")
    ok, failed = 0, 0
    async with httpx.AsyncClient(base_url=base, timeout=settings.GIS_SERVICE_TIMEOUT,
                                 headers=settings.gis_headers) as client:
        for gis_dataset_id, description in rows:
            try:
                resp = await client.patch(
                    f"/datasets/{gis_dataset_id}", json={"description": description}
                )
                if resp.status_code != 200:
                    print(f"  FAIL {gis_dataset_id}: PATCH -> {resp.status_code} {resp.text[:200]}")
                    failed += 1
                    continue
                check = await client.get(f"/datasets/{gis_dataset_id}")
                got = check.json().get("description") if check.status_code == 200 else "<no-get>"
                match = "OK" if got == description else f"MISMATCH (got={got!r})"
                print(f"  {match} {gis_dataset_id}: description set")
                ok += 1 if match == "OK" else 0
                failed += 0 if match == "OK" else 1
            except httpx.HTTPError as exc:
                print(f"  ERROR {gis_dataset_id}: {exc}")
                failed += 1

    print(f"Готово: успешно={ok}, ошибок={failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
