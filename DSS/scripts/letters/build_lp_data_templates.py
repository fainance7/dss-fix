#!/usr/bin/env python3
"""Шаблоны запроса данных для алгоритма выбора варианта ЛП (13.10.2026).

Два файла Excel в docs/письма:
  * для ТОО «АЛДИС» — этапность, «класс ЛП → угодье», баланс вариантов, ряды угодий
    и категорий земель (лист Values в формате intake-portal), почвы;
  * для исследовательской группы — критерии модуля Б, попарные сравнения AHP,
    цена природы, земли запаса.

Ячейки, которые нужно заполнить, — жёлтые. Серые — наше допущение [Э] или пример:
их достаточно подтвердить или исправить. Числа берутся из справочников модуля
(lp_variant_presets.json, lp_compare.json), поэтому шаблон пересобирается этим
скриптом после пересчёта вариантов.

Запуск из корня репозитория:
    backend/.venv/bin/python scripts/letters/build_lp_data_templates.py
"""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "письма"
DATE = "2026-10-13"
PRESETS = ROOT / "ai-service/app/services/landscape_impact/reference/lp_variant_presets.json"
COMPARE = ROOT / "backend/app/data/lp_compare.json"

FILL_INPUT = PatternFill("solid", fgColor="FFF2CC")   # заполнить
FILL_OURS = PatternFill("solid", fgColor="E7E6E6")    # наше допущение / пример
FILL_HEAD = PatternFill("solid", fgColor="1F4E78")
FONT_HEAD = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")

DISTRICTS = [  # КАТО, имя в DSS, имя в пресетах вариантов
    ("593200000", "Айыртауский район", "Айыртауский район"),
    ("593400000", "Акжарский район", "Акжарский район"),
    ("595800000", "Аккайынский район", "Аккайынский район"),
    ("594200000", "Есильский район", "Есильский район"),
    ("594600000", "Жамбылский район", "Жамбылский район"),
    ("595000000", "Кызылжарский район", "Кызылжарский район"),
    ("595200000", "Мамлютский район", "Мамлютский район"),
    ("593600000", "Район Магжана Жумабаева", "Район Магжана Жумабаева"),
    ("595600000", "Район Шал акына", "Район Шал акына"),
    ("596600000", "Район им.Габита Мусрепова", "Район им. Габита Мусрепова"),
    ("596000000", "Тайыншинский район", "Тайыншинский район"),
    ("596200000", "Тимирязевский район", "Тимирязевский район"),
    ("596400000", "Уалихановский район", "Уалихановский район"),
]
CITY = ("591010000", "г. Петропавловск", "Петропавловск Г.А.")
VARIANTS = [("lp-v1-conservative", "V1 — природоохранный"),
            ("lp-v2-balanced", "V2 — сбалансированный"),
            ("lp-v3-development", "V3 — хозяйственно-ориентированный")]
LP_CLASSES = [(1, "регулируемое природопользование"), (2, "орошение"),
              (3, "талые снеговые воды"), (4, "пашня (богара)"), (5, "пастбищное")]


# ── оформление ──────────────────────────────────────────────────────

def header(ws, row: int, names: list[str], widths: list[int] | None = None) -> None:
    for j, name in enumerate(names, start=1):
        c = ws.cell(row=row, column=j, value=name)
        c.fill, c.font, c.alignment, c.border = FILL_HEAD, FONT_HEAD, WRAP, BORDER
    if widths:
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def put(ws, row: int, values: list, fills: dict[int, PatternFill] | None = None) -> None:
    for j, v in enumerate(values, start=1):
        c = ws.cell(row=row, column=j, value=v)
        c.border, c.alignment = BORDER, WRAP
        if fills and j in fills:
            c.fill = fills[j]


def title(ws, text: str, note: str | None = None) -> int:
    ws.cell(row=1, column=1, value=text).font = Font(bold=True, size=12)
    if note:
        ws.cell(row=2, column=1, value=note).alignment = Alignment(wrap_text=False)
        return 4
    return 3


