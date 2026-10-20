import os
"""
Конвертация 4 файлов в DSS формат и загрузка в raw_historical.
Запускать внутри контейнера airflow-worker:

docker exec -w /tmp aldispetro-airflow-worker-1 bash -c \
  "sed 's/host=localhost/host=postgres/g' convert_to_dss.py > conv_fixed.py && python3 conv_fixed.py"
"""
import json
import pandas as pd
import psycopg2
from pathlib import Path

DB_CONN = os.environ["REPORTS_DB_DSN"]  # M-3: required, no insecure default

import sys
sys.path.insert(0, "/opt/aldispetro/etl")
sys.path.insert(0, "/opt/airflow/etl")
from kato_map import KATO_MAP as _KATO_MAP_CANON, normalize as _normalize

# Доп. варианты написания, специфичные для исходных файлов этого скрипта
# (не входят в эталонный справочник, но встречаются в конкретных xlsx)
_EXTRA_KATO_ALIASES = {
    "северо-казахстанская обасть": 590000000,
    "итого по области":            590000000,
    "г. петропавл":                591000000,
    "айыртауский район (ско)":     593200000,
    "акжарский район (ско)":       593400000,
    "м.жумабаева район (ско)":     593600000,
    "есильский район (ско)":       594200000,
    "жамбылский район (ско)":      594600000,
    "кызылжарский район (ско)":    595000000,
    "мамлютский район (ско)":      595200000,
    "шал акына район (ско)":       595600000,
    "тайыншинский район (ско)":    596000000,
    "тимирязевский район (ско)":   596200000,
    "уалихановский район (ско)":   596400000,
    "район им.габита мусрепова":   596600000,
    "им.габита мусрепова":         596600000,
    "новоишимский":                596600000,
    "новоишимское":                596600000,
    "благовещенка":  594600000,
    "булаево":       593600000,
    "пресновка":     594600000,
    "явленка":       594200000,
    "мамлютка":      595200000,
    "сергеевка":     595600000,
    "смирново":      596200000,
    "тайынша":       596000000,
    "тимирязево":    596200000,
    "кишкенеколь":   596400000,
}

# Собираем финальный словарь: эталонный (kato_map.py) + доп. алиасы.
# Эталонный словарь имеет формат {ключ: (код, имя)}, тут нужен только код.
KATO_MAP = {k: v[0] for k, v in _KATO_MAP_CANON.items()}
KATO_MAP.update(_EXTRA_KATO_ALIASES)

def lookup_kato(name):
    return KATO_MAP.get(str(name).strip().lower())

def process_wide(df, indicator_name, direction_code, unit_name,
                 region_col=0, years_row=1, data_start=2):
    rows = []
    year_row = df.iloc[years_row]
    year_cols = {}
    for i, v in enumerate(year_row):
        if i == region_col:
            continue
        try:
            y = int(float(str(v)))
            if 2000 <= y <= 2030:
                year_cols[i] = y
        except:
            continue
    for ri in range(data_start, len(df)):
        row = df.iloc[ri]
        region_raw = str(row.iloc[region_col]).strip()
        if not region_raw or region_raw in ("nan", "NaN", "None", ""):
            continue
        kato = lookup_kato(region_raw)
        if not kato:
            print(f"    [ПРОПУСК] нет КАТО для: '{region_raw}'")
            continue
        for ci, year in year_cols.items():
            val = row.iloc[ci]
            if pd.isna(val) or str(val).strip() in ("-", ""):
                continue
            try:
                value = float(str(val).replace(" ", "").replace(",", "."))
            except:
                continue
            rows.append({
                "indicator_name": indicator_name,
                "direction_code": direction_code,
                "unit_name":      unit_name,
                "kato_code":      kato,
                "period_type":    "year",
                "period_start":   f"{year}-01-01",
                "period_end":     f"{year}-12-31",
                "value":          value,
                "comment":        "",
            })
    return rows

