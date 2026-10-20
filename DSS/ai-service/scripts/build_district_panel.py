#!/usr/bin/env python3
"""Сборка районной панели ЗБ-УР из `indicator_values` в справочник модуля.

Панель — снимок утверждённых годовых значений по районам (уровень 2) на дату
сборки. Расчёт читает справочник, а не БД: прогон должен воспроизводиться
(гейт G12), а утверждённые значения меняются при каждой новой партии данных.
После загрузки новой партии панель пересобирается этим скриптом, версия
справочника меняется, и меняется версия калибровки в каждом прогоне.

Запуск:
    docker exec dss-ai-service python scripts/build_district_panel.py            # печать сводки
    docker exec dss-ai-service python scripts/build_district_panel.py --write    # запись справочника
    docker exec dss-ai-service python scripts/build_district_panel.py --from-csv /w/panel.csv --write

Справочник лежит в app/services/landscape_impact/reference/district_panel.json
и поставляется вместе с образом; на проде после сборки его нужно закоммитить.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.landscape_impact.district_panel import (  # noqa: E402
    _PANEL_FILE, PANEL_INDICATORS, build_panel_json,
)

QUERY = """
    SELECT i.name, d.code, r.name AS region,
           EXTRACT(YEAR FROM v.period_start)::int AS yr, v.value
    FROM indicator_values v
    JOIN indicators i ON i.id = v.indicator_id
    JOIN directions d ON d.id = i.direction_id
    JOIN regions r ON r.id = v.region_id
    WHERE r.level = 2
      AND v.validation_status = 'approved'
      AND v.period_type = 'year'
      AND i.name = ANY(:names)
"""


async def collect() -> dict:
    from sqlalchemy import text

    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(QUERY), {"names": list(PANEL_INDICATORS.values())})).fetchall()
    today = date.today().isoformat()
    return build_panel_json(
        rows, version=f"panel-{today}",
        source=f"dss_db.indicator_values на {today}: approved, period_type=year, regions.level=2",
    )


def from_csv(path: Path, version: str | None) -> dict:
    """Та же сборка по CSV-выгрузке прода — когда БД сервиса недоступна."""
    with path.open(encoding="utf-8") as fh:
        rows = [(r["indicator"], r["direction"], r["region"], int(r["year"]),
                 float(r["value"]) if r["value"] else None)
                for r in csv.DictReader(fh) if r["level"] == "2"]
    return build_panel_json(
        rows, version=version or f"panel-{date.today().isoformat()}",
        source=f"CSV-выгрузка {path.name}: approved, period_type=year, regions.level=2",
    )


def summary(panel: dict) -> None:
    print(f"районов: {len(panel['regions'])}, годы {panel['years'][0]}–{panel['years'][-1]}")
    for key, by_region in panel["series"].items():
        n = sum(len(v) for v in by_region.values())
        yrs = sorted({int(y) for v in by_region.values() for y in v})
        print(f"  {key:14s} {n:4d} значений, районов {len(by_region):2d}, лет {len(yrs):2d}"
              + (f" ({yrs[0]}–{yrs[-1]})" if yrs else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="записать справочник")
    ap.add_argument("--from-csv", metavar="FILE",
                    help="собрать из CSV-выгрузки (indicator,direction,unit,region,level,year,value)")
    ap.add_argument("--version", help="версия справочника (по умолчанию panel-<сегодня>)")
    args = ap.parse_args()
    if args.from_csv:
        panel = from_csv(Path(args.from_csv), args.version)
    else:
        panel = asyncio.run(collect())
        if args.version:
            panel["meta"]["version"] = args.version
    summary(panel)
    if args.write:
        _PANEL_FILE.write_text(json.dumps(panel, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"записано: {_PANEL_FILE}")


if __name__ == "__main__":
    main()