def instructions(wb: Workbook, heading: str, lines: list[str]) -> None:
    ws = wb.active
    ws.title = "Как заполнять"
    ws.column_dimensions["A"].width = 120
    ws.cell(row=1, column=1, value=heading).font = Font(bold=True, size=13)
    r = 3
    for ln in lines:
        c = ws.cell(row=r, column=1, value=ln)
        c.alignment = WRAP
        if ln.startswith("■"):
            c.font = Font(bold=True)
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Заполнить").fill = FILL_INPUT
    ws.cell(row=r + 1, column=1, value="Наше допущение [Э] или пример — подтвердить или исправить").fill = FILL_OURS


# ── АЛДИС ───────────────────────────────────────────────────────────

def aldis_workbook(presets: dict) -> Workbook:
    wb = Workbook()
    instructions(wb, "Данные для алгоритма выбора варианта ЛП — ТОО «АЛДИС»", [
        "Файл приложен к письму GRAPH от 13.10.2026 «Требования к данным для алгоритма выбора варианта ЛП».",
        "",
        "■ Листы и сроки",
        "1_Этапность — когда реализуется каждый вариант (доля к 2027, 2030, 2035 гг.). Срок 22.10.2026.",
        "2_Класс_угодье — во что превращается каждое текущее угодье в каждом классе ЛП. Срок 22.10.2026.",
        "3_Баланс_вариантов — площадь пашни и пастбищ района при полной реализации варианта. Срок 22.10.2026.",
        "Values — ряды угодий и категорий земель по районам и годам в формате intake-portal. Срок: пашня — 19.10.2026, остальное — 22.10.2026.",
        "5_Почвы — состояние почв по районам за два последних тура обследования. Срок 05.11.2026.",
        "6_Вопросы — короткие ответы текстом. Срок 19.10.2026.",
        "",
        "■ Как работать с листами",
        "На листах 1–3 серым уже стоят наши оценки по наложению растров V1–V3 на маски категорий земель (01.10.2026).",
        "Если оценка верна — оставьте её и напишите «верно» в колонке ответа. Если нет — впишите ваше значение в жёлтую колонку.",
        "Лист Values можно загрузить в intake-portal как есть: строки уже заведены, нужно вписать value и источник в comment.",
        "Строки, по которым данных нет, удалите — пустой value портал считает ошибкой.",
        "Если какой-то пункт получить нельзя — напишите это в 6_Вопросы: под это изменим метод.",
        "",
        "■ Пример",
        "На каждом листе первая строка после заголовка — пример (серый, с пометкой ПРИМЕР). Её числа условные, перед отправкой удалите или перезапишите.",
    ])

    # 1. Этапность
    ws = wb.create_sheet("1_Этапность")
    r = title(ws, "Этапность реализации вариантов ЛП: доля от полного объёма изменения, нарастающим итогом, %",
              "Серым — объём изменения при полной реализации по нашему наложению [Э], тыс. га. Жёлтым — ваш ответ.")
    header(ws, r, ["Вариант", "Район", "КАТО", "Изменение", "Полный объём [Э], тыс. га",
                   "Полный объём АЛДИС, тыс. га (если иной)", "к 2027, %", "к 2030, %", "к 2035, %",
                   "Источник / комментарий"], [26, 28, 12, 44, 14, 16, 10, 10, 10, 36])
    changes = [("other_to_arable", "распашка земель, не являющихся пашней (класс 4)"),
               ("other_to_irrigated", "орошение земель, не являющихся пашней (класс 2)"),
               ("fields_to_pasture", "перевод полей в пастбища (класс 5)"),
               ("fields_to_regulated", "вывод полей под регулируемое природопользование (класс 1)")]
    r += 1
    put(ws, r, ["ПРИМЕР: V2 — сбалансированный", "Айыртауский район", "593200000", changes[0][1],
                presets["lp-v2-balanced"]["districts"]["Айыртауский район"]["components"]["other_to_arable"],
                None, 20, 60, 100, "ПРИМЕР: условные доли; программа развития АПК района"],
        {j: FILL_OURS for j in range(1, 11)})
    for code, label in VARIANTS:
        for kato, name, pname in DISTRICTS:
            comp = presets[code]["districts"][pname]["components"]
            for key, text in changes:
                if not comp.get(key):
                    continue
                r += 1
                put(ws, r, [label, name, kato, text, comp[key], None, None, None, None, None],
                    {5: FILL_OURS, 6: FILL_INPUT, 7: FILL_INPUT, 8: FILL_INPUT, 9: FILL_INPUT, 10: FILL_INPUT})
    ws.cell(row=r + 2, column=1, value="Если этапность одинакова для всех районов варианта, достаточно заполнить одну строку на вариант "
            "и написать в комментарии «для всех районов».").alignment = Alignment(wrap_text=False)

    # 2. Класс → угодье
    ws = wb.create_sheet("2_Класс_угодье")
    r = title(ws, "Что происходит с текущим угодьем в каждом классе ЛП при полной реализации",
              "Серым — правило, по которому сейчас считает DSS [Э]. Подтвердите («верно») или впишите своё: целевое угодье и долю площади, %.")
    header(ws, r, ["Класс ЛП", "Название класса", "Текущее угодье / категория", "Наше правило [Э]",
                   "Ответ АЛДИС: «верно» или целевое угодье", "Доля площади, переходящей в целевое, %",
                   "Комментарий"], [9, 30, 30, 44, 34, 16, 36])
    current = ["пашня", "залежь", "пастбища", "сенокосы", "многолетние насаждения",
               "лес (лесной фонд)", "ООПТ", "водный фонд", "населённые пункты, дороги", "прочие земли"]
    rule = {
        1: {"пашня": "выбывает из пашни (регулируемое природопользование)"},
        2: {"залежь": "орошаемая пашня", "пастбища": "орошаемая пашня", "сенокосы": "орошаемая пашня",
            "прочие земли": "орошаемая пашня"},
        4: {"залежь": "пашня", "пастбища": "пашня", "сенокосы": "пашня", "прочие земли": "пашня"},
        5: {"пашня": "пастбища"},
    }
    keep = {"лес (лесной фонд)", "ООПТ", "водный фонд", "населённые пункты, дороги"}
    r += 1
    put(ws, r, [4, "пашня (богара)", "залежь", "пашня", "ПРИМЕР: пашня", 60,
                "ПРИМЕР: 40 % залежи в классе 4 остаётся залежью — засолённые участки"],
        {j: FILL_OURS for j in range(1, 8)})
    for cls, cname in LP_CLASSES:
        for cur in current:
            if cur in keep:
                ours = "без изменений"
            else:
                ours = rule.get(cls, {}).get(cur, "без изменений")
            r += 1
            put(ws, r, [cls, cname, cur, ours, None, None, None],
                {4: FILL_OURS, 5: FILL_INPUT, 6: FILL_INPUT, 7: FILL_INPUT})
    ws.cell(row=r + 2, column=1, value="Класс 3 «талые снеговые воды» на полном разрешении растра почти пуст (0,5–2,4 тыс. га). "
            "Если так задумано — напишите в комментарии.").alignment = Alignment(wrap_text=False)

    # 3. Баланс вариантов
    ws = wb.create_sheet("3_Баланс_вариантов")
    r = title(ws, "Изменение угодий района при полной реализации варианта, тыс. га (+ рост, − сокращение)",
              "Серым — наша оценка [Э]. Если у вас есть земельный баланс вариантов — впишите его в жёлтые колонки.")
    header(ws, r, ["Вариант", "Район", "КАТО", "Пашня [Э]", "Пастбища [Э]", "Пашня АЛДИС",
                   "Пастбища АЛДИС", "Залежь АЛДИС", "Сенокосы АЛДИС", "Категории земель меняются? (да/нет, какие)",
                   "Комментарий"], [26, 28, 12, 11, 11, 12, 12, 12, 12, 30, 32])
    r += 1
    put(ws, r, ["ПРИМЕР: V3 — хозяйственно-ориентированный", "Уалихановский район", "596400000", 387.3, -387.5,
                360.0, -380.0, 10.0, 10.0, "ПРИМЕР: 15 тыс. га из земель запаса в с/х назначение",
                "ПРИМЕР"], {j: FILL_OURS for j in range(1, 12)})
    for code, label in VARIANTS:
        for kato, name, pname in [*DISTRICTS, CITY]:
            d = presets[code]["districts"][pname]
            r += 1
            put(ws, r, [label, name, kato, d["arable"], d["pasture"], None, None, None, None, None, None],
                {4: FILL_OURS, 5: FILL_OURS, **{j: FILL_INPUT for j in range(6, 12)}})

    # 4. Values — intake-portal
    ws = wb.create_sheet("Values")
    cols = ["indicator_name", "direction_code", "unit_name", "kato_code", "period_type",
            "period_start", "period_end", "value", "comment"]
    header(ws, 1, cols, [44, 14, 10, 12, 10, 12, 12, 10, 60])
    series = [  # (показатель, годы, зачем)
        ("Площадь пашни", [*range(2004, 2011), *range(2022, 2026)], "п. 1.5: продлить ряд 2011–2021 гг."),
        ("Площадь пастбищ", range(2014, 2026), "п. 2.3: угодья по годам"),
        ("Сенокосы", range(2014, 2026), "п. 2.3"),
        ("Залежь", range(2014, 2026), "п. 2.3"),
        ("Многолетние насаждения", range(2014, 2026), "п. 2.3"),
        ("Площадь земель сельскохозяйственного назначения", range(2014, 2026), "п. 2.4: категории по годам"),
        ("Площадь земель населённых пунктов", range(2014, 2026), "п. 2.4"),
        ("Земли промышленности, транспорта, связи и иного несельскохозяйственного назначения",
         range(2014, 2026), "п. 2.4"),
        ("Земли особо охраняемых природных территорий", range(2014, 2026), "п. 2.4 / 2.8: ООПТ по районам"),
        ("Площадь земель водного фонда", range(2014, 2026), "п. 2.4"),
        ("Площадь земель запаса", range(2014, 2026), "п. 2.4"),
    ]
    put(ws, 2, ["Площадь пашни", "land", "тыс.га", "593200000", "year", "2022-01-01", "2022-12-31", 429.5,
                "ПРИМЕР — удалить строку. Источник: сборник «Охрана окружающей среды СКО», табл. «Наличие земель по районам», на 1 ноября"],
        {j: FILL_OURS for j in range(1, 10)})
    r = 2
    for ind, years, why in series:
        for kato, _name, _p in DISTRICTS:
            for y in years:
                r += 1
                put(ws, r, [ind, "land", "тыс.га", kato, "year", f"{y}-01-01", f"{y}-12-31", None, None],
                    {8: FILL_INPUT, 9: FILL_INPUT})
    ws.cell(row=1, column=9).comment = Comment(
        "Источник значения: сборник, таблица, дата (например, «на 1 ноября»).", "GRAPH")
    src = wb.create_sheet("Source")
    header(src, 1, ["name", "url", "description"], [50, 50, 70])
    put(src, 2, ["ТОО «АЛДИС»: угодья и категории земель по районам", None,
                 "Ответ на требования GRAPH от 13.10.2026"], {1: FILL_INPUT, 2: FILL_INPUT, 3: FILL_INPUT})

    # 5. Почвы
    ws = wb.create_sheet("5_Почвы")
    r = title(ws, "Состояние почв по районам — два последних тура обследования",
              "Показатели DSS заведёт под ваши данные; формат свободный по годам, но одна строка — район × показатель × тур.")
    header(ws, r, ["Район", "КАТО", "Показатель", "Единица", "Год тура", "Значение", "Источник"],
           [28, 12, 52, 12, 10, 10, 44])
    r += 1
    put(ws, r, ["Айыртауский район", "593200000", "Эродированные земли (водная и ветровая эрозия)", "тыс. га", 2019,
                12.3, "ПРИМЕР: материалы почвенного обследования, Гипрозем"], {j: FILL_OURS for j in range(1, 8)})
    soil = [("Эродированные земли (водная и ветровая эрозия)", "тыс. га"), ("Засолённые земли", "тыс. га"),
            ("Переувлажнённые и заболоченные земли", "тыс. га"), ("Сбитые пастбища", "тыс. га"),
            ("Содержание гумуса в пахотном слое, среднее", "%"), ("Нарушенные земли", "тыс. га"),
            ("Рекультивированные земли", "тыс. га")]
    for _kato, name, _p in DISTRICTS[:1]:
        for ind, unit in soil:
            for tour in ("тур 1", "тур 2"):
                r += 1
                put(ws, r, [name, _kato, ind, unit, tour, None, None],
                    {5: FILL_INPUT, 6: FILL_INPUT, 7: FILL_INPUT})
    ws.cell(row=r + 2, column=1, value="Строки выше — для одного района как образец; остальные районы добавьте так же.").alignment = Alignment(wrap_text=False)

    # 6. Вопросы
    ws = wb.create_sheet("6_Вопросы")
    r = title(ws, "Короткие вопросы")
    header(ws, r, ["№", "Вопрос", "Зачем", "Ответ АЛДИС"], [6, 70, 50, 50])
    qs = [
        ("1.5а", "Источник среза угодий 2026 г. («Площадь пашни» 2026 г. в DSS) и что в нём считается пашней: с залежью и паром или без?",
         "срез и статистика 2021 г. расходятся до −50 % (Уалихановский 327 → 164 тыс. га); от этого зависит база сдвига вариантов"),
        ("1.3", "Население районов 2025 г. в DSS — на 1 января 2026 г.?",
         "стык 2024/2025 гг. −1,4…−5,8 % за год"),
        ("2.1а", "Есть ли у вариантов официальная программа реализации (документ, на который можно сослаться)?",
         "этапность [Э] или данные заказчика"),
        ("К3", "Если какой-то пункт получить нельзя — какой и почему?", "изменим метод под имеющиеся данные"),
    ]
    for q in qs:
        r += 1
        put(ws, r, [*q, None], {4: FILL_INPUT})
    return wb