def process_station_wide(df, indicator_name, direction_code, unit_name,
                         prefix, station_col=1, region_col=2, data_start=1):
    rows = []
    headers = list(df.iloc[0])
    year_cols = {}
    for i, h in enumerate(headers):
        if str(h).startswith(prefix):
            try:
                year = int(str(h).replace(prefix, "").strip())
                if 2000 <= year <= 2030:
                    year_cols[i] = year
            except:
                continue
    for ri in range(data_start, len(df)):
        row = df.iloc[ri]
        region_raw = str(row.iloc[region_col]).strip().replace(" (СКО)", "")
        if not region_raw or region_raw in ("nan", "NaN"):
            continue
        kato = lookup_kato(region_raw) or lookup_kato(str(row.iloc[station_col]).strip().lower())
        if not kato:
            print(f"    [ПРОПУСК] нет КАТО для: '{region_raw}'")
            continue
        for ci, year in year_cols.items():
            val = row.iloc[ci]
            if pd.isna(val) or str(val).strip() in ("-", ""):
                continue
            try:
                value = float(str(val).replace(" ", "").replace(",", "."))
            except:
                continue
            rows.append({
                "indicator_name": indicator_name,
                "direction_code": direction_code,
                "unit_name":      unit_name,
                "kato_code":      kato,
                "period_type":    "year",
                "period_start":   f"{year}-01-01",
                "period_end":     f"{year}-12-31",
                "value":          value,
                "comment":        "",
            })
    return rows

def insert_rows(cur, conn, rows, source_file):
    for rec in rows:
        cur.execute(
            "INSERT INTO raw_historical (source_file, data) VALUES (%s, %s)",
            (source_file, json.dumps(rec, ensure_ascii=False))
        )
    conn.commit()

