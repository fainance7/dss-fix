"""
Генератор Excel-шаблона для систематизированной загрузки исторических данных.

Результат: manuals/data_intake_template.xlsx (6 листов, включая справочник метеостанций).
Запуск:    backend/.venv/bin/python scripts/generate_intake_template.py [--api-base http://localhost:8000]

При наличии работающего DSS API скрипт подтянет справочники indicators/units/kato.
Без --api-base сохранит шаблон с пустыми листами Indicators/KATO — заполнить их можно потом.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import json
from pathlib import Path

import psycopg2
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "manuals" / "data_intake_template.xlsx"

VALUES_HEADER = [
    "indicator_name",
    "direction_code",
    "unit_name",
    "kato_code",
    "station",
    "period_type",
    "period_start",
    "period_end",
    "value",
    "comment",
]

PERIOD_TYPES = ["year", "quarter", "month", "day", "dekad"]

HEADER_FILL = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")
HEADER_FONT = Font(bold=True)


def fetch_from_db() -> tuple[list[dict], list[dict], list[dict]]:
    """Прямой коннект к Postgres. Возвращает (indicators, kato).

    Использует переменные окружения POSTGRES_HOST/PORT/USER/PASSWORD/DB
    (как и backend/app/config.py); DSS dev: localhost:5433/dss_db_test НЕ нужен —
    шаблон должен генериться против рабочей БД (dss_db dev или appdb prod).
    """
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5433")),
        user=os.environ.get("POSTGRES_USER", "dss_user"),
        password=os.environ.get("POSTGRES_PASSWORD", "dss_local_password"),
        dbname=os.environ.get("POSTGRES_DB", "dss_db"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.code, i.name, u.name
                FROM indicators i
                JOIN directions d ON d.id = i.direction_id
                JOIN units      u ON u.id = i.unit_id
                WHERE i.is_active = TRUE
                ORDER BY d.code, i.sort_order, i.name
                """
            )
            indicators = [
                {"direction_code": d, "name": n, "unit_name": u}
                for d, n, u in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT kato_code, name_ru, level
                FROM kato_reference
                WHERE is_active = TRUE
                ORDER BY level, kato_code
                """
            )
            kato = [
                {"kato_code": code, "name_ru": name, "level": lvl}
                for code, name, lvl in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT s.name, r.name FROM meteo_stations s JOIN regions r ON r.id = s.region_id
                WHERE s.is_active ORDER BY s.name
                """
            )
            stations = [{"name": n, "region": r} for n, r in cur.fetchall()]
        return indicators, kato, stations
    finally:
        conn.close()


def fetch_json(base: str, path: str) -> list[dict]:
    url = base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"warn: не удалось получить {url}: {e}", file=sys.stderr)
        return []
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return data if isinstance(data, list) else []


def write_header(ws, headers: list[str]) -> None:
    for col, name in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="left")
        ws.column_dimensions[get_column_letter(col)].width = max(15, len(name) + 4)


