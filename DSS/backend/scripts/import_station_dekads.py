"""Загрузка исторических декад метеостанций из файла заказчика и расчёт месяцев.

Источник: «Клим_данные_по_метеостанциям_СКО.xlsx», листы T_R_YYYY (разбор —
app/services/climate_xlsx.py). Берутся только станции справочника meteo_stations
(станции соседних областей пропускаются с записью в отчёт). Декады пишутся в
meteo_station_values со статусом approved (исторические данные заказчика), затем
пересчитываются месяцы станций и районов (app/services/climate_dekads.py).

Запуск в контейнере бэкенда (файл предварительно скопировать в контейнер):
    docker cp "Клим_данные_по_метеостанциям_СКО.xlsx" dss-backend:/tmp/stations.xlsx
    docker exec dss-backend python scripts/import_station_dekads.py --file /tmp/stations.xlsx --dry-run
    docker exec dss-backend python scripts/import_station_dekads.py --file /tmp/stations.xlsx

Повторный запуск безопасен: UPSERT по (станция, показатель, период).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.main  # noqa: E402,F401 — регистрирует все модели
from app.database import AsyncSessionLocal  # noqa: E402
from app.services import climate_dekads as cd  # noqa: E402
from app.services.climate_xlsx import norm_station, parse_workbook  # noqa: E402

KIND_TO_INDICATOR = {"T": cd.TEMPERATURE, "R": cd.PRECIPITATION}


def _years(spec: str | None) -> range | None:
    if not spec:
        return None
    a, _, b = spec.partition("-")
    return range(int(a), int(b or a) + 1)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--years", help="например 2003-2025")
    ap.add_argument("--status", default="approved", choices=["approved", "pending"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wb = load_workbook(args.file, read_only=True, data_only=True)
    cells = parse_workbook(wb, _years(args.years))

    async with AsyncSessionLocal() as db:
        stations = {norm_station(r.name): r.id for r in (await db.execute(
            text("SELECT id, name FROM meteo_stations"))).all()}
        indicators = {r.name: r.id for r in (await db.execute(
            text("SELECT id, name FROM indicators WHERE name = ANY(:n)"), {"n": list(KIND_TO_INDICATOR.values())}
        )).all()}
        missing = [n for n in KIND_TO_INDICATOR.values() if n not in indicators]
        if missing:
            print(f"Нет показателей {missing} — применена ли миграция 033?", file=sys.stderr)
            return 1
        source_id = (await db.execute(text("SELECT id FROM data_sources WHERE name = :n LIMIT 1"),
                                      {"n": cd.DEKAD_SOURCE})).scalar_one_or_none()

        skipped = Counter()
        rows, per_year = [], defaultdict(Counter)
        for c in cells:
            st = stations.get(norm_station(c.station))
            if st is None:
                skipped[c.station] += 1
                continue
            start, end = cd.dekad_bounds(c.year, c.month, c.dekad)
            rows.append({"st": st, "ind": indicators[KIND_TO_INDICATOR[c.kind]], "ps": start, "pe": end,
                         "dk": c.dekad, "v": c.value, "src": source_id, "status": args.status})
            per_year[c.year][c.kind] += 1

        print(f"Декадных значений в файле: {len(cells)}, к загрузке: {len(rows)}")
        for y in sorted(per_year):
            print(f"  {y}: температура {per_year[y]['T']}, осадки {per_year[y]['R']}")
        if skipped:
            print("Пропущены станции вне справочника: " + ", ".join(f"{k} ({v})" for k, v in sorted(skipped.items())))

        if rows:
            await db.execute(text("""
                INSERT INTO meteo_station_values
                    (id, station_id, indicator_id, period_type, period_start, period_end, dekad, value,
                     source_id, validation_status, validated_at)
                VALUES (gen_random_uuid(), :st, :ind, 'dekad', :ps, :pe, :dk, :v, :src, CAST(:status AS varchar),
                        CASE WHEN CAST(:status AS varchar) = 'approved' THEN now() END)
                ON CONFLICT ON CONSTRAINT msv_unique DO UPDATE SET
                    value = EXCLUDED.value, source_id = EXCLUDED.source_id,
                    validation_status = EXCLUDED.validation_status, validated_at = EXCLUDED.validated_at
            """), rows)
            keys = cd.month_keys_from_values((r["st"], r["ind"], r["ps"]) for r in rows)
            stats = await cd.recompute(db, keys)
            print(f"Пересчёт: {stats}")

        if args.dry_run:
            await db.rollback()
            print("dry-run: изменения откатены")
        else:
            await db.commit()
            print("Записано.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
