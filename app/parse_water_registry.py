#!/usr/bin/env python3
"""
Парсит реестр водных объектов (озёра/реки СКО) в формат DSS.
НЕ использует universal_detector/convert_sheets — эта структура (список
объектов под заголовками секций, без временного ряда) принципиально не
подходит под их логику "район x год".

Вход:  путь к .xls/.xlsx файлу реестра (первый аргумент командной строки)
Выход: /app/artifacts/dss_isolated_water_registry_<ts>.xlsx

ОХВАТ (v2, все 16 листов):
  14 листов-районов: 6 категорий озёр/водоёмов, подсчёт сверяется со
    строкой "Всего водных объектов по X району: N" — при расхождении
    скрипт останавливается.
  Лист "3 района" (водоёмы Есильского/Мамлютского/Кызылжарского): все
    объекты отнесены к Мамлютскому району (kato 595200000). Основание:
    два из трёх упомянутых в данных населённых пунктов (с.Андреевка,
    с.Владимировка) по факту находятся в Мамлютском районе, оба в одном
    Андреевском сельском округе — совпадение не случайное.
  Лист "Реки" (64 реки, 8 из 14 районов): разделены на
    "Наличие крупной реки, Wр" (только Есіл/Ишим, 690 км по области —
    единственная река региона, подпадающая под официальный порог
    "большая река" >500 км) и "Наличие притоков реки" (все остальные,
    независимо от длины — они впадают в Есіл прямо или через другие
    реки, то есть являются притоками по смыслу термина).
"""
import sys
import re
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, "/app/etl")
from universal_detector import lookup_kato  # noqa: E402
from excel_builder import build_dss_excel  # noqa: E402

RIVER_SHEET = "Реки"
MAMLYUTSKY_OVERRIDE = {"3 района": 595200000}  # см. обоснование в docstring

SECTION_MAP = [
    ("более 100 га", "Озёр площадью более 100 га"),
    ("от 50 до 100", "Озёр площадью до 100 га"),
    ("от 30 до 50", "Озёр от 30 до 50 га"),
    ("малые озер", "Малых озёр"),
    ("болота", "Болот"),
    ("другие водоем", "Других водоёмов"),
]

DIRECTION = "water"
UNIT = "шт"

RIVER_MAJOR_NAME = "Наличие крупной реки, Wр"
RIVER_TRIB_NAME = "Наличие притоков реки"
RIVER_MAJOR_KEYWORDS = ("ишим", "есіл", "есил")


def match_section(text: str):
    t = text.lower()
    for kw, name in SECTION_MAP:
        if kw in t:
            return name
    return None


def parse_lake_sheet(df: pd.DataFrame):
    """Возвращает (counts: dict[indicator_name, int], total_declared: int|None)."""
    col0 = df[0].astype(str).fillna("")

    section_rows = []
    total_declared = None
    for i, val in enumerate(col0):
        v = val.strip()
        sec = match_section(v)
        if sec:
            section_rows.append((i, sec))
        m = re.search(r"Всего водных объект\w* по .*?:\s*(\d+)", v)
        if m:
            total_declared = int(m.group(1))

    counts = {}
    for idx, (start, sec) in enumerate(section_rows):
        end = section_rows[idx + 1][0] if idx + 1 < len(section_rows) else len(df)
        n = 0
        for j in range(start + 1, end):
            cell = str(df.iat[j, 0]).strip()
            if re.match(r"^\d+$", cell):
                n += 1
        counts[sec] = counts.get(sec, 0) + n

    return counts, total_declared


def is_river_district_header(v: str) -> bool:
    v = v.strip()
    if not v:
        return False
    vl = v.lower()
    return vl.endswith("район") or vl.startswith("район ") or vl.startswith("г.") or vl.startswith("г ")


def parse_river_sheet(df: pd.DataFrame):
    """Возвращает dict[район_name, (крупная_count, притоки_count)]."""
    col0 = df[0].astype(str).fillna("")
    col1 = df[1].astype(str).fillna("")

    district_rows = [(i, v.strip()) for i, v in enumerate(col0) if is_river_district_header(v)]

    counts = {}
    for idx, (start, district) in enumerate(district_rows):
        end = district_rows[idx + 1][0] if idx + 1 < len(district_rows) else len(df)
        krupnaya, pritoki = 0, 0
        for j in range(start + 1, end):
            c0 = col0.iloc[j].strip()
            if re.match(r"^\d+$", c0):
                name = col1.iloc[j].lower()
                if any(k in name for k in RIVER_MAJOR_KEYWORDS):
                    krupnaya += 1
                else:
                    pritoki += 1
        counts[district] = (krupnaya, pritoki)
    return counts