# ── Исследовательская группа ────────────────────────────────────────

SAATY = "1 — равноценны; 3 — умеренно важнее; 5 — существенно; 7 — значительно; 9 — абсолютно; 2, 4, 6, 8 — промежуточные; 1/3, 1/5 … — обратное"


def ahp_block(ws, top: int, items: list[str], example: list[list[float]] | None, name: str) -> int:
    """Матрица попарных сравнений: верхний треугольник — ввод, нижний — 1/x, веса — геом. среднее."""
    n = len(items)
    ws.cell(row=top, column=1, value=name).font = Font(bold=True)
    hr = top + 1
    put(ws, hr, ["", *items, "Вес (геом. среднее)", "(A·w)ᵢ / wᵢ"])
    for j in range(1, n + 4):
        ws.cell(row=hr, column=j).fill, ws.cell(row=hr, column=j).font = FILL_HEAD, FONT_HEAD
    first = hr + 1
    full = f"{get_column_letter(2)}{first}:{get_column_letter(1 + n)}{first + n - 1}"
    ready = f"COUNT({full})={n * n}"  # веса и CR — только по заполненной матрице
    for i in range(n):
        row = first + i
        ws.cell(row=row, column=1, value=items[i]).border = BORDER
        for j in range(n):
            c = ws.cell(row=row, column=2 + j)
            c.border = BORDER
            if i == j:
                c.value = 1
            elif j > i:
                c.value = example[i][j] if example else None
                c.fill = FILL_OURS if example else FILL_INPUT
            else:
                src = ws.cell(row=first + j, column=2 + i).coordinate
                c.value = f'=IF({src}="","",1/{src})'
        rng = f"{get_column_letter(2)}{row}:{get_column_letter(1 + n)}{row}"
        ws.cell(row=row, column=n + 2).value = (
            f'=IF(NOT({ready}),"",IFERROR(GEOMEAN({rng})/(' + "+".join(
                f"GEOMEAN({get_column_letter(2)}{first + k}:{get_column_letter(1 + n)}{first + k})"
                for k in range(n)) + '),""))')
        ws.cell(row=row, column=n + 2).number_format = "0.000"
        wcol = get_column_letter(n + 2)
        prod = "+".join(f"{get_column_letter(2 + k)}{row}*{wcol}{first + k}" for k in range(n))
        ws.cell(row=row, column=n + 3).value = f'=IF(NOT({ready}),"",IFERROR(({prod})/{wcol}{row},""))'
        ws.cell(row=row, column=n + 3).number_format = "0.000"
    last = first + n - 1
    ri = {1: 0, 2: 0, 3: 0.58, 4: 0.90, 5: 1.12}[n]
    lam = f"AVERAGE({get_column_letter(n + 3)}{first}:{get_column_letter(n + 3)}{last})"
    ws.cell(row=last + 1, column=1, value="Согласованность CR (допустимо ≤ 0,10)").font = Font(italic=True)
    if ri:
        ws.cell(row=last + 1, column=n + 2).value = f'=IF(NOT({ready}),"",IFERROR(({lam}-{n})/({n}-1)/{ri},""))'
        ws.cell(row=last + 1, column=n + 2).number_format = "0.00"
    else:
        ws.cell(row=last + 1, column=n + 2, value="—")
    return last + 3


