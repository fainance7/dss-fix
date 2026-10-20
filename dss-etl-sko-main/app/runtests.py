import sys, collections
sys.path.insert(0, "/app/etl")
from pathlib import Path
from universal_detector import IndicatorCatalog, convert_sheets
from document_adapters import extract_sheets
CAT = IndicatorCatalog.from_csv("/app/etl/indicator_catalog.csv", "/app/etl/indicator_aliases.csv")

print(f"{'ФАЙЛ':38} {'СТРОК':>6} {'РЕГИОНОВ':>9} {'ЛЕТ':>4}  ИТОГ")
print("-" * 78)
for p in sorted(Path("/app/synthtests").glob("*")):
    try:
        sh, cr = extract_sheets(p.name, p.read_bytes(), catalog=CAT, snapshot_year=2026)
        rows, _ = convert_sheets(sh, CAT, source_file=p.name, log_fn=lambda m: None)
        rows += cr
    except Exception as e:
        print(f"{p.name[:36]:38} {'—':>6} {'—':>9} {'—':>4}  ОШИБКА: {str(e)[:28]}")
        continue
    katos = {r["kato_code"] for r in rows}
    years = {r["period_start"][:4] for r in rows}
    ok = "OK" if len(rows) >= 6 else ("ЧАСТИЧНО" if rows else "ПРОВАЛ")
    print(f"{p.name[:36]:38} {len(rows):6} {len(katos):9} {len(years):4}  {ok}")
