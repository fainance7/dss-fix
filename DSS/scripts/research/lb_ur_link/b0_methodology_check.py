"""b0. Сверка новой редакции отчёта НИР (таблицы 3 и 4) со справочником ur_official.json.

Читает /wlb/nir.docx (копия `docs/отчет НИР_оценка УР СКО_испр.docx`), разбирает таблицы
без python-docx (только zipfile + XML) и сравнивает границы и веса. Справочник не правится.
"""
import re
import zipfile
import xml.etree.ElementTree as ET

from common import W, ur_model

NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ORDER = [("social", "birth_rate"), ("social", "death_rate"), ("social", "migration_balance"),
         ("social", "unemployment"), ("social", "ageing_index"), ("social", "crime_rate"),
         ("economic", "investment_pc"), ("economic", "industry_pc"), ("economic", "agri_pc"),
         ("economic", "retail_pc"), ("economic", "wage_to_subsistence"),
         ("ecological", "emission_sources"), ("ecological", "emissions_pc"), ("ecological", "emissions_per_area")]


def num(s):
    s = s.replace("−", "-").replace("–", "-").replace("\xa0", "").replace(" ", "").replace(",", ".").replace("+", "")
    return float(s)


def tables(path):
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    for tbl in root.iter(NS + "tbl"):
        yield [["".join(t.text or "" for t in c.iter(NS + "t")).strip() for c in r.iter(NS + "tc")]
               for r in tbl.iter(NS + "tr")]


model = ur_model()
bounds_doc, weights_doc = None, None
for rows in tables(f"{W}/nir.docx"):
    head = " ".join(rows[0])
    if "Xmin" in head and "Xmax" in head:
        bounds_doc = [r for r in rows if r and re.fullmatch(r"\d+", r[0])]
    if "Вес" in head and "Блок" in head:
        weights_doc = [r for r in rows[1:] if len(r) >= 3 and re.fullmatch(r"[\d,]+", r[2])]

problems = []
print("Таблица 3 — границы нормирования")
for (block, ind), row in zip(ORDER, bounds_doc):
    ref = model.blocks[block]["indicators"][ind]["bounds"]
    lo, hi = num(row[3]), num(row[4])
    ok = abs(lo - ref["low"]) < 1e-9 and abs(hi - ref["high"]) < 1e-9
    print(f"  {row[1][:60]:60s} док {lo:>8g}…{hi:<8g} справочник {ref['low']:>8g}…{ref['high']:<8g} {'OK' if ok else 'РАСХОЖДЕНИЕ'}")
    if not ok:
        problems.append(("bounds", ind, (lo, hi), (ref["low"], ref["high"])))

print("Таблица 4 — веса AHP")
for (block, ind), row in zip(ORDER, weights_doc):
    ref = model.weight(block, ind)
    w = num(row[2])
    ok = abs(w - ref) < 1e-9
    print(f"  {row[1][:60]:60s} док {w:.2f} справочник {ref:.2f} {'OK' if ok else 'РАСХОЖДЕНИЕ'}")
    if not ok:
        problems.append(("weight", ind, w, ref))

print(f"\nСтрок границ: {len(bounds_doc)}, весов: {len(weights_doc)}; расхождений: {len(problems)}")
for p in problems:
    print("  ", p)
