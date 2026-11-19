import sys, traceback
from pathlib import Path
sys.path.insert(0, "/app/etl")
import pandas as pd
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 14)

from universal_detector import IndicatorCatalog, find_blocks, convert_sheets, resolve_orientation
from document_adapters import extract_sheets, detect_and_pivot_vertical_table, detect_categorical_table

CAT = IndicatorCatalog.from_csv("/app/etl/indicator_catalog.csv", "/app/etl/indicator_aliases.csv")
YEAR = 2025
D = Path("/app/testfiles")

files = sorted([p for p in D.rglob("*") if p.is_file()])
print(f"НАЙДЕНО ФАЙЛОВ: {len(files)}\n")

for p in files:
    print("=" * 90)
    print(f"ФАЙЛ: {p.name}  ({p.stat().st_size} байт)")
    print("=" * 90)
    try:
        sheets, cat_rows = extract_sheets(p.name, p.read_bytes(), catalog=CAT, snapshot_year=YEAR)
    except Exception as e:
        print(f"  !!! extract_sheets УПАЛ: {type(e).__name__}: {e}")
        traceback.print_exc()
        print()
        continue

    print(f"  Листов: {len(sheets)} | категориальных строк: {len(cat_rows)}")

    for name, df in sheets.items():
        print(f"\n  --- ЛИСТ '{name}' shape={None if df is None else df.shape}")
        if df is None or df.empty:
            print("      ПУСТОЙ")
            continue
        print("      ВЕРХНИЙ ЛЕВЫЙ УГОЛ (8 строк x 8 колонок):")
        print(df.iloc[:8, :8].to_string().replace("\n", "\n      "))
        try:
            blocks = find_blocks(df)
        except Exception as e:
            print(f"      find_blocks УПАЛ: {e}")
            blocks = []
        print(f"      find_blocks -> {len(blocks)} блок(ов)")
        for b in blocks:
            try:
                mode, lcol = resolve_orientation(df, b)
            except Exception as e:
                mode, lcol = f"ERR:{e}", "?"
            print(f"         * '{str(b.title)[:55]}' годы={sorted(set(b.year_cols.values()))[:12]} "
                  f"строки={b.data_start}..{b.data_end} режим={mode} label_col={lcol}")
        if not blocks:
            try:
                piv = detect_and_pivot_vertical_table(df)
                print(f"      вертикальный пивот -> {list(piv.keys())[:6] if piv else 'НЕТ'}")
            except Exception as e:
                print(f"      вертикальный пивот УПАЛ: {e}")
            try:
                cr = detect_categorical_table(df, CAT, YEAR)
                print(f"      категориальный -> {len(cr)} строк")
            except Exception as e:
                print(f"      категориальный УПАЛ: {e}")

    try:
        rows, stats = convert_sheets(sheets, CAT, source_file=p.name, log_fn=lambda m: None)
        rows = rows + cat_rows
        print(f"\n  ==> ИТОГО СТРОК: {len(rows)}")
        if rows:
            unm = sum(1 for r in rows if r["direction_code"] == "unmapped")
            print(f"      unmapped: {unm}")
            print(f"      пример: {rows[0]}")
        else:
            print("      !!! НОЛЬ СТРОК — файл не обработается")
    except Exception as e:
        print(f"  !!! convert_sheets УПАЛ: {type(e).__name__}: {e}")
        traceback.print_exc()
    print()
