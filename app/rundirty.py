import sys
sys.path.insert(0, "/app/etl")
from pathlib import Path
from universal_detector import IndicatorCatalog, convert_sheets
from document_adapters import extract_sheets
CAT = IndicatorCatalog.from_csv("/app/etl/indicator_catalog.csv", "/app/etl/indicator_aliases.csv")

def load(p):
    sh, cr = extract_sheets(p.name, p.read_bytes(), catalog=CAT, snapshot_year=2026)
    rows, _ = convert_sheets(sh, CAT, source_file=p.name, log_fn=lambda m: None)
    return rows + cr

CHECKS = {
 "d1": lambda rs: (len({r["indicator_name"] for r in rs}) >= 3,
                   "музеи/театры/памятники — разные показатели"),
 "d2": lambda rs: (any(abs(r["value"] - 60202.6) < 0.1 for r in rs)
                   and not any(r["value"] > 113000 for r in rs),
                   "неразрывный пробел разобран, 'Всего' пропущено"),
 "d3": lambda rs: (not any(r["value"] == 134100 for r in rs),
                   "составная ячейка не приписана одному району"),
 "d4": lambda rs: (len({r["period_start"][:4] for r in rs}) >= 10,
                   "диапазоны развёрнуты по годам"),
 "d5": lambda rs: (len({r["indicator_name"] for r in rs}) >= 2,
                   "млн.тенге и % — разные показатели"),
 "d6": lambda rs: (len({r["indicator_name"] for r in rs}) >= 2,
                   "область и зона разведены"),
 "d7": lambda rs: (len({r["kato_code"] for r in rs}) >= 6,
                   "'Наименование адм единицы' распознано"),
 "d8": lambda rs: (any("Поголовье КРС" == r["indicator_name"] for r in rs),
                   "мусор в заголовке очищен"),
 "d9": lambda rs: (len({r["kato_code"] for r in rs}) >= 5,
                   "варианты написания районов найдены"),
 "d10": lambda rs: (len({r["kato_code"] for r in rs}) >= 6,
                    "пустые строки/колонки не мешают"),
}

print(f"{'ТЕСТ':32} {'СТРОК':>6} {'РЕГ':>4} {'ЛЕТ':>4}  РЕЗУЛЬТАТ")
print("-" * 88)
for p in sorted(Path("/app/dirtytests").glob("*")):
    key = p.name.split("_")[0]
    try:
        rs = load(p)
    except Exception as e:
        print(f"{p.name[:30]:32} {'—':>6} {'—':>4} {'—':>4}  ОШИБКА: {str(e)[:30]}")
        continue
    reg = len({r["kato_code"] for r in rs}); yrs = len({r["period_start"][:4] for r in rs})
    fn = CHECKS.get(key)
    if fn and rs:
        ok, what = fn(rs)
        mark = "OK  " if ok else "ПРОВАЛ"
        print(f"{p.name[:30]:32} {len(rs):6} {reg:4} {yrs:4}  {mark} {what}")
    else:
        print(f"{p.name[:30]:32} {len(rs):6} {reg:4} {yrs:4}  ПУСТО")
