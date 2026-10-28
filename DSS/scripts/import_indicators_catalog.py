"""
Импорт каталога показателей из Excel-файла (название направления → имя + единица).

Файл-источник: data/Показатели по направлениям 12 мая 2026.xlsx
Каждая строка = индикатор; единица измерения зашита в имя после последней запятой.
Заголовки направлений сидят отдельной строкой (колонка B пустая).

Запуск (dev):
    backend/.venv/bin/python scripts/import_indicators_catalog.py [--dry-run] \
        [--file data/Показатели по направлениям 12 мая 2026.xlsx]

Идемпотентность: ON CONFLICT DO NOTHING по UNIQUE (direction_id, name) и units(name).
indicator_values не трогаем; orphans (есть в БД, нет в файле) только показываем.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

import psycopg2
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "data" / "Показатели по направлениям 12 мая 2026.xlsx"
REPORT_CSV = ROOT / "data" / f"indicator_import_report_{date.today().isoformat()}.csv"

DIRECTION_MAP: dict[str, str] = {
    "Водные ресурсы": "water",
    "Климат": "climate",
    "Социально-демографическое": "social_demog",
    "Транспорт": "transport",
    "Трудовые ресурсы": "labor",
    "Туризм": "tourism",
    "Экология": "ecology",
    "Экономические": "economy",
}

UNIT_ALIASES: dict[str, str] = {
    "млрд тенге": "млрд.тенге",
    "млн тенге": "млн.тенге",
    "тыс. га": "тыс.га",
    "тыс. тонн": "тыс.тонн",
    "кв. м": "м²",
    "м³/чел": "м³/чел",
}

EXPLICIT_MATCH: dict[tuple[str, str], str] = {
    ("economy", "Доля населения с доходами ниже прожиточного минимума"):
        "Доля населения, имеющие доходы ниже величины прожиточного уровня",
    ("economy", "Среднегодовая доля занятых в экономике"):
        "Среднегодовая доля занятых в экономике в общей численности активного населения",
    ("economy", "Индекс физического объёма ВРП"):
        "Индекс физического объема ВРП",
    ("economy", "Объём инвестиций в основной капитал"):
        "Объем инвестиций в основной капитал",
    ("economy", "Объём розничной торговли"):
        "Объем розничной торговли",
}

SPECIAL_ROWS: dict[str, tuple[str, str]] = {
    "Категория туристско-рекреационного потенциала района (1–4)":
        ("Категория туристско-рекреационного потенциала района", "балл (1–4)"),
    "Имантау-Шалкарская курортная зона: места, посетители, объём услуг":
        ("Имантау-Шалкарская курортная зона (комплексный показатель)", "ед."),
    "Площадь ООПТ, га (республиканского значения)":
        ("Площадь ООПТ (республиканского значения)", "га"),
    "Ёмкость номерного фонда (койко-места), ед.":
        ("Ёмкость номерного фонда", "койко-мест"),
    "Качество поверхностных вод (индекс/класс)":
        ("Качество поверхностных вод", "индекс/класс"),
    "Индекс качества воды (WQI / класс)":
        ("Индекс качества воды (WQI)", "класс"),
}


def normalize(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_name_unit(raw: str) -> tuple[str, str]:
    """Отделяет единицу измерения от названия. Берёт последний фрагмент после ', '."""
    if raw in SPECIAL_ROWS:
        return SPECIAL_ROWS[raw]
    if ", " not in raw:
        return raw.strip(), "ед."
    name, unit = raw.rsplit(", ", 1)
    return name.strip(), unit.strip()


def canon_unit(raw: str) -> str:
    return UNIT_ALIASES.get(raw, raw)


def parse_excel(path: Path) -> list[dict]:
    wb = load_workbook(path, data_only=True)
    ws = wb["Sheet1"]
    rows: list[dict] = []
    current_dir: str | None = None
    sort_counter: dict[str, int] = {}
    for row in ws.iter_rows(min_row=1, values_only=True):
        a, b = (row + (None, None))[:2]
        if a is None:
            continue
        a = str(a).strip()
        if a == "Показатели по направлениям":
            continue
        if b is None:
            code = DIRECTION_MAP.get(a)
            if code is None:
                print(f"WARN: unknown direction header {a!r}", file=sys.stderr)
            current_dir = code
            continue
        if current_dir is None:
            print(f"WARN: indicator before direction header: {a!r}", file=sys.stderr)
            continue
        name, unit_raw = split_name_unit(a)
        unit = canon_unit(unit_raw)
        sort_counter[current_dir] = sort_counter.get(current_dir, 0) + 1
        rows.append({
            "direction_code": current_dir,
            "name": name,
            "unit": unit,
            "status": str(b).strip(),
            "sort_order": sort_counter[current_dir] * 10,
            "raw": a,
        })
    return rows


def db_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5433")),
        user=os.environ.get("POSTGRES_USER", "dss_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "dss_db"),
    )


def load_directions(cur) -> dict[str, str]:
    cur.execute("SELECT code, id FROM directions")
    return {code: str(did) for code, did in cur.fetchall()}


def load_units(cur) -> dict[str, str]:
    cur.execute("SELECT name, id FROM units")
    return {name: str(uid) for name, uid in cur.fetchall()}


def load_db_indicators(cur, direction_ids: dict[str, str]) -> dict[str, dict[str, dict]]:
    """{direction_code: {normalized_name: {id, name, unit}}}"""
    rev_dir = {v: k for k, v in direction_ids.items()}
    cur.execute("""
        SELECT i.id, i.direction_id, i.name, u.name AS unit_name
        FROM indicators i
        JOIN units u ON u.id = i.unit_id
    """)
    out: dict[str, dict[str, dict]] = {code: {} for code in direction_ids}
    for ind_id, dir_id, name, unit in cur.fetchall():
        code = rev_dir.get(str(dir_id))
        if code is None:
            continue
        out[code][normalize(name)] = {
            "id": str(ind_id),
            "name": name,
            "unit": unit,
        }
    return out


def ensure_units(cur, needed: Iterable[str], existing: dict[str, str], dry_run: bool) -> dict[str, str]:
    missing = [u for u in needed if u not in existing]
    if not missing:
        return existing
    print(f"-> Будут добавлены единицы измерения ({len(missing)}): {missing}")
    if not dry_run:
        cur.executemany(
            "INSERT INTO units (name, description) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
            [(u, u) for u in missing],
        )
        cur.execute("SELECT name, id FROM units")
        return {name: str(uid) for name, uid in cur.fetchall()}
    # в dry-run возвращаем плейсхолдеры для печати; реальный INSERT не выполняем
    return {**existing, **{u: "(new)" for u in missing}}


def resolve_match(rec: dict, db_state: dict[str, dict[str, dict]]) -> dict | None:
    code = rec["direction_code"]
    bucket = db_state.get(code, {})
    explicit = EXPLICIT_MATCH.get((code, rec["name"]))
    if explicit:
        match = bucket.get(normalize(explicit))
        if match:
            return match
    return bucket.get(normalize(rec["name"]))


def upsert_indicators(
    cur,
    rows: list[dict],
    db_state: dict[str, dict[str, dict]],
    direction_ids: dict[str, str],
    unit_ids: dict[str, str],
    dry_run: bool,
) -> tuple[list[dict], list[dict]]:
    added: list[dict] = []
    existed: list[dict] = []
    for rec in rows:
        match = resolve_match(rec, db_state)
        if match is not None:
            existed.append({**rec, "db_name": match["name"], "db_unit": match["unit"]})
            continue
        added.append(rec)
        if dry_run:
            continue
        dir_id = direction_ids[rec["direction_code"]]
        unit_id = unit_ids.get(rec["unit"])
        if unit_id is None or unit_id == "(new)":
            raise RuntimeError(f"Unit {rec['unit']!r} not in DB (run without --dry-run первый раз)")
        cur.execute(
            """
            INSERT INTO indicators (direction_id, unit_id, name, sort_order)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (direction_id, name) DO NOTHING
            """,
            (dir_id, unit_id, rec["name"], rec["sort_order"]),
        )
    return added, existed


def find_orphans(rows: list[dict], db_state: dict[str, dict[str, dict]]) -> list[dict]:
    matched_db_ids: set[str] = set()
    for rec in rows:
        match = resolve_match(rec, db_state)
        if match:
            matched_db_ids.add(match["id"])
    orphans: list[dict] = []
    for code, bucket in db_state.items():
        for entry in bucket.values():
            if entry["id"] not in matched_db_ids:
                orphans.append({
                    "direction_code": code,
                    "name": entry["name"],
                    "unit": entry["unit"],
                })
    orphans.sort(key=lambda x: (x["direction_code"], x["name"]))
    return orphans


def print_report(added: list[dict], existed: list[dict], orphans: list[dict], file_total: int, dry_run: bool) -> None:
    title_suffix = " (DRY-RUN)" if dry_run else ""
    print()
    print(f"=== ДОБАВЛЕНО ({len(added)}){title_suffix} ===")
    for r in sorted(added, key=lambda x: (x["direction_code"], x["sort_order"])):
        print(f"  [{r['direction_code']:<12}] {r['name']:<70.70} ({r['unit']}) [status: {r['status']}]")
    print()
    print(f"=== УЖЕ БЫЛО ({len(existed)}) ===")
    for r in sorted(existed, key=lambda x: (x["direction_code"], x["name"])):
        suffix = ""
        if normalize(r["db_name"]) != normalize(r["name"]):
            suffix = f"  ← file: {r['name']}"
        unit_suffix = f"  (unit: db={r['db_unit']} / file={r['unit']})" if r["db_unit"] != r["unit"] else ""
        print(f"  [{r['direction_code']:<12}] {r['db_name']:<70.70}{suffix}{unit_suffix}")
    print()
    print(f"=== ЛИШНЕЕ В БД, нет в файле ({len(orphans)}) ===")
    for r in orphans:
        print(f"  [{r['direction_code']:<12}] {r['name']:<70.70} ({r['unit']})")
    print()
    print(
        f"ИТОГО: добавлено={len(added)}, уже было={len(existed)}, orphan={len(orphans)}, "
        f"всего в файле={file_total}"
    )


def write_csv(added: list[dict], existed: list[dict], orphans: list[dict]) -> None:
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bucket", "direction", "name", "unit", "status_or_note"])
        for r in added:
            w.writerow(["added", r["direction_code"], r["name"], r["unit"], r["status"]])
        for r in existed:
            note = ""
            if r["db_unit"] != r["unit"]:
                note = f"unit_diff db={r['db_unit']} file={r['unit']}"
            w.writerow(["existed", r["direction_code"], r["db_name"], r["db_unit"], note])
        for r in orphans:
            w.writerow(["orphan", r["direction_code"], r["name"], r["unit"], ""])
    print(f"CSV-отчёт: {REPORT_CSV}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.file.exists():
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 1

    rows = parse_excel(args.file)
    print(f"Прочитано из файла: {len(rows)} индикаторов")

    needed_units = sorted({r["unit"] for r in rows})

    conn = db_connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            direction_ids = load_directions(cur)
            unit_ids = load_units(cur)
            db_state = load_db_indicators(cur, direction_ids)

            unit_ids = ensure_units(cur, needed_units, unit_ids, args.dry_run)
            added, existed = upsert_indicators(cur, rows, db_state, direction_ids, unit_ids, args.dry_run)
            orphans = find_orphans(rows, db_state)

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print_report(added, existed, orphans, file_total=len(rows), dry_run=args.dry_run)
    write_csv(added, existed, orphans)
    return 0


if __name__ == "__main__":
    sys.exit(main())