def main():
    conn = psycopg2.connect(DB_CONN)
    cur  = conn.cursor()
    total = 0

    # ФАЙЛ 1: индексы СКО для дашборда.xlsx
    print("\n=== Файл 1: индексы СКО для дашборда.xlsx ===")
    f1 = "/tmp/индексы СКО для дашборда.xlsx"
    if Path(f1).exists():
        xl = pd.read_excel(f1, sheet_name=None, header=None)
        for sheet, (ind, dc, unit) in {
            "Лист1": ("Демографические риски рынка труда", "social_demog", "индекс"),
            "Лист2": ("Социальная устойчивость",           "social_demog", "индекс"),
        }.items():
            if sheet not in xl: continue
            rows = process_wide(xl[sheet], ind, dc, unit, 0, 1, 2)
            insert_rows(cur, conn, rows, Path(f1).name)
            total += len(rows)
            print(f"  {sheet}: {len(rows)} записей")
    else:
        print("  [SKIP] не найден")

    # ФАЙЛ 2: Клим_данные_по_метеостанциям_СКО.xlsx
    print("\n=== Файл 2: Клим_данные_по_метеостанциям_СКО.xlsx ===")
    f2 = "/tmp/Клим_данные_по_метеостанциям_СКО_T_R_decade__1_.xlsx"
    if Path(f2).exists():
        xl = pd.read_excel(f2, sheet_name=None, header=None)
        if "GTKmean03_24" in xl:
            rows = process_station_wide(xl["GTKmean03_24"],
                "Гидротермический коэффициент (ГТК)", "climate", "коэфф.", "GTK_")
            insert_rows(cur, conn, rows, Path(f2).name)
            total += len(rows)
            print(f"  GTK: {len(rows)} записей")
        if "Rmean03_24" in xl:
            rows = process_station_wide(xl["Rmean03_24"],
                "Сумма осадков за вегетационный период", "climate", "мм", "R_05_08_")
            insert_rows(cur, conn, rows, Path(f2).name)
            total += len(rows)
            print(f"  Осадки: {len(rows)} записей")
        if "Tmean03_24" in xl:
            r1 = process_station_wide(xl["Tmean03_24"],
                "Сумма активных температур >10°C (Σt)", "climate", "°C·сут", "ΣcрT_05_08_")
            r2 = process_station_wide(xl["Tmean03_24"],
                "Сумма активных температур >10°C (Σt)", "climate", "°C·сут", "cрT_05_08_")
            seen = set()
            deduped = []
            for r in r1 + r2:
                k = (r["kato_code"], r["period_start"])
                if k not in seen:
                    seen.add(k)
                    deduped.append(r)
            insert_rows(cur, conn, deduped, Path(f2).name)
            total += len(deduped)
            print(f"  Температуры: {len(deduped)} записей")
    else:
        print("  [SKIP] не найден")

    # ФАЙЛ 3: СКО_показатели_для_дашборда.xlsx
    print("\n=== Файл 3: СКО_показатели_для_дашборда.xlsx ===")
    f3 = "/tmp/СКО_показатели_для_дашборда.xlsx"
    if Path(f3).exists():
        xl = pd.read_excel(f3, sheet_name=None, header=None)
        for sheet, (ind, dc, unit) in {
            "числ.занят.нас.":     ("Занятое население", "labor", "тыс.человек"),
            "числ.наем.раб.":      ("Занятое население", "labor", "тыс.человек"),
            "числ.самозанятых":    ("Занятое население", "labor", "тыс.человек"),
            "ср.мес.ЗП по сферам": ("Среднемесячная заработная плата населения по секторам экономики", "labor", "тенге"),
        }.items():
            if sheet not in xl: continue
            rows = process_wide(xl[sheet], ind, dc, unit, 0, 1, 2)
            insert_rows(cur, conn, rows, Path(f3).name)
            total += len(rows)
            print(f"  {sheet}: {len(rows)} записей")
    else:
        print("  [SKIP] не найден")

    # ФАЙЛ 4: Данные_по_СКО_по_дашброду___.xlsx
    print("\n=== Файл 4: Данные_по_СКО_по_дашброду___.xlsx ===")
    f4 = "/tmp/Данные_по_СКО_по_дашброду___.xlsx"
    if Path(f4).exists():
        xl = pd.read_excel(f4, sheet_name=None, header=None)
        sheets_config = {
            "Индекс ВРП":               ("Индекс физического объема ВРП", "economy", "%", 0, 1, 2),
            "Индекс ВПСХ":              ("Индекс валовой продукции сельского хозяйства", "economy", "%", 0, 1, 2),
            "индекс ВПП":               ("Валовая продукция промышленности", "economy", "%", 0, 1, 2),
            "Индекс произ. труда":      ("Индекс производительности труда", "economy", "%", 0, 1, 2),
            "Ср. год доля занятости":   ("Среднегодовая доля занятых в экономике в общей численности активного населения", "economy", "%", 0, 1, 2),
            "Объем инвестиций":         ("Объем инвестиций в основной капитал", "economy", "млрд.тенге", 0, 1, 2),
            "Объем розничной торговли": ("Объем розничной торговли", "economy", "млрд.тенге", 0, 1, 2),
            "Валовая продукция СХ":     ("Валовая продукция сельского хозяйства", "economy", "млрд.тенге", 0, 1, 2),
            "Валовая продукция промыш.":("Валовая продукция промышленности", "economy", "млрд.тенге", 0, 1, 2),
            "Ср ном зп":                ("Среднемесячная номинальная заработная плата", "labor", "тенге", 0, 1, 2),
            "Доля насел.,ниже прож мин":("Доля населения, имеющие доходы ниже величины прожиточного уровня", "economy", "%", 0, 1, 2),
            "Величина прожиточного минимума": ("Величина прожиточного минимума", "social_demog", "тенге", 0, 2, 3),
        }
        for sheet, (ind, dc, unit, rc, yr, ds) in sheets_config.items():
            if sheet not in xl:
                print(f"  [SKIP] лист не найден: {sheet}")
                continue
            rows = process_wide(xl[sheet], ind, dc, unit, rc, yr, ds)
            insert_rows(cur, conn, rows, Path(f4).name)
            total += len(rows)
            print(f"  {sheet}: {len(rows)} записей")
    else:
        print("  [SKIP] не найден")

    cur.close()
    conn.close()
    print(f"\n✅ Итого загружено в raw_historical: {total} записей")
    print("Теперь запусти dag_extract_historical в Airflow")

if __name__ == "__main__":
    main()
