#!/usr/bin/env python3
"""
Обновление витрины ЗБ-УР по исправленным статданным заказчика (22.09.2026).

Политика по каждому ряду выписана явно, потому что «взять новый файл целиком»
здесь неверно: часть рядов витрины намеренно взята из базы системы, а не из
файла, именно из-за дефектов файла, о которых мы и писали. Слепая перезапись
вернула бы эти дефекты обратно.

## Что подтвердилось

Исправленный файл привёл лист «Сельское хозяйство» в соответствие с листом
«Распределение земель по категориям» — как мы и просили (замечание 16), и
восстановил разряды земель с/х назначения за 2015 год (замечание 15).

## Что осталось

Замечание 16 закрыто частично: повторы устранены только в ряде земель
сельскохозяйственного назначения. В пашне сохранился повтор 2021 = 2020, в
пастбищах — оба (2021 = 2020 и 2023 = 2022). В ряде посевных площадей на
орошении исправление 2023 года создало новый повтор: 2024 = 2023.

Замечания 13 (масличные) и 14 (ценовая база ВРП) без изменений — ряды в файле
те же, арбитраж не проведён. Эти ряды остаются в витрине из базы системы, а
значения файла сохраняются рядом как альтернатива.

Запуск:
    backend/.venv/bin/python scripts/update_landscape_datamart.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import openpyxl

XLSX = Path(
    "data/research/new_stats/Испр_Статданные по  СКО за 2014-2024 гг "
    "для моделирования и оценки вариантов планирования по УР и эконом эффективности.xlsx"
)
REF = Path("ai-service/app/services/landscape_impact/reference/sko_2014_2024.json")
SHEET = "Сельское хозяйство"

# Ряды, которые берём из исправленного файла: там заполнены пропуски, о которых
# мы писали (замечание 18), а конкурирующего значения в базе нет.
FILL_FROM_FILE = {
    "sown_irrigated": {"row": 9, "scale": 1e-3, "years": [2014, 2024]},
}

# Ряды, по которым файл и база расходятся, а арбитраж не проведён. Витрина
# остаётся на значениях базы; значения файла кладём рядом, чтобы при решении
# заказчика переключение было механическим.
KEEP_DB_STORE_ALTERNATIVE = {
    "sown_oil": {"row": 7, "scale": 1e-3, "block": "land_use", "remark": 13},
    "grp": {"row": 14, "scale": 1.0, "block": "economy", "remark": 14},
}

# Повторяющиеся значения соседних лет — признак ошибки заполнения. Проверяем
# ряды, о которых уже писали, плюс тот, где исправление создало новый повтор.
DUPLICATE_CHECKS = {
    "agricultural": {"row": 3, "scale": 1e-3},
    "arable": {"row": 4, "scale": 1e-3},
    "pasture": {"row": 10, "scale": 1e-3},
    "sown_irrigated": {"row": 9, "scale": 1e-3},
}


def read_row(ws, row: int, scale: float, n_years: int) -> list[float | None]:
    out = []
    for i in range(n_years):
        v = ws.cell(row=row, column=2 + i).value
        out.append(round(v * scale, 6) if isinstance(v, (int, float)) else None)
    return out


def find_duplicates(series: list[float | None], years: list[int]) -> list[dict]:
    dups = []
    for i in range(1, len(series)):
        if series[i] is not None and series[i] == series[i - 1]:
            dups.append({"year": years[i], "equals_year": years[i - 1], "value": series[i]})
    return dups


def main() -> None:
    p = argparse.ArgumentParser(description="Обновление витрины ЗБ-УР")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    ws = openpyxl.load_workbook(XLSX, data_only=True)[SHEET]
    ref = json.loads(REF.read_text(encoding="utf-8"))
    years: list[int] = ref["meta"]["years"]

    changes: list[str] = []

    # 1. Заполнение пропусков из файла
    for key, spec in FILL_FROM_FILE.items():
        block = "land_use"
        current = ref[block]["series"][key]
        from_file = read_row(ws, spec["row"], spec["scale"], len(years))
        for year in spec["years"]:
            i = years.index(year)
            if current[i] is None and from_file[i] is not None:
                current[i] = from_file[i]
                changes.append(f"{key} {year}: пропуск заполнен значением {from_file[i]}")
            elif current[i] != from_file[i] and from_file[i] is not None:
                changes.append(
                    f"{key} {year}: витрина {current[i]}, файл {from_file[i]} — оставлено значение витрины"
                )

    # 2. Альтернативы по рядам, ожидающим арбитража
    for key, spec in KEEP_DB_STORE_ALTERNATIVE.items():
        block = spec["block"]
        from_file = read_row(ws, spec["row"], spec["scale"], len(years))
        alt_key = f"alternative_{key}_customer_file"
        previous = ref[block].get(alt_key)
        ref[block][alt_key] = from_file
        if previous != from_file:
            changes.append(f"{key}: обновлена альтернатива из файла (замечание {spec['remark']})")

    # 3. Повторы соседних лет
    duplicates: dict[str, list[dict]] = {}
    for key, spec in DUPLICATE_CHECKS.items():
        series = read_row(ws, spec["row"], spec["scale"], len(years))
        found = find_duplicates(series, years)
        if found:
            duplicates[key] = found

    # 4. Отметки о состоянии замечаний
    ref["land_use"]["suspect_points"] = [
        p for p in ref["land_use"].get("suspect_points", [])
        if p.get("series") != "sown_irrigated"
    ]
    for key, found in duplicates.items():
        if key == "agricultural":
            continue  # в этом ряде повторы устранены
        for item in found:
            ref["land_use" if key != "agricultural" else "land_balance"].setdefault(
                "suspect_points", []
            ).append({
                "series": key,
                "year": item["year"],
                "reason": (
                    f"значение совпадает с {item['equals_year']} годом "
                    f"({item['value']}) — признак ошибки заполнения, замечание 16"
                ),
            })

    ref["meta"]["dataset_version"] = "1.1.0"
    ref["meta"]["updated"] = date.today().isoformat()
    ref["meta"]["sources"]["customer_file_stat_corrected"] = str(XLSX)
    ref["meta"]["customer_response"] = {
        "date": "2026-09-22",
        "file": "data/research/new_stats/ответы на Замечания_методика_оценки_УР_СКО_2026-09-10-2.docx",
        "closed": [15, 16, 18],
        "closed_partially": {
            16: (
                "Повторы устранены только в ряде земель сельскохозяйственного "
                "назначения. В пашне сохранился 2021 = 2020, в пастбищах — "
                "2021 = 2020 и 2023 = 2022."
            ),
        },
        "still_open": {
            13: "Посевная площадь масличных: ряд файла не изменился, арбитраж не проведён",
            14: "Ценовая база ВРП за 2022–2024: ряд файла не изменился",
            17: "Посевная площадь кормовых за 2014 (9 103 га при 653 603 в 2015) не исправлена",
        },
        "new_findings": {
            "sown_irrigated": (
                "Исправление 2023 года создало новый повтор: 2024 = 2023 = 17.329"
            ),
        },
    }

    print("Изменения:")
    for c in changes:
        print(f"  • {c}")
    print("\nПовторы соседних лет в исправленном файле:")
    for key, found in duplicates.items():
        marks = ", ".join(f"{d['year']}={d['equals_year']}" for d in found)
        print(f"  {key:16s} {marks}")
    if not duplicates.get("agricultural"):
        print("  agricultural     повторов нет — замечание 16 по этому ряду закрыто")

    if args.dry_run:
        print("\n[dry-run] файл не изменён")
        return

    REF.write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nВитрина обновлена: {REF} (версия {ref['meta']['dataset_version']})")


if __name__ == "__main__":
    main()
