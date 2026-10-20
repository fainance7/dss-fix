#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_dss.py — преобразует агрегированную сводку agromap (район + тип угодья)
в формат DSS Intake (data_intake_template.xlsx, лист Values, 9 колонок).

Явное, вручную проверенное сопоставление названий районов agromap с kato_code —
не автоматическое/нечёткое сравнение (см. историю с Булаево/Мамлюткой, где
автоматическое сопоставление по созвучию оказалось ошибочным).

Единицы: DSS ожидает тыс.га (indicator_catalog.csv), не га — деление на 1000.
Период: year, 2026 (разовый срез, данные не имеют истории по годам).
"""
import os
import sys
from datetime import datetime

import psycopg2
import pandas as pd

DISTRICT_TO_KATO = {
    "Айыртауский р-н": 593200000,
    "Акжарский р-н": 593400000,
    "Аккайынский р-н": 595800000,
    "Есильский р-н": 594200000,
    "Жамбылский р-н": 594600000,
    "Кызылжарский р-н": 595000000,
    "Мамлютский р-н": 595200000,
    "р-н им.Габита Мусрепова": 596600000,
    "р-н Магжана Жумабаева": 593600000,
    "р-н Шал акына": 595600000,
    "Тайыншинский р-н": 596000000,
    "Тимирязевский р-н": 596200000,
    "Уалихановский р-н": 596400000,
}

OBJECT_TO_INDICATOR = {
    "пашня": "Площадь пашни",
    "залежь": "Залежь",
    "пастбище": "Площадь пастбищ",
    "сенокос": "Сенокосы",
    "многолетние насаждения": "Многолетние насаждения",
}

UNIT_NAME = "тыс.га"
DIRECTION_CODE = "land"
PERIOD_TYPE = "year"
PERIOD_START = "2026-01-01"
PERIOD_END = "2026-12-31"


def get_geo_db_conn():
    required = ["GEO_DB_HOST", "GEO_DB_PORT", "GEO_DB_NAME", "GEO_DB_USER", "GEO_DB_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"FATAL: отсутствуют переменные окружения: {missing}", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(
        host=os.environ["GEO_DB_HOST"],
        port=os.environ["GEO_DB_PORT"],
        dbname=os.environ["GEO_DB_NAME"],
        user=os.environ["GEO_DB_USER"],
        password=os.environ["GEO_DB_PASSWORD"],
    )


def main():
    conn = get_geo_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.name AS district_name, lp.object_ru, sum(lp.area_ha) AS area_ha
                FROM land_polygons lp
                JOIN agromap_districts d ON d.id = lp.district_id
                WHERE lp.source_region = 'sko' AND lp.is_active = true
                GROUP BY d.name, lp.object_ru
                ORDER BY d.name, lp.object_ru
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    output_rows = []
    skipped_district = set()
    skipped_object = set()

    for district_name, object_ru, area_ha in rows:
        kato_code = DISTRICT_TO_KATO.get(district_name)
        if kato_code is None:
            skipped_district.add(district_name)
            continue

        indicator_name = OBJECT_TO_INDICATOR.get(object_ru)
        if indicator_name is None:
            skipped_object.add(object_ru)
            continue

        output_rows.append({
            "indicator_name": indicator_name,
            "direction_code": DIRECTION_CODE,
            "unit_name": UNIT_NAME,
            "kato_code": kato_code,
            "period_type": PERIOD_TYPE,
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "value": round(float(area_ha) / 1000, 3),
            "comment": "",
        })

    print(f"Сформировано строк: {len(output_rows)}")
    if skipped_district:
        print(f"ПРОПУЩЕНО районов (нет в DISTRICT_TO_KATO): {sorted(skipped_district)}", file=sys.stderr)
    if skipped_object:
        print(f"ПРОПУЩЕНО типов угодий (нет в OBJECT_TO_INDICATOR): {sorted(skipped_object)}", file=sys.stderr)

    df = pd.DataFrame(output_rows)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifacts_dir = os.environ.get("ARTIFACTS_DIR", "/app/artifacts")
    out_path = os.path.join(artifacts_dir, f"agromap_dss_land_{timestamp}.xlsx")

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Values", index=False)

    print(f"Файл сохранён: {out_path}")


if __name__ == "__main__":
    main()
