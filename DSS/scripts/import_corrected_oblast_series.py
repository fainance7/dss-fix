#!/usr/bin/env python3
"""
Дополнение областных рядов в базе по исправленным статданным заказчика.

Файл с исправлениями (22.09.2026) закрыл замечания 15, 16 и 18. Но заливать его
в базу целиком нельзя: часть рядов в базе и в файле расходится, и расхождение —
это предмет арбитража, а не повод для перезаписи. Поэтому скрипт пишет только
то, чего в базе нет, а конфликты печатает и оставляет нетронутыми.

Что дополняется:
  • площадь пашни и земли сельскохозяйственного назначения за 2024 — в базе
    ряды обрывались на 2023;
  • посевная площадь на орошении за 2014-2024 — показателя в справочнике не
    было вовсе, хотя он входит в расчёт устойчивого развития.

Что НЕ дополняется:
  • посевная площадь масличных культур — база и файл расходятся на 18-48 % в
    2020, 2022, 2023 и 2024 годах (наше замечание 13, ответа нет). Пока ряды не
    сверены, прогноз по любому из них будет прогнозом спорных чисел.

По умолчанию режим проверки: печатает план и ничего не пишет.

    backend/.venv/bin/python scripts/import_corrected_oblast_series.py            # план
    backend/.venv/bin/python scripts/import_corrected_oblast_series.py --commit   # запись
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import openpyxl
import psycopg2

XLSX = Path(
    "data/research/new_stats/Испр_Статданные по  СКО за 2014-2024 гг "
    "для моделирования и оценки вариантов планирования по УР и эконом эффективности.xlsx"
)
SHEET = "Сельское хозяйство"
YEARS = list(range(2014, 2025))
OBLAST = "Северо-Казахстанская область"

# Файл даёт гектары, показатели базы заведены в тыс. га — отсюда `scale`.
SERIES: list[dict] = [
    {
        "row": 3,
        "indicator": "Площадь земель сельскохозяйственного назначения",
        "scale": 1e-3,
    },
    {
        "row": 4,
        "indicator": "Площадь пашни",
        "scale": 1e-3,
    },
    {
        "row": 9,
        "indicator": "Посевная площадь на орошении",
        "scale": 1e-3,
        "unit": "тыс.га",
        "direction": "land",
        "create": True,
        "note": "показателя не было в справочнике, хотя он входит в расчёт УР",
    },
]

# Ряд, расхождение по которому ждёт арбитража: печатается, но не пишется.
CONFLICTING = {"row": 7, "indicator": "Посевная площадь масличных культур",
               "scale": 1e-3, "remark": 13}

REL_TOLERANCE = 0.005  # расхождение ниже — округление, а не разные данные


def _lit(value: str) -> str:
    """Строковый литерал SQL с экранированием апострофа."""
    return "'" + value.replace("'", "''") + "'"


def dsn() -> str:
    return os.environ.get(
        "DSS_DB_DSN",
        "host=127.0.0.1 port=5433 dbname=dss_db user=dss_user password=dss_local_password",
    )


def read_row(ws, row: int, scale: float) -> dict[int, float]:
    out = {}
    for i, year in enumerate(YEARS):
        v = ws.cell(row=row, column=2 + i).value
        if isinstance(v, (int, float)):
            out[year] = round(float(v) * scale, 6)
    return out


def existing_values(cur, name: str) -> dict[int, float]:
    cur.execute(
        """
        SELECT EXTRACT(YEAR FROM iv.period_start)::int, iv.value
        FROM indicator_values iv
        JOIN indicators i ON i.id = iv.indicator_id
        JOIN regions r ON r.id = iv.region_id AND r.level = 1
        WHERE i.name = %s AND EXTRACT(YEAR FROM iv.period_start) BETWEEN %s AND %s
        """,
        (name, YEARS[0], YEARS[-1]),
    )
    return {y: float(v) for y, v in cur.fetchall()}


def main() -> None:
    p = argparse.ArgumentParser(description="Дополнение областных рядов по исправленным данным")
    p.add_argument("--commit", action="store_true", help="Записать в базу")
    p.add_argument("--sql", metavar="FILE",
                   help="Выгрузить план как SQL для применения на другом сервере "
                        "(в прод-контейнере нет openpyxl, а таскать туда книгу незачем)")
    args = p.parse_args()

    ws = openpyxl.load_workbook(XLSX, data_only=True)[SHEET]
    conn = psycopg2.connect(dsn())
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT id FROM regions WHERE name = %s AND level = 1", (OBLAST,))
    found = cur.fetchone()
    if not found:
        sys.exit(f"В базе нет области «{OBLAST}»")
    region_id = found[0]

    cur.execute("SELECT name, id FROM units")
    units = dict(cur.fetchall())
    cur.execute("SELECT code, id FROM directions")
    directions = dict(cur.fetchall())
    cur.execute("SELECT name, id FROM indicators")
    indicators = dict(cur.fetchall())

    planned = 0
    sql_lines: list[str] = []
    for spec in SERIES:
        name = spec["indicator"]
        from_file = read_row(ws, spec["row"], spec["scale"])

        if spec.get("create"):
            # Создание показателя идёт в SQL всегда, а не только когда его нет
            # в локальной базе: на целевом сервере его может не быть.
            sql_lines.append(
                "-- {note}\n"
                "INSERT INTO indicators (id, direction_id, unit_id, name, sort_order,"
                " is_active, created_at)\n"
                "SELECT gen_random_uuid(), d.id, u.id, {name}, 999, true, now()\n"
                "FROM directions d, units u\n"
                "WHERE d.code = {direction} AND u.name = {unit}\n"
                "  AND NOT EXISTS (SELECT 1 FROM indicators WHERE name = {name});".format(
                    note=spec["note"], name=_lit(name),
                    direction=_lit(spec["direction"]), unit=_lit(spec["unit"]),
                )
            )

        indicator_id = indicators.get(name)
        if indicator_id is None:
            if not spec.get("create"):
                print(f"⚠ {name}: показателя нет в базе и создание не разрешено — пропуск")
                continue
            unit_id = units.get(spec["unit"])
            if unit_id is None:
                sys.exit(f"{name}: единицы «{spec['unit']}» нет в справочнике")
            print(f"+ показатель «{name}» ({spec['unit']}, {spec['direction']}) — {spec['note']}")
            if args.commit:
                cur.execute(
                    "INSERT INTO indicators (id, direction_id, unit_id, name, sort_order,"
                    " is_active, created_at)"
                    " VALUES (gen_random_uuid(), %s, %s, %s, 999, true, now()) RETURNING id",
                    (directions[spec["direction"]], unit_id, name),
                )
                indicator_id = cur.fetchone()[0]

        in_db = existing_values(cur, name) if indicator_id else {}
        to_write = {y: v for y, v in from_file.items() if y not in in_db}
        conflicts = {
            y: (v, in_db[y]) for y, v in from_file.items()
            if y in in_db and abs(v - in_db[y]) / max(abs(in_db[y]), 1e-9) > REL_TOLERANCE
        }

        years_text = ", ".join(str(y) for y in sorted(to_write)) or "—"
        print(f"  {name}: дополняется {len(to_write)} знач. ({years_text})")
        for y, (f, d) in sorted(conflicts.items()):
            print(f"    ! конфликт {y}: файл {f:.1f}, база {d:.1f} — оставлено значение базы")
        planned += len(to_write)

        # В SQL идут ВСЕ значения ряда из файла, а не только недостающие в
        # локальной базе: выгрузка предназначена для другого сервера, состояние
        # которого здесь неизвестно. Защиту от перезаписи даёт NOT EXISTS —
        # существующие значения останутся как есть, чьими бы они ни были.
        for year, value in sorted(from_file.items()):
            sql_lines.append(
                "INSERT INTO indicator_values (id, indicator_id, region_id, period_type,"
                " period_start, period_end, value, validation_status, created_at)\n"
                "SELECT gen_random_uuid(), i.id, r.id, 'year', DATE {start}, DATE {end},"
                " {value}, 'approved', now()\n"
                "FROM indicators i, regions r\n"
                "WHERE i.name = {name} AND r.name = {region} AND r.level = 1\n"
                "  AND NOT EXISTS (\n"
                "    SELECT 1 FROM indicator_values v WHERE v.indicator_id = i.id\n"
                "      AND v.region_id = r.id AND v.period_start = DATE {start});".format(
                    start=_lit(f"{year}-01-01"), end=_lit(f"{year}-12-31"),
                    value=repr(float(value)), name=_lit(name), region=_lit(OBLAST),
                )
            )

        if args.commit and indicator_id and to_write:
            for year, value in sorted(to_write.items()):
                cur.execute(
                    "INSERT INTO indicator_values (id, indicator_id, region_id, period_type,"
                    " period_start, period_end, value, validation_status, created_at)"
                    " VALUES (gen_random_uuid(), %s, %s, 'year', %s, %s, %s, 'approved', now())",
                    (indicator_id, region_id, date(year, 1, 1), date(year, 12, 31), value),
                )

    # Ряд, ожидающий арбитража: показываем масштаб расхождения, но не пишем.
    from_file = read_row(ws, CONFLICTING["row"], CONFLICTING["scale"])
    in_db = existing_values(cur, CONFLICTING["indicator"])
    print(f"\n{CONFLICTING['indicator']} — замечание {CONFLICTING['remark']}, "
          f"ряд не импортируется:")
    for year in YEARS:
        f, d = from_file.get(year), in_db.get(year)
        if f is None or d is None:
            continue
        rel = abs(f - d) / max(abs(d), 1e-9)
        if rel > REL_TOLERANCE:
            print(f"    {year}: файл {f:7.1f}, база {d:7.1f}  ({(f - d) / d * 100:+.1f} %)")

    if args.sql:
        header = (
            "-- Дополнение областных рядов земель по исправленным статданным "
            "заказчика (22.09.2026).\n"
            "-- Сгенерировано scripts/import_corrected_oblast_series.py --sql\n"
            "-- Пишет только отсутствующие значения: каждый INSERT защищён NOT EXISTS,\n"
            "-- поэтому повторный прогон ничего не меняет и ничего не перезаписывает.\n"
            "BEGIN;\n\n"
        )
        Path(args.sql).write_text(
            header + "\n\n".join(sql_lines) + "\n\nCOMMIT;\n", encoding="utf-8")
        print(f"\nSQL выгружен: {args.sql} ({len(sql_lines)} операторов)")

    print(f"\nВсего к записи: {planned} значений")
    if args.commit:
        conn.commit()
        print("Записано в базу")
    else:
        conn.rollback()
        print("[режим проверки] в базу ничего не записано — добавьте --commit")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
