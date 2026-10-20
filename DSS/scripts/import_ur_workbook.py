#!/usr/bin/env python3
"""
Импорт расчётной книги «Оценка УР СКО.xlsx» в справочник официального индекса.

Книга получена от заказчика 22.09.2026 вместе с ответами на наши замечания и
впервые содержит полный набор данных: 14 показателей по 14 территориям за
2014–2024, площади территорий и возрастные группы. До неё экологический блок —
треть итогового индекса — не рассчитывался вовсе.

## Почему границы нормирования разбираются из формул

Ответ на замечание 1 подтвердил: минимум и максимум в методике — **фиксированные
границы**, а не пересчитываемые min/max выборки (наш прежний вывод о сквозном
нормировании по 154 наблюдениям снят). Таблицу границ заказчик обещал привести
в новой редакции методики, но она ещё не выпущена. Единственный доступный
первоисточник — формулы самой книги, поэтому границы извлекаются разбором
формул, а не переписываются руками: так они воспроизводимы и при новой редакции
книги пересобираются заново, а опечатка при переносе исключена.

## Устройство книги

Каждый лист показателя разбит на блоки по 20 строк:
    строка 1  — заголовок блока
    строка 2  — шапка годов (2014…2024 в столбцах B…L)
    строка 3  — Северо-Казахстанская область (сводка, в расчёт блоков не входит)
    строки 4–17 — 14 территорий
Следующие блоки (21–37, 41–57, 61–77) — промежуточные пересчёты (на душу
населения, на единицу площади) и итоговое нормированное значение. Нормированным
считается ПОСЛЕДНИЙ блок листа.

Запуск:
    backend/.venv/bin/python scripts/import_ur_workbook.py \\
        --workbook "data/research/new_stats/Оценка УР СКО.xlsx" \\
        --out ai-service/app/services/landscape_impact/reference/ur_official.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import openpyxl
except ImportError:  # pragma: no cover
    sys.exit("Нужен openpyxl: запускайте через backend/.venv/bin/python")

# Листы показателей в порядке блоков методики.
INDICATOR_SHEETS: dict[str, dict] = {
    "social": {
        "рождаемость": {"key": "birth_rate", "weight": 0.36, "unit": "на 1000 человек"},
        "смертность": {"key": "death_rate", "weight": 0.25, "unit": "на 1000 человек"},
        "миграция": {"key": "migration_balance", "weight": 0.16, "unit": "‰"},
        "безработица": {"key": "unemployment", "weight": 0.11, "unit": "%"},
        "55-64 к 25-34": {"key": "ageing_index", "weight": 0.07, "unit": "индекс"},
        "преступность": {"key": "crime_rate", "weight": 0.05, "unit": "на 10 000 человек"},
    },
    "economic": {
        "инвестиции": {"key": "investment_pc", "weight": 0.39, "unit": "тыс. тенге на душу"},
        # Вес 0,25, а не 0,26: ответ на замечание 2 подтвердил, что 0,26 в отчёте —
        # ошибка переноса, в расчётах применялось 0,25 (сумма весов блока = 1,00).
        # В самой книге лист «Перечень показателей» всё ещё показывает 0,26,
        # тогда как формулы листа «Индексы» считают по 0,25 — сверено.
        "промыш-ть": {"key": "industry_pc", "weight": 0.25, "unit": "тыс. тенге на душу"},
        "сельхоз": {"key": "agri_pc", "weight": 0.11, "unit": "млн тенге на душу"},
        "рознич.торговля": {"key": "retail_pc", "weight": 0.08, "unit": "тыс. тенге на душу"},
        "ЗП к ВПМ": {"key": "wage_to_subsistence", "weight": 0.17, "unit": "раз"},
    },
    "ecological": {
        "стац.источ.": {"key": "emission_sources", "weight": 0.11, "unit": "ед. на площадь"},
        "выбросы на душу": {"key": "emissions_pc", "weight": 0.31, "unit": "кг на душу"},
        "выбросы на площадь": {"key": "emissions_per_area", "weight": 0.58, "unit": "т на км²"},
    },
}

AUX_SHEETS = {"Числ.нас-я": "population", "Площадь": "area"}
INDEX_SHEET = "Индексы"
OBLAST_LABEL = "Северо-Казахстанская область"

BLOCK_STRIDE = 20          # блоки начинаются на строках 1, 21, 41, 61
YEARS_ROW_OFFSET = 1       # шапка годов — вторая строка блока
OBLAST_ROW_OFFSET = 2      # сводка по области — третья
FIRST_TERRITORY_OFFSET = 3
TERRITORY_COUNT = 14


def _block_starts(ws) -> list[int]:
    """Строки начала блоков листа (1, 21, 41, …) — по наличию шапки годов."""
    starts = []
    for start in range(1, ws.max_row + 1, BLOCK_STRIDE):
        year_cell = ws.cell(row=start + YEARS_ROW_OFFSET, column=2).value
        if isinstance(year_cell, (int, float)) and 2000 <= year_cell <= 2100:
            starts.append(start)
    return starts


def _years(ws, block_start: int) -> list[int]:
    row = block_start + YEARS_ROW_OFFSET
    out = []
    for col in range(2, ws.max_column + 1):
        v = ws.cell(row=row, column=col).value
        if isinstance(v, (int, float)) and 2000 <= v <= 2100:
            out.append(int(v))
    return out


def _trim_to_window(series: dict[str, list], years: list[int], window: list[int]) -> dict[str, list]:
    """Обрезать ряды до опубликованного окна лет."""
    idx = [years.index(y) for y in window if y in years]
    return {name: [vals[i] for i in idx] for name, vals in series.items()}


def _read_block(ws, block_start: int, years: list[int]) -> dict[str, list]:
    """Территория → ряд значений по годам. Строка области пропускается:
    в методике областное значение получается усреднением территорий, а не берётся
    из этой строки (см. замечание 10 о невзвешенном усреднении)."""
    series: dict[str, list] = {}
    for i in range(TERRITORY_COUNT):
        row = block_start + FIRST_TERRITORY_OFFSET + i
        name = ws.cell(row=row, column=1).value
        if not name:
            continue
        values = []
        for col in range(2, 2 + len(years)):
            v = ws.cell(row=row, column=col).value
            values.append(float(v) if isinstance(v, (int, float)) else None)
        series[str(name).strip()] = values
    return series


# Разбор формул нормирования. Встречаются четыре вида:
#   =B3/20                     → границы 0…20, стимулятор
#   =1-B3/22                   → границы 0…22, дестимулятор
#   =(B43+70)/(25+70)          → границы −70…25, стимулятор
#   =1-(B3-30)/(220-30)        → границы 30…220, дестимулятор
#   =(B43-3)/(6.5-3)           → границы 3…6,5, стимулятор
_NUM = r"-?\d+(?:[.,]\d+)?"
_PATTERNS = [
    # 1-(X-lo)/(hi-lo)  — дестимулятор с ненулевой нижней границей
    (re.compile(rf"^=1-\([A-Z]+\d+-({_NUM})\)/\(({_NUM})-({_NUM})\)$"),
     lambda m: (float(m[1]), float(m[2]), -1)),
    # (X+a)/(hi+a)      — стимулятор с отрицательной нижней границей
    (re.compile(rf"^=\([A-Z]+\d+\+({_NUM})\)/\(({_NUM})\+({_NUM})\)$"),
     lambda m: (-float(m[1]), float(m[2]), +1)),
    # (X-lo)/(hi-lo)    — стимулятор с ненулевой нижней границей
    (re.compile(rf"^=\([A-Z]+\d+-({_NUM})\)/\(({_NUM})-({_NUM})\)$"),
     lambda m: (float(m[1]), float(m[2]), +1)),
    # 1-X/hi            — дестимулятор от нуля
    (re.compile(rf"^=1-[A-Z]+\d+/({_NUM})$"),
     lambda m: (0.0, float(m[1]), -1)),
    # X/hi              — стимулятор от нуля
    (re.compile(rf"^=[A-Z]+\d+/({_NUM})$"),
     lambda m: (0.0, float(m[1]), +1)),
]


def parse_bounds(formula: str) -> tuple[float, float, int] | None:
    """(нижняя граница, верхняя граница, полярность) из формулы нормирования."""
    f = formula.replace(" ", "")
    for pattern, extract in _PATTERNS:
        m = pattern.match(f)
        if m:
            groups = [g.replace(",", ".") for g in m.groups()]
            return extract([None] + groups)
    return None


def extract_normalization(ws_formulas) -> dict:
    """Границы нормирования из последнего блока листа."""
    starts = _block_starts(ws_formulas)
    if not starts:
        return {}
    for block_start in reversed(starts):
        row = block_start + FIRST_TERRITORY_OFFSET
        cell = ws_formulas.cell(row=row, column=2).value
        if isinstance(cell, str) and cell.startswith("="):
            parsed = parse_bounds(cell)
            if parsed:
                lo, hi, polarity = parsed
                return {
                    "low": lo, "high": hi, "polarity": polarity,
                    "formula": cell, "block_row": row,
                }
    return {}


def main() -> None:
    p = argparse.ArgumentParser(description="Импорт расчётной книги индекса УР")
    p.add_argument("--workbook", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--dry-run", action="store_true", help="Показать разбор, файл не писать")
    args = p.parse_args()

    path = Path(args.workbook)
    wb_v = openpyxl.load_workbook(path, data_only=True)
    wb_f = openpyxl.load_workbook(path, data_only=False)

    result: dict = {
        "schema_version": "1.0.0",
        "source_file": path.name,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "bounds_edition": "2014-2024",
        "bounds_origin": (
            "Разобраны из формул расчётной книги. Таблица границ обещана "
            "заказчиком в новой редакции методики (ответ на замечание 1); "
            "до её выпуска книга — единственный первоисточник."
        ),
        "aggregation": {
            "within_block": "weighted_sum",
            "between_blocks": "arithmetic_mean",
            "region_value": "unweighted_mean_of_territories",
        },
        "blocks": {},
        "aux": {},
        "published_indices": {},
    }

    # Окно лет задаёт лист «Индексы»: считать нужно ровно то, что опубликовано.
    # В листах показателей встречается столбец 2025, но заполнен он лишь у части
    # показателей, а индексов за 2025 год в книге нет.
    ws_idx_head = wb_v[INDEX_SHEET]
    window = _years(ws_idx_head, _block_starts(ws_idx_head)[0])
    result["years_window"] = window

    territories: list[str] = []
    years_ref: list[int] = []
    partial_years: dict[str, list[int]] = {}

    for block, sheets in INDICATOR_SHEETS.items():
        result["blocks"][block] = {"weights_sum": 0.0, "indicators": {}}
        for sheet_name, spec in sheets.items():
            ws_v, ws_f = wb_v[sheet_name], wb_f[sheet_name]
            starts = _block_starts(ws_v)
            if not starts:
                sys.exit(f"Лист «{sheet_name}»: не найдена шапка годов")
            years = _years(ws_v, starts[0])
            extra = [y for y in years if y not in window]
            if extra:
                # Данные за годы вне опубликованного окна: фиксируем, но в
                # расчёт не берём — иначе индекс разойдётся с опубликованным.
                filled = [
                    y for y in extra
                    if any(
                        ws_v.cell(row=starts[0] + FIRST_TERRITORY_OFFSET + i,
                                  column=2 + years.index(y)).value is not None
                        for i in range(TERRITORY_COUNT)
                    )
                ]
                if filled:
                    partial_years[sheet_name] = filled
            raw = _trim_to_window(_read_block(ws_v, starts[0], years), years, window)
            normalized = _trim_to_window(_read_block(ws_v, starts[-1], years), years, window)
            # Вход нормирования: предпоследний блок, если ступеней больше одной
            # (приведение на душу населения или на единицу площади), иначе сырые
            # значения. Нужен, чтобы проверять реализацию на каждой ступени, а не
            # только по конечному результату.
            pre_block = starts[-2] if len(starts) > 1 else starts[0]
            pre_normalized = _trim_to_window(_read_block(ws_v, pre_block, years), years, window)
            pre_formula = ws_f.cell(row=pre_block + FIRST_TERRITORY_OFFSET, column=2).value
            bounds = extract_normalization(ws_f)
            if not bounds:
                sys.exit(f"Лист «{sheet_name}»: не разобрана формула нормирования")

            if not territories:
                territories, years_ref = list(raw.keys()), window

            result["blocks"][block]["indicators"][spec["key"]] = {
                "sheet": sheet_name,
                "weight": spec["weight"],
                "unit": spec["unit"],
                "bounds": bounds,
                "transform_formula": pre_formula if isinstance(pre_formula, str) else None,
                "raw": raw,
                "pre_normalized": pre_normalized,
                "normalized_published": normalized,
            }
            result["blocks"][block]["weights_sum"] = round(
                result["blocks"][block]["weights_sum"] + spec["weight"], 6
            )

    for sheet_name, key in AUX_SHEETS.items():
        ws = wb_v[sheet_name]
        starts = _block_starts(ws)
        years = _years(ws, starts[0])
        entry = {
            "sheet": sheet_name,
            "series": _trim_to_window(_read_block(ws, starts[0], years), years, window),
        }
        if len(starts) > 1:
            # У численности второй блок — та же величина в тысячах; именно она
            # стоит в знаменателе подушевых показателей.
            entry["series_scaled"] = _trim_to_window(
                _read_block(ws, starts[-1], years), years, window,
            )
        result["aux"][key] = entry

    # Опубликованные индексы блоков — цель сверки нашей реализации.
    ws_idx = wb_v[INDEX_SHEET]
    for block_start, block in zip(_block_starts(ws_idx), ("social", "economic", "ecological", "total")):
        years = _years(ws_idx, block_start)
        result["published_indices"][block] = _trim_to_window(
            _read_block(ws_idx, block_start, years), years, window,
        )

    result["years"] = years_ref
    result["territories"] = territories

    print(f"Территорий: {len(territories)}, годов: {len(years_ref)} ({years_ref[0]}–{years_ref[-1]})")
    print("\nГраницы нормирования:")
    for block, spec in result["blocks"].items():
        print(f"  {block} (сумма весов {spec['weights_sum']}):")
        for key, ind in spec["indicators"].items():
            b = ind["bounds"]
            polarity = "стимулятор" if b["polarity"] > 0 else "дестимулятор"
            print(f"    {key:22s} {b['low']:>9.4g} … {b['high']:<9.4g} {polarity:13s} {b['formula']}")
    print("\nОпубликованные индексы:", ", ".join(
        f"{k} ({len(v)} терр.)" for k, v in result["published_indices"].items()
    ))

    if partial_years:
        print("\n⚠ Данные вне опубликованного окна (в расчёт не взяты):")
        for sheet, ys in partial_years.items():
            print(f"    {sheet}: {ys}")
        result["partial_years_outside_window"] = partial_years

    for block, spec in result["blocks"].items():
        if abs(spec["weights_sum"] - 1.0) > 1e-9:
            print(f"\n⚠ Сумма весов блока {block} = {spec['weights_sum']}, а должна быть 1,00")

    if args.dry_run:
        print("\n[dry-run] файл не записан")
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСправочник записан: {out} ({out.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