def research_workbook(compare: dict) -> Workbook:
    wb = Workbook()
    instructions(wb, "Решения для алгоритма выбора варианта ЛП — исследовательская группа", [
        "Файл приложен к письму GRAPH от 13.10.2026 «Требования к решениям исследовательской группы».",
        "Данные от АЛДИС уточняют числа, но выбрать вариант без этих решений нельзя: выбор между природой и продукцией — ценностный.",
        "",
        "■ Листы",
        "1_Критерии — какие из 8 критериев модуля Б оставить, их направление и границы нормирования.",
        "2_AHP_блоки — попарные сравнения трёх блоков: природа, хозяйственный эффект, реализуемость.",
        "3_AHP_внутри — попарные сравнения критериев внутри блоков «природа» и «хозяйственный эффект».",
        "Пример_AHP — заполненный образец с условными суждениями: как выглядит ответ и что получается.",
        "4_Цена_природы — цена 1 га земель высокой природной значимости в год или принцип её выбора.",
        "5_Запас — место земель запаса в индексе DSS.",
        "",
        "■ Как заполнять AHP",
        "Заполняются только жёлтые ячейки над диагональью: насколько строка важнее столбца по шкале Саати. "
        "Обратные значения, веса и коэффициент согласованности CR посчитаются сами. Если CR больше 0,10 — суждения противоречат друг другу, их стоит пересмотреть.",
        "Шкала: " + SAATY,
        "Если группа предпочитает задать веса напрямую (сумма 100) — впишите их в колонку «Вес напрямую» на листе 1_Критерии и не заполняйте AHP.",
    ])

    crit = compare["criteria"]
    ob = compare["oblast"]
    blocks = compare["blocks"]

    ws = wb.create_sheet("1_Критерии")
    r = title(ws, "Критерии модуля Б и их значения по области, тыс. га",
              "Серым — значения вариантов и предложенные границы (размах вариантов). Жёлтым — решение группы.")
    header(ws, r, ["id", "Блок", "Критерий", "Лучше", "V1", "V2", "V3", "Включить? (да/нет)",
                   "Нижняя граница нормирования", "Верхняя граница нормирования", "Вес напрямую (сумма 100)",
                   "Комментарий"], [16, 20, 56, 10, 9, 9, 9, 12, 14, 14, 12, 36])
    for c in crit:
        vals = [ob[v][c["id"]] for v in ("V1", "V2", "V3")]
        r += 1
        put(ws, r, [c["id"], blocks[c["block"]], c["label"], "меньше" if c["better"] == "lower" else "больше",
                    *vals, "да", min(vals), max(vals), None, None],
            {5: FILL_OURS, 6: FILL_OURS, 7: FILL_OURS, 8: FILL_INPUT, 9: FILL_INPUT, 10: FILL_INPUT,
             11: FILL_INPUT, 12: FILL_INPUT})
    r += 1
    put(ws, r, ["новый", "", "Предложите критерий, если нужен (например, по почвам — после данных АЛДИС к 05.11)",
                None, None, None, None, None, None, None, None, None], {3: FILL_INPUT, 12: FILL_INPUT})
    dv = DataValidation(type="list", formula1='"да,нет"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"H{r - len(crit)}:H{r}")

    names_b = list(blocks.values())
    nature = [c["label"] for c in crit if c["block"] == "nature"]
    economy = [c["label"] for c in crit if c["block"] == "economy"]

    ws = wb.create_sheet("2_AHP_блоки")
    ws.column_dimensions["A"].width = 40
    for j in range(2, 8):
        ws.column_dimensions[get_column_letter(j)].width = 18
    ws.cell(row=1, column=1, value="Попарное сравнение блоков. Шкала: " + SAATY).alignment = Alignment(wrap_text=False)
    ahp_block(ws, 3, names_b, None, "Насколько блок в строке важнее блока в столбце")

    ws = wb.create_sheet("3_AHP_внутри")
    ws.column_dimensions["A"].width = 60
    for j in range(2, 8):
        ws.column_dimensions[get_column_letter(j)].width = 18
    ws.cell(row=1, column=1, value="Попарное сравнение критериев внутри блоков. Шкала: " + SAATY).alignment = Alignment(wrap_text=False)
    nxt = ahp_block(ws, 3, nature, None, "Блок «Сохранение природы»")
    ahp_block(ws, nxt, economy, None, "Блок «Хозяйственный эффект»")

    ws = wb.create_sheet("Пример_AHP")
    ws.column_dimensions["A"].width = 60
    for j in range(2, 8):
        ws.column_dimensions[get_column_letter(j)].width = 18
    ws.cell(row=1, column=1, value="ПРИМЕР с условными суждениями — не решение. Природа умеренно важнее хозяйства (3) "
            "и существенно важнее реализуемости (5); хозяйство умеренно важнее реализуемости (3).").alignment = Alignment(wrap_text=False)
    nxt = ahp_block(ws, 3, names_b, [[1, 3, 5], [0, 1, 3], [0, 0, 1]], "Блоки (пример)")
    ahp_block(ws, nxt, nature, [[1, 2, 3, 3], [0, 1, 2, 2], [0, 0, 1, 1], [0, 0, 0, 1]],
              "Природа (пример): значимость ≥ уязвимость ≥ ООПТ = лес")

    ws = wb.create_sheet("4_Цена_природы")
    r = title(ws, "Цена 1 га земель высокой природной значимости, вовлекаемых в пашню, тыс. тг в год",
              "От неё зависит выбор между V1 и V3 при любых весах. Достаточно числа или принципа — число посчитаем сами.")
    header(ws, r, ["Что", "Значение", "Комментарий"], [56, 30, 70])
    rows = [
        ("Расчётные пороги DSS (отчёт от 06.10.2026)", "выше 80–107 → V1; ниже 40–66 → V3; между — V2",
         "при крайних допущениях: V1 выше 60–133, V3 ниже 27–88", FILL_OURS),
        ("ПРИМЕР: принцип", "кадастровая (нормативная) стоимость земель по району",
         "ПРИМЕР: постановление о базовых ставках платы за землю", FILL_OURS),
        ("Цена, тыс. тг/га в год", None, None, FILL_INPUT),
        ("или принцип выбора цены", None, None, FILL_INPUT),
        ("Источник (документ, методика)", None, None, FILL_INPUT),
        ("Ставка дисконтирования, % (если цена разовая)", None, None, FILL_INPUT),
    ]
    for a, b, c, fill in rows:
        r += 1
        put(ws, r, [a, b, c], {2: fill, 3: fill})
    dv = DataValidation(type="list", allow_blank=True, formula1='"кадастровая стоимость,упущенные экосистемные услуги,'
                        'плата за изъятие / компенсационное восстановление,экспертная оценка группы,другое"')
    ws.add_data_validation(dv)
    dv.add(f"B{r - 2}")

    ws = wb.create_sheet("5_Запас")
    r = title(ws, "Земли запаса в индексе DSS",
              "С 12.10.2026 (индекс 1.1.0) запас исключён из экологического блока: знак «−» в версии 1.0.0 был допущением без источника.")
    header(ws, r, ["Вопрос", "Варианты ответа", "Ответ группы"], [56, 60, 40])
    qs = [("Входит ли запас в экологический блок?",
           "нет (как сейчас) / да, со знаком «+» (природный резерв) / да, со знаком «−» / по составу: залежь, степь, нарушенные земли раздельно"),
          ("Если «по составу» — источник состава запаса по районам", "документ / АЛДИС / нет данных"),
          ("Влияние на вердикт", "200 тыс. га из запаса в с/х: «−» → способствует УР; исключён → не подтверждено; «+» → препятствует, экологическое вето")]
    for q, opts in qs:
        r += 1
        put(ws, r, [q, opts, None], {2: FILL_OURS, 3: FILL_INPUT})
    return wb


def main() -> None:
    presets = json.loads(PRESETS.read_text(encoding="utf-8"))["variants"]
    compare = json.loads(COMPARE.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    a = OUT / f"Шаблон_АЛДИС_данные_для_выбора_ЛП_{DATE}.xlsx"
    b = OUT / f"Шаблон_ИГ_решения_для_выбора_ЛП_{DATE}.xlsx"
    aldis_workbook(presets).save(a)
    research_workbook(compare).save(b)
    print(a.relative_to(ROOT))
    print(b.relative_to(ROOT))


if __name__ == "__main__":
    main()