def main():
    if len(sys.argv) < 2:
        print("Использование: python parse_water_registry.py <путь_к_файлу.xls>")
        sys.exit(1)

    src_path = Path(sys.argv[1])
    if not src_path.exists():
        print(f"Файл не найден: {src_path}")
        sys.exit(1)

    xl = pd.read_excel(src_path, sheet_name=None, header=None)

    year = datetime.now().year
    period_start = f"{year}-01-01"
    period_end = f"{year}-12-31"

    def make_comment(extra: str = "") -> str:
        base = (
            f"Подсчитано автоматически из реестра водных объектов "
            f"({src_path.name}); снимок на {year} год, "
            "исходник не содержит временного ряда."
        )
        return f"{base} {extra}".strip()

    rows = []

    print("=== Озёра/водоёмы по районам ===")
    for sheet_name, df in xl.items():
        if sheet_name == RIVER_SHEET:
            continue

        counts, total_declared = parse_lake_sheet(df)
        total_counted = sum(counts.values())

        if total_declared is None:
            print(f"{sheet_name:30} !!! не найдена итоговая строка — пропуск листа")
            continue

        if total_counted != total_declared:
            print(f"{sheet_name:30} !!! РАСХОЖДЕНИЕ: посчитано={total_counted}, заявлено={total_declared}")
            print("Остановлено — считать дальше небезопасно, пока расхождение не объяснено.")
            sys.exit(1)

        if sheet_name in MAMLYUTSKY_OVERRIDE:
            kato = MAMLYUTSKY_OVERRIDE[sheet_name]
            extra_comment = f"Район определён вручную (см. docstring): исходный лист '{sheet_name}'."
        else:
            kato = lookup_kato(sheet_name)
            extra_comment = ""

        if kato is None:
            print(f"{sheet_name:30} !!! kato_code не найден для '{sheet_name}' — пропуск листа")
            continue

        print(f"{sheet_name:30} OK, посчитано={total_counted}, kato={kato}")
        for indicator_name, value in counts.items():
            rows.append({
                "indicator_name": indicator_name,
                "direction_code": DIRECTION,
                "unit_name": UNIT,
                "kato_code": kato,
                "period_type": "year",
                "period_start": period_start,
                "period_end": period_end,
                "value": value,
                "comment": make_comment(extra_comment),
            })

    if RIVER_SHEET in xl:
        print()
        print("=== Реки по районам ===")
        river_counts = parse_river_sheet(xl[RIVER_SHEET])
        total_rivers = 0
        for district, (krupnaya, pritoki) in river_counts.items():
            kato = lookup_kato(district)
            if kato is None:
                print(f"{district:30} !!! kato_code не найден — пропуск")
                continue
            print(f"{district:30} OK, крупная={krupnaya}, притоки={pritoki}, kato={kato}")
            total_rivers += krupnaya + pritoki

            comment = make_comment(
                "Крупная = только Есіл(Ишим) >500км (офиц. порог 'большая река'), "
                "остальные — притоки независимо от длины."
            )
            if krupnaya:
                rows.append({
                    "indicator_name": RIVER_MAJOR_NAME, "direction_code": DIRECTION,
                    "unit_name": UNIT, "kato_code": kato, "period_type": "year",
                    "period_start": period_start, "period_end": period_end,
                    "value": krupnaya, "comment": comment,
                })
            if pritoki:
                rows.append({
                    "indicator_name": RIVER_TRIB_NAME, "direction_code": DIRECTION,
                    "unit_name": UNIT, "kato_code": kato, "period_type": "year",
                    "period_start": period_start, "period_end": period_end,
                    "value": pritoki, "comment": comment,
                })
        print(f"Всего рек учтено: {total_rivers}")

    if not rows:
        print("\nНи одной строки не собрано.")
        sys.exit(1)

    df_out = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_name = f"dss_isolated_water_registry_{ts}.xlsx"
    out_path = Path("/app/artifacts") / out_name
    build_dss_excel(df_out, str(out_path))

    print(f"\nВсего строк: {len(rows)}")
    print(f"Готово: {out_path}")


if __name__ == "__main__":
    main()