def build_template(indicators: list[dict], kato: list[dict], stations: list[dict] | None = None) -> Workbook:
    wb = Workbook()

    # README
    readme = wb.active
    readme.title = "README"
    readme.append(["Шаблон загрузки исторических данных DSS"])
    readme["A1"].font = Font(bold=True, size=14)
    readme.append([])
    readme.append([
        "1. Заполняйте лист Values: одна строка = одно значение показателя за один период по одному региону."
    ])
    readme.append([
        "2. Поля indicator_name + direction_code + unit_name берите со справочника на листе Indicators."
    ])
    readme.append([
        "3. Поле kato_code берите со справочника на листе KATO (9-значный код, соответствующий региону СКО)."
    ])
    readme.append([
        "4. period_type — одно из: year, quarter, month, day, dekad. period_start / period_end — даты YYYY-MM-DD."
    ])
    readme.append([
        "   Декадные данные метеостанций (средняя температура воздуха, сумма осадков): заполните колонку "
        "station названием станции с листа Stations, kato_code оставьте пустым, period_type = dekad."
    ])
    readme.append([
        "   Декады: 1-я — с 1 по 10 число, 2-я — с 11 по 20, 3-я — с 21 по последний день месяца "
        "(например, 2025-02-21 … 2025-02-28). Месячные значения не загружаются — Система считает их сама:"
    ])
    readme.append([
        "   температура за месяц = (d1·10 + d2·10 + d3·(дней в месяце − 20)) / дней в месяце; осадки за месяц = d1 + d2 + d3."
    ])
    readme.append([
        "5. Лист Source: укажите название источника, URL и описание; на DSS они попадут в data_sources."
    ])
    readme.append([
        "6. После заполнения загрузите файл в DSS на странице «Импорт данных» (требуется учётная запись)."
    ])
    readme.column_dimensions["A"].width = 110

    # Indicators (read-only справочник)
    ws_ind = wb.create_sheet("Indicators")
    write_header(ws_ind, ["direction_code", "indicator_name", "unit_name"])
    for ind in indicators:
        ws_ind.append([
            ind.get("direction_code", ""),
            ind.get("name", ""),
            ind.get("unit_name", ""),
        ])
    ws_ind.column_dimensions["B"].width = 70

    # KATO (read-only справочник)
    ws_kato = wb.create_sheet("KATO")
    write_header(ws_kato, ["kato_code", "name_ru", "level"])
    for k in kato:
        ws_kato.append([
            k.get("kato_code", ""),
            k.get("name_ru", ""),
            k.get("level", ""),
        ])
    ws_kato.column_dimensions["B"].width = 50

    # Stations (read-only справочник метеостанций)
    ws_st = wb.create_sheet("Stations")
    write_header(ws_st, ["station", "region"])
    for st in stations or []:
        ws_st.append([st.get("name", ""), st.get("region", "")])
    ws_st.column_dimensions["A"].width = 25
    ws_st.column_dimensions["B"].width = 35

    # Values (рабочий лист — сюда вписывают данные)
    ws_v = wb.create_sheet("Values")
    write_header(ws_v, VALUES_HEADER)

    last_ind_row = max(2, ws_ind.max_row)
    last_kato_row = max(2, ws_kato.max_row)

    dv_ind = DataValidation(
        type="list",
        formula1=f"=Indicators!$B$2:$B${last_ind_row}",
        allow_blank=True,
    )
    dv_dir = DataValidation(
        type="list",
        formula1=f"=Indicators!$A$2:$A${last_ind_row}",
        allow_blank=True,
    )
    dv_unit = DataValidation(
        type="list",
        formula1=f"=Indicators!$C$2:$C${last_ind_row}",
        allow_blank=True,
    )
    dv_kato = DataValidation(
        type="list",
        formula1=f"=KATO!$A$2:$A${last_kato_row}",
        allow_blank=True,
    )
    dv_period = DataValidation(
        type="list",
        formula1='"' + ",".join(PERIOD_TYPES) + '"',
        allow_blank=True,
    )
    dv_station = DataValidation(
        type="list",
        formula1=f"=Stations!$A$2:$A${max(2, ws_st.max_row)}",
        allow_blank=True,
    )

    for dv in (dv_ind, dv_dir, dv_unit, dv_kato, dv_station, dv_period):
        ws_v.add_data_validation(dv)

    rng_max = 1000
    dv_ind.add(f"A2:A{rng_max}")
    dv_dir.add(f"B2:B{rng_max}")
    dv_unit.add(f"C2:C{rng_max}")
    dv_kato.add(f"D2:D{rng_max}")
    dv_station.add(f"E2:E{rng_max}")
    dv_period.add(f"F2:F{rng_max}")

    # Source (один источник на партию)
    ws_src = wb.create_sheet("Source")
    write_header(ws_src, ["source_name", "url", "description"])
    ws_src.column_dimensions["A"].width = 40
    ws_src.column_dimensions["B"].width = 50
    ws_src.column_dimensions["C"].width = 70

    return wb


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-db", action="store_true",
                    help="Брать справочники прямым SELECT из Postgres (надёжнее, чем API)")
    ap.add_argument("--api-base", default=None,
                    help="Альтернатива --from-db: HTTP API, например http://localhost:8000")
    ap.add_argument("--output", default=str(OUTPUT),
                    help=f"Куда писать xlsx (default {OUTPUT})")
    args = ap.parse_args()

    indicators: list[dict] = []
    kato: list[dict] = []
    stations: list[dict] = []
    if args.from_db:
        indicators, kato, stations = fetch_from_db()
    elif args.api_base:
        indicators = fetch_json(args.api_base, "/api/v1/indicators?is_active=true")
        kato = fetch_json(args.api_base, "/api/v1/kato?level=4")
        if isinstance(indicators, dict) and "items" in indicators:
            indicators = indicators["items"]
        if isinstance(kato, dict) and "items" in kato:
            kato = kato["items"]

    wb = build_template(indicators, kato, stations)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"saved {out} (indicators={len(indicators)}, kato={len(kato)})")


if __name__ == "__main__":
    main()
