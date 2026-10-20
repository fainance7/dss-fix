#!/usr/bin/env python3
"""
Обновление витрины ЗБ-УР по второй редакции статданных заказчика (30.09.2026).

Файл: «2 Испр_Статданные по СКО за 2014-2024 …» из docs/updates/300926. Как и
в первой редакции (scripts/update_landscape_datamart.py, 22.09), новый файл
целиком не переносится: политика по каждому ряду выписана ниже. Строки ищутся
по названию, а не по номеру — в этой редакции вставлена строка и нумерация
сдвинулась.

## Что закрыто

- **Замечание 16 (повторы соседних лет).** Пашня 2021 и пастбища 2021/2023
  исправлены, и новые значения совпали с теми, что витрина уже брала из базы
  системы. Отметки о повторах снимаются.
- **Замечание 17 (кормовые 2014).** Заказчик привёл второй источник
  («уточнённая посевная площадь», тыс. га): 2014 = 443,1 тыс. га вместо
  9,103. Значение берётся в витрину; остальные годы остаются из базы — второй
  источник расходится с ними до 6 %, и смешивать источники внутри ряда не
  нужно.

## Что осталось или появилось

- **Строка кормовых в гектарах испорчена**: 2014–2015 записаны в тыс. га, а
  2024 = 3 221 041,9 га (в 9 раз больше соседних лет). Из неё ничего не берём.
- **Орошение.** 2021 = 11,614 тыс. га закрывает повтор и берётся. 2023: файл
  17,329, база 13,364 — витрина остаётся на базе, файл сохраняется рядом.
  2024: файл исправил собственный повтор на 6,8 тыс. га (падение на 61 % за
  год) — значение берётся, потому что витрина за 2024 и прежде стояла на файле,
  но помечается как требующее подтверждения. В калибровку ряд не входит.
- **Замечание 13 (масличные).** Файл изменил 2021–2022, но с базой расходится
  по-прежнему (2022: 959,7 против 1118,5; 2024: 674,2 против 1296,8) —
  арбитраж не проведён, альтернатива обновляется.
- **Замечание 14 (ценовая база ВРП)** — ряд файла не изменился.

Запуск:
    backend/.venv/bin/python scripts/update_landscape_datamart_2026_09_30.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import openpyxl

XLSX = Path(
    "docs/updates/300926/2 Испр_Статданные по  СКО за 2014-2024 гг для моделирования "
    "и оценки вариантов планирования по УР и эконом эффективн.xlsx"
)
REF = Path("ai-service/app/services/landscape_impact/reference/sko_2014_2024.json")
SHEET = "Сельское хозяйство"
YEARS = list(range(2014, 2025))


def find_rows(ws, prefix: str) -> list[int]:
    """Номера строк, чьё название начинается с prefix (без учёта пробелов по краям)."""
    return [
        r for r in range(1, ws.max_row + 1)
        if isinstance(ws.cell(r, 1).value, str) and ws.cell(r, 1).value.strip().startswith(prefix)
    ]


def read_row(ws, row: int, scale: float) -> list[float | None]:
    out = []
    for i in range(len(YEARS)):
        v = ws.cell(row=row, column=2 + i).value
        out.append(round(v * scale, 6) if isinstance(v, (int, float)) else None)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Обновление витрины ЗБ-УР (редакция 30.09.2026)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    ws = openpyxl.load_workbook(XLSX, data_only=True)[SHEET]
    ref = json.loads(REF.read_text(encoding="utf-8"))
    assert ref["meta"]["years"] == YEARS
    lu = ref["land_use"]
    series = lu["series"]
    changes: list[str] = []

    def idx(year: int) -> int:
        return YEARS.index(year)

    # 1. Замечание 16: пашня и пастбища — файл должен совпасть с витриной.
    for key, prefix in (("arable", "Пашня-всего"), ("pasture", "Площадь пастбищ")):
        [row] = find_rows(ws, prefix)
        from_file = read_row(ws, row, 1e-3)
        for year in (2021, 2023):
            i = idx(year)
            if from_file[i] is None or abs(from_file[i] - series[key][i]) > 0.1:
                raise SystemExit(f"{key} {year}: файл {from_file[i]}, витрина {series[key][i]} — разбор вручную")
        changes.append(f"{key}: файл совпал с витриной в 2021/2023 — замечание 16 закрыто")

    # 2. Замечание 17: кормовые 2014 по второму источнику (тыс. га).
    # Строк с таким названием несколько (гектары, тыс. га, подзаголовок без
    # чисел) — второй источник узнаём по данным: все годы заполнены, тыс. га.
    candidates = [
        read_row(ws, r, 1.0) for r in find_rows(ws, "Посевная площадь кормовых культур")
    ]
    [second_source] = [
        s for s in candidates if all(v is not None for v in s) and max(s) < 10_000
    ]
    old = series["sown_fodder"][0]
    series["sown_fodder"][0] = second_source[0]
    lu["notes"]["sown_fodder"] = (
        "dss_db; 2014 — из второго источника заказчика («уточнённая посевная площадь», "
        f"ответ 30.09.2026 на замечание 17): {second_source[0]} тыс. га вместо {old}. "
        "Строка файла в гектарах испорчена (2014–2015 в тыс. га, 2024 = 3,22 млн га) и не используется."
    )
    lu["alternative_sown_fodder_second_source"] = second_source
    changes.append(f"sown_fodder 2014: {old} → {second_source[0]} (замечание 17)")

    # 3. Орошение.
    [irr_row] = find_rows(ws, "Посевная площадь сельхозкультур на орошении")
    irr = read_row(ws, irr_row, 1e-3)
    for year in (2021, 2024):
        i = idx(year)
        changes.append(f"sown_irrigated {year}: {series['sown_irrigated'][i]} → {irr[i]}")
        series["sown_irrigated"][i] = irr[i]
    lu["alternative_sown_irrigated_customer_file"] = irr
    changes.append(
        f"sown_irrigated 2023: витрина {series['sown_irrigated'][idx(2023)]}, файл {irr[idx(2023)]} — оставлена база"
    )

    # 4. Замечание 13: масличные — обновить альтернативу, витрина остаётся на базе.
    [oil_row] = find_rows(ws, "Посевная площадь масличных")
    lu["alternative_sown_oil_customer_file"] = read_row(ws, oil_row, 1e-3)
    changes.append("sown_oil: альтернатива из файла обновлена (2021–2022), арбитраж не проведён")

    # 5. Отметки о подозрительных точках.
    lu["suspect_points"] = [
        pt for pt in lu.get("suspect_points", [])
        if pt["series"] not in ("arable", "pasture", "sown_fodder")
        and not (pt["series"] == "sown_irrigated" and pt["year"] in (2021, 2024))
    ]
    lu["suspect_points"].append({
        "series": "sown_irrigated",
        "year": 2024,
        "reason": (
            "6,8 тыс. га при 17,3 в 2023 (−61 % за год) — исправление повтора заказчиком "
            "30.09.2026, требует подтверждения"
        ),
    })
    lu["suspect_points"].append({
        "series": "sown_irrigated",
        "year": 2023,
        "reason": "база 13,364 тыс. га, файл заказчика 17,329 — расхождение источников",
    })

    ref["meta"]["dataset_version"] = "1.2.0"
    ref["meta"]["updated"] = date.today().isoformat()
    ref["meta"]["sources"]["customer_file_stat_corrected_2"] = str(XLSX)
    ref["meta"]["customer_response_2"] = {
        "date": "2026-09-30",
        "closed": [16, 17],
        "still_open": {
            13: "Посевная площадь масличных: файл изменил 2021–2022, расхождение с базой до 48 % сохраняется",
            14: "Ценовая база ВРП за 2022–2024: ряд файла не изменился",
        },
        "new_findings": {
            "sown_fodder_ha_row": "Строка кормовых в гектарах: смешаны единицы (2014–2015 в тыс. га), 2024 = 3 221 041,9 га",
            "sown_irrigated": "2023 расходится с базой (17,329 против 13,364); 2024 = 6,8 — падение на 61 %",
        },
    }

    print("Изменения:")
    for c in changes:
        print(f"  • {c}")
    if args.dry_run:
        print("\n[dry-run] файл не изменён")
        return
    REF.write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nВитрина обновлена: {REF} (версия {ref['meta']['dataset_version']})")


if __name__ == "__main__":
    main()
