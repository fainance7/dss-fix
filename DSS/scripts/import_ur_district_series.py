#!/usr/bin/env python3
"""
Импорт районных рядов из расчётной книги индекса УР в базу системы.

Эти ряды закрывают то, из-за чего экологический блок — треть итогового индекса —
не рассчитывался в системе вовсе: выбросы и стационарные источники по районам в
базе отсутствовали, как и возрастные группы для индекса старения и площади
территорий.

Импортируются только те ряды, которых в базе нет или которые заведены пустыми.
Показатели, уже наполненные данными, не трогаются: расхождение источников — это
предмет арбитража, а не молчаливой перезаписи.

По умолчанию режим проверки: скрипт печатает план и ничего не пишет.

    backend/.venv/bin/python scripts/import_ur_district_series.py            # план
    backend/.venv/bin/python scripts/import_ur_district_series.py --commit   # запись
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import psycopg2

REF = Path("ai-service/app/services/landscape_impact/reference/ur_official.json")

# Названия территорий в книге → названия регионов в базе. Сопоставление
# выписано явно: сокращения книги («М.Жумабаева», «Г.Мусрепова») по имени не
# резолвятся, а ошибка сопоставления тихо разложила бы данные по чужим районам.
TERRITORY_MAP = {
    "г.Петропавловск": "г. Петропавловск",
    "Айыртауский": "Айыртауский район",
    "Акжарский": "Акжарский район",
    "М.Жумабаева": "Район Магжана Жумабаева",
    "Есильский": "Есильский район",
    "Жамбылский": "Жамбылский район",
    "Кызылжарский": "Кызылжарский район",
    "Мамлютский": "Мамлютский район",
    "Шал акына": "Район Шал акына",
    "Аккайынский": "Аккайынский район",
    "Тайыншинский": "Тайыншинский район",
    "Тимирязевский": "Тимирязевский район",
    "Уалихановский": "Уалихановский район",
    "Г.Мусрепова": "Район им.Габита Мусрепова",
}

# Ряд книги → показатель базы. `scale` приводит к единице показателя.
SERIES: list[dict] = [
    {
        "source": ("ecological", "emissions_pc"),
        "indicator": "Выбросы на 1 жителя",
        "unit": "кг/чел",
        "direction": "ecology",
        "scale": 1.0,
        "note": "показатель заведён в справочнике, но данных не содержал",
    },
    {
        "source": ("ecological", "emissions_per_area"),
        "indicator": "Выбросы загрязняющих веществ (всего)",
        "unit": "тыс.тонн",
        "direction": "ecology",
        "scale": 1e-3,  # книга даёт тонны
        "note": "в базе есть только областные значения, районных нет",
        "skip_if_present_for_region": True,
    },
    {
        "source": ("ecological", "emission_sources"),
        "indicator": "Количество стационарных источников загрязнения",
        "unit": "единиц",
        "direction": "ecology",
        "scale": 1.0,
        "note": "показателя в справочнике не было",
        "create": True,
    },
    {
        "source": ("social", "ageing_index"),
        "indicator": "Индекс старения трудовых ресурсов (55-64 к 25-34)",
        "unit": "индекс",
        "direction": "social_demog",
        "scale": 1.0,
        "note": (
            "отличается от существующего «Индекс старения населения»: там доля "
            "пожилых, здесь отношение возрастных групп трудоспособного возраста"
        ),
        "create": True,
    },
    {
        "aux": "area",
        "indicator": "Площадь территории",
        "unit": "км²",
        "direction": "land",
        "scale": 1.0,
        "note": "площадей территорий в базе не было ни в каком виде",
        "create": True,
    },
]


def dsn() -> str:
    return os.environ.get(
        "DSS_DB_DSN",
        "host=127.0.0.1 port=5433 dbname=dss_db user=dss_user password=dss_password",
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Импорт районных рядов индекса УР")
    p.add_argument("--commit", action="store_true", help="Записать в базу")
    args = p.parse_args()

    ref = json.loads(REF.read_text(encoding="utf-8"))
    years: list[int] = ref["years_window"]

    conn = psycopg2.connect(dsn())
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT name, id FROM regions")
    regions = dict(cur.fetchall())
    missing = [t for t in TERRITORY_MAP.values() if t not in regions]
    if missing:
        sys.exit(f"В базе нет регионов: {missing}")

    cur.execute("SELECT name, id FROM units")
    units = dict(cur.fetchall())
    cur.execute("SELECT code, id FROM directions")
    directions = dict(cur.fetchall())
    cur.execute("SELECT name, id FROM indicators")
    indicators = dict(cur.fetchall())

    planned = 0
    for spec in SERIES:
        name = spec["indicator"]
        if spec.get("aux"):
            series = ref["aux"][spec["aux"]]["series"]
        else:
            block, key = spec["source"]
            series = ref["blocks"][block]["indicators"][key]["raw"]

        indicator_id = indicators.get(name)
        if indicator_id is None:
            if not spec.get("create"):
                print(f"⚠ {name}: показателя нет в базе и создание не разрешено — пропуск")
                continue
            unit_id = units.get(spec["unit"])
            if unit_id is None:
                print(f"⚠ {name}: единицы «{spec['unit']}» нет в справочнике — пропуск")
                continue
            if args.commit:
                cur.execute(
                    "INSERT INTO indicators (id, direction_id, unit_id, name, sort_order, is_active, created_at)"
                    " VALUES (gen_random_uuid(), %s, %s, %s, 999, true, now()) RETURNING id",
                    (directions[spec["direction"]], unit_id, name),
                )
                indicator_id = cur.fetchone()[0]
                indicators[name] = indicator_id
            print(f"+ показатель «{name}» ({spec['unit']}, {spec['direction']}) — {spec['note']}")

        # Сколько значений уже есть — чтобы не перезаписывать чужой источник.
        existing: set[tuple] = set()
        if indicator_id is not None:
            cur.execute(
                "SELECT region_id, EXTRACT(YEAR FROM period_start)::int"
                " FROM indicator_values WHERE indicator_id = %s",
                (indicator_id,),
            )
            existing = {(str(r), y) for r, y in cur.fetchall()}

        to_write, skipped = [], 0
        for territory, db_name in TERRITORY_MAP.items():
            region_id = regions[db_name]
            values = series.get(territory, [])
            for i, year in enumerate(years):
                v = values[i] if i < len(values) else None
                if v is None:
                    continue
                if (str(region_id), year) in existing:
                    skipped += 1
                    continue
                to_write.append((indicator_id, region_id, year, v * spec["scale"]))

        print(f"  {name}: к записи {len(to_write)}, уже есть {skipped}")
        planned += len(to_write)

        if args.commit and indicator_id is not None and to_write:
            for ind_id, region_id, year, value in to_write:
                cur.execute(
                    "INSERT INTO indicator_values"
                    " (id, indicator_id, region_id, period_type, period_start, period_end,"
                    "  value, validation_status, created_at)"
                    " VALUES (gen_random_uuid(), %s, %s, 'year', %s, %s, %s, 'approved', now())",
                    (ind_id, region_id, date(year, 1, 1), date(year, 12, 31), value),
                )

    print(f"\nВсего к записи: {planned} значений")
    if args.commit:
        conn.commit()
        print("Записано в базу")
    else:
        conn.rollback()
        print("[режим проверки] в базу ничего не записано — добавьте --commit")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
