"""Перенос районных декад осадков из месячного ряда «Сумма месячных осадков» в архив.

Казгидрометовские декады по районам когда-то загружены с period_type='month'
(начало 11-го и 21-го числа; 1-я декада совпадала по ключу с месячной строкой
и почти везде затёрта). Они искажают месячный ряд, поэтому:

1. Сверка: каждая районная декада сравнивается со средним станционных декад
   района (meteo_station_values, загружены import_station_dekads.py). Отчёт —
   доля совпадений в пределах 0,5 мм.
2. 1-я декада восстанавливается как месяц − d2 − d3 ТОЛЬКО если месячная строка
   того же района — Казгидромет (2003–2004), есть d2 и d3 и результат ≥ 0;
   помечается reconstructed (расчётное). Для лет, где месяц — Open-Meteo,
   1-я декада не восстанавливается (источники нельзя смешивать).
3. Все районные декады переносятся в legacy_district_precip_dekads и удаляются
   из indicator_values. Месячные строки (с 1-го по последнее число) остаются.
4. Итоговая проверка: в ряду не осталось месячных строк не с 1-го числа.

Запуск в контейнере бэкенда:
    docker exec dss-backend python scripts/migrate_legacy_precip_dekads.py --dry-run
    docker exec dss-backend python scripts/migrate_legacy_precip_dekads.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

import app.main  # noqa: E402,F401
from app.database import AsyncSessionLocal  # noqa: E402
from app.services import climate_dekads as cd  # noqa: E402

LEGACY_INDICATOR = "Сумма месячных осадков"
TOLERANCE_MM = 0.5

_LEGACY_SQL = """
WITH ind AS (SELECT id FROM indicators WHERE name = :legacy),
leg AS (
    SELECT iv.id, iv.region_id, iv.period_start, iv.period_end, iv.value, iv.source_id,
           CASE extract(day FROM iv.period_start) WHEN 1 THEN 1 WHEN 11 THEN 2 ELSE 3 END AS dekad
    FROM indicator_values iv JOIN ind ON ind.id = iv.indicator_id
    WHERE iv.period_type = 'month'
      AND (extract(day FROM iv.period_start) IN (11, 21)
           OR (extract(day FROM iv.period_start) = 1 AND iv.period_end = iv.period_start + 9))
)
SELECT leg.*,
       (SELECT avg(v.value) FROM meteo_station_values v
          JOIN climate_region_station_map m ON m.station_id = v.station_id AND m.region_id = leg.region_id
                                            AND m.mode = 'own'
          JOIN indicators pi ON pi.id = v.indicator_id AND pi.name = :precip
         WHERE v.period_type = 'dekad' AND v.period_start = leg.period_start) AS stations_mean
FROM leg
"""

# 1-я декада = месяц − d2 − d3, только для месяцев-Казгидромет
_RECONSTRUCT_SQL = """
WITH ind AS (SELECT id FROM indicators WHERE name = :legacy),
src AS (SELECT id FROM data_sources WHERE name = 'РГП «Казгидромет»'),
mon AS (
    SELECT iv.region_id, iv.period_start, iv.value, iv.source_id
    FROM indicator_values iv JOIN ind ON ind.id = iv.indicator_id
    WHERE iv.period_type = 'month' AND extract(day FROM iv.period_start) = 1
      AND iv.period_end = (date_trunc('month', iv.period_start) + interval '1 month - 1 day')::date
      AND iv.source_id IN (SELECT id FROM src)
),
dk AS (
    SELECT iv.region_id, date_trunc('month', iv.period_start)::date AS m,
           max(iv.value) FILTER (WHERE extract(day FROM iv.period_start) = 11) AS d2,
           max(iv.value) FILTER (WHERE extract(day FROM iv.period_start) = 21) AS d3
    FROM indicator_values iv JOIN ind ON ind.id = iv.indicator_id
    WHERE iv.period_type = 'month' AND extract(day FROM iv.period_start) IN (11, 21)
    GROUP BY 1, 2
)
SELECT mon.region_id, mon.period_start, mon.value - dk.d2 - dk.d3 AS d1, mon.source_id
FROM mon JOIN dk ON dk.region_id = mon.region_id AND dk.m = mon.period_start
WHERE dk.d2 IS NOT NULL AND dk.d3 IS NOT NULL
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        params = {"legacy": LEGACY_INDICATOR, "precip": cd.PRECIPITATION}
        legacy = (await db.execute(text(_LEGACY_SQL), params)).mappings().all()
        print(f"Районных декад в месячном ряду: {len(legacy)}")
        if not legacy:
            print("Переносить нечего.")
            return 0

        compared = [r for r in legacy if r["stations_mean"] is not None]
        close = [r for r in compared if abs(r["value"] - r["stations_mean"]) <= TOLERANCE_MM]
        share = len(close) / len(compared) * 100 if compared else 0
        print(f"Сверка со станциями: сравнимо {len(compared)}, в пределах {TOLERANCE_MM} мм — "
              f"{len(close)} ({share:.1f} %)")
        worst = sorted(compared, key=lambda r: -abs(r["value"] - r["stations_mean"]))[:5]
        for r in worst:
            print(f"  наибольшее расхождение: {r['period_start']} декада {r['dekad']}: район {r['value']:.1f}, "
                  f"станции {r['stations_mean']:.1f}")

        recon = [r for r in (await db.execute(text(_RECONSTRUCT_SQL), params)).mappings().all()]
        recon_ok = [r for r in recon if r["d1"] is not None and r["d1"] >= 0]
        print(f"1-я декада восстанавливается (месяц Казгидромет − d2 − d3 ≥ 0): {len(recon_ok)}, "
              f"отброшено (отрицательный результат): {len(recon) - len(recon_ok)}")

        # Архив: сами декады + восстановленные 1-е декады (не перезаписывая сохранившиеся 1-е декады)
        await db.execute(text("""
            INSERT INTO legacy_district_precip_dekads
                (region_id, period_start, period_end, dekad, value, source_id, reconstructed, stations_mean)
            VALUES (:region_id, :period_start, :period_end, :dekad, :value, :source_id, FALSE, :stations_mean)
            ON CONFLICT (region_id, period_start) DO UPDATE SET
                value = EXCLUDED.value, stations_mean = EXCLUDED.stations_mean, reconstructed = FALSE
        """), [dict(r) for r in legacy])
        if recon_ok:
            await db.execute(text("""
                INSERT INTO legacy_district_precip_dekads
                    (region_id, period_start, period_end, dekad, value, source_id, reconstructed)
                VALUES (:region_id, CAST(:period_start AS date), CAST(:period_start AS date) + 9, 1, :d1, :source_id, TRUE)
                ON CONFLICT (region_id, period_start) DO NOTHING
            """), [dict(r) for r in recon_ok])

        await db.execute(text("DELETE FROM indicator_values WHERE id = ANY(:ids)"),
                         {"ids": [r["id"] for r in legacy]})

        left = (await db.execute(text("""
            SELECT count(*) FROM indicator_values iv JOIN indicators i ON i.id = iv.indicator_id
            WHERE i.name = :legacy AND iv.period_type = 'month'
              AND (extract(day FROM iv.period_start) <> 1
                   OR iv.period_end <> (date_trunc('month', iv.period_start) + interval '1 month - 1 day')::date)
        """), params)).scalar_one()
        print(f"Месячных строк не за полный месяц после переноса: {left} (ожидается 0)")

        if args.dry_run:
            await db.rollback()
            print("dry-run: изменения откатены")
        else:
            await db.commit()
            print("Перенесено в legacy_district_precip_dekads.")
    return 0 if left == 0 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
