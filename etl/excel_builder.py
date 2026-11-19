# etl/excel_builder.py
"""
Сборка финального .xlsx по формату DSS:
5 листов: README, Indicators, KATO, Values, Source
"""
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from datetime import datetime


REQUIRED = [
    "indicator_name", "direction_code", "unit_name",
    "kato_code", "period_type", "period_start", "period_end", "value", "comment"
]

README_TEXT = """
Лист Values     — данные для загрузки
Лист Indicators — справочник показателей
Лист KATO       — справочник районов
Лист Source     — источники данных
"""


def build_dss_excel(df: pd.DataFrame, output_path: str) -> str:
    values_df = df[REQUIRED].copy()
    values_df["kato_code"] = values_df["kato_code"].astype(int)
    values_df["value"]     = pd.to_numeric(values_df["value"], errors="coerce")
    values_df["period_start"] = pd.to_datetime(
        values_df["period_start"]).dt.strftime("%Y-%m-%d")
    values_df["period_end"] = pd.to_datetime(
        values_df["period_end"]).dt.strftime("%Y-%m-%d")

    indicators_df = (
        df[["direction_code", "indicator_name", "unit_name"]]
        .drop_duplicates()
        .sort_values(["direction_code", "indicator_name"])
        .reset_index(drop=True)
    )

    _KATO_REF = {
        590000000: ("Северо-Казахстанская область", 1),
        591000000: ("Петропавловск Г.А.", 2),
        593200000: ("Айыртауский район", 2),
        593400000: ("Акжарский район", 2),
        593600000: ("Район Магжана Жумабаева", 2),
        594200000: ("Есильский район", 2),
        594600000: ("Жамбылский район", 2),
        595000000: ("Кызылжарский район", 2),
        595200000: ("Мамлютский район", 2),
        595600000: ("Район Шал акына", 2),
        595800000: ("Аккайынский район", 2),
        596000000: ("Тайыншинский район", 2),
        596200000: ("Тимирязевский район", 2),
        596400000: ("Уалихановский район", 2),
        596600000: ("Район им.Габита Мусрепова", 2),
    }
    _kato_codes = (
        df[["kato_code"]].drop_duplicates()["kato_code"].astype(int).tolist()
    )
    kato_df = pd.DataFrame(
        [
            {
                "kato_code": c,
                "name_ru": _KATO_REF.get(c, ("", 2))[0],
                "level": _KATO_REF.get(c, ("", 2))[1],
            }
            for c in sorted(_kato_codes)
        ]
    )

    source_df = pd.DataFrame([{
        "source_name": "БНС РК / Управление статистики СКО / Open-Meteo",
        "url": "https://stat.gov.kz",
        "description": (
            f"Данные СКО. "
            f"Сформировано: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    }])

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Сначала создаём книгу через writer
        indicators_df.to_excel(writer, sheet_name="Indicators", index=False)
        kato_df.to_excel(writer,       sheet_name="KATO",       index=False)
        values_df.to_excel(writer,     sheet_name="Values",     index=False)
        source_df.to_excel(writer,     sheet_name="Source",     index=False)

        # README добавляем вручную
        wb = writer.book
        ws = wb.create_sheet("README", 0)
        ws["A1"] = "ФОРМАТ ЗАГРУЗКИ DSS"
        ws["A1"].font = Font(bold=True, size=14)
        for i, line in enumerate(README_TEXT.strip().split("\n"), start=2):
            ws[f"A{i}"] = line

        # Форматируем заголовки всех листов
        for sheet_name in ["Indicators", "KATO", "Values", "Source"]:
            _format_header(writer.sheets[sheet_name])

    print(f"Excel сохранён: {output_path} ({len(values_df)} строк)")
    return output_path


def _format_header(ws):
    header_fill = PatternFill("solid", fgColor="2E75B6")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill      = header_fill
        cell.font      = header_font
        cell.alignment = Alignment(horizontal="center")
