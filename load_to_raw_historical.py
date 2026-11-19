import os
"""
Скрипт загрузки Excel файлов в raw_historical для последующего ETL.
Запускать на сервере: python3 load_to_raw_historical.py

Поддерживает широкий формат таблиц (года в колонках, районы в строках).
"""
import json
import sys
import pandas as pd
import psycopg2
from pathlib import Path

DB_CONN = os.environ.get("REPORTS_DB_DSN", "host=localhost port=5432 dbname=reports_db user=aldis password=CHANGE_ME")

# Файлы для загрузки — добавляй сюда свои файлы
FILES = [
    {
        "path":           "индексы_СКО_для_дашборда.xlsx",
        "sheets": {
            "Лист1": "Индекс демографического риска рынка труда",
            "Лист2": "Индекс социальной устойчивости СКО",
        },
        "region_col_idx": 0,   # номер колонки с названиями районов (0 = первая)
        "header_row":     0,   # строка с заголовком показателя (0 = первая)
        "data_start_row": 1,   # строка с которой начинаются данные (после годов)
        "years_row":      0,   # строка с годами (0 = строка 0 в уже прочитанном df)
    },
    {
        "path":           "Инновационная_активность_предприятий.xls",
        "sheets": {
            "Лист1": "Инновационная активность предприятий",
        },
        "region_col_idx": 0,
        "header_row":     0,
        "data_start_row": 4,   # данные начинаются с 5-й строки
        "years_row":      3,   # годы в строке 3
    },
]

import sys
sys.path.insert(0, "/opt/aldispetro/etl")
sys.path.insert(0, "/opt/airflow/etl")
from kato_map import KATO_MAP as _KATO_MAP_CANON

# Эталонный словарь имеет формат {ключ: (код, имя)} — берём только код
KATO_MAP = {k: v[0] for k, v in _KATO_MAP_CANON.items()}


def normalize(name: str) -> str:
    return str(name).strip().lower().replace("  ", " ")


def lookup_kato(region_name: str):
    norm = normalize(region_name)
    return KATO_MAP.get(norm)


def process_wide_table(df: pd.DataFrame, indicator_name: str,
                       region_col_idx: int, years_row_idx: int,
                       data_start_row: int):
    """
    Преобразует широкую таблицу (года в колонках) в список записей DSS.
    """
    rows = []
    skipped = []

    # Получаем годы из нужной строки
    year_row = df.iloc[years_row_idx]
    year_cols = {}
    for col_idx, val in enumerate(year_row):
        if col_idx == region_col_idx:
            continue
        try:
            year = int(float(str(val)))
            if 2000 <= year <= 2030:
                year_cols[col_idx] = year
        except (ValueError, TypeError):
            continue

    if not year_cols:
        print(f"  [WARN] Не найдены годы в строке {years_row_idx}")
        return rows

    print(f"  Найдены годы: {sorted(year_cols.values())}")

    # Обходим строки с данными
    for row_idx in range(data_start_row, len(df)):
        row = df.iloc[row_idx]
        region_raw = str(row.iloc[region_col_idx]).strip()

        if not region_raw or region_raw in ("nan", "NaN", "None"):
            continue

        kato_code = lookup_kato(region_raw)
        if not kato_code:
            skipped.append(region_raw)
            continue

        for col_idx, year in year_cols.items():
            val = row.iloc[col_idx]
            if pd.isna(val) or str(val).strip() in ("-", ""):
                continue
            try:
                value = float(str(val).replace(" ", "").replace(",", "."))
            except ValueError:
                continue

            rows.append({
                "indicator_name": indicator_name,
                "direction_code": "other",
                "unit_name":      "единиц",
                "kato_code":      kato_code,
                "period_type":    "year",
                "period_start":   f"{year}-01-01",
                "period_end":     f"{year}-12-31",
                "value":          value,
            })

    if skipped:
        print(f"  [ПРОПУСК] Не найден KATO для: {list(set(skipped))}")

    return rows


def main():
    conn = psycopg2.connect(DB_CONN)
    cur  = conn.cursor()

    total_inserted = 0

    for file_cfg in FILES:
        path = Path(file_cfg["path"])
        if not path.exists():
            print(f"[SKIP] Файл не найден: {path}")
            continue

        print(f"\n=== Обрабатываю: {path.name} ===")

        # Читаем все листы
        try:
            if path.suffix == ".xls":
                all_sheets = pd.read_excel(str(path), sheet_name=None, header=None)
            else:
                all_sheets = pd.read_excel(str(path), sheet_name=None, header=None)
        except Exception as e:
            print(f"  [ERROR] Не могу открыть файл: {e}")
            continue

        for sheet_name, indicator_name in file_cfg["sheets"].items():
            if sheet_name not in all_sheets:
                print(f"  [SKIP] Лист '{sheet_name}' не найден")
                continue

            df = all_sheets[sheet_name]
            print(f"\n  Лист: {sheet_name} → показатель: {indicator_name}")
            print(f"  Размер: {df.shape}")

            rows = process_wide_table(
                df=df,
                indicator_name=indicator_name,
                region_col_idx=file_cfg["region_col_idx"],
                years_row_idx=file_cfg["years_row"],
                data_start_row=file_cfg["data_start_row"],
            )

            if not rows:
                print(f"  [WARN] Нет данных для загрузки")
                continue

            # Загружаем в raw_historical
            for rec in rows:
                cur.execute("""
                    INSERT INTO raw_historical (source_file, data)
                    VALUES (%s, %s)
                """, (
                    path.name,
                    json.dumps(rec, ensure_ascii=False)
                ))

            conn.commit()
            total_inserted += len(rows)
            print(f"  Загружено: {len(rows)} записей")

    cur.close()
    conn.close()
    print(f"\n✅ Итого загружено в raw_historical: {total_inserted} записей")
    print("Теперь запусти dag_extract_historical в Airflow")


if __name__ == "__main__":
    main()
