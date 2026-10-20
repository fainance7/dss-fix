"""
Регистрация 20 показателей «Среднемесячная номинальная ЗП по секторам экономики»
в каталоге показателей (таблица indicators) одним прогоном.

Источник имён — справочник секторов economic_sectors (колонка indicator_name),
чтобы не дублировать список и держать единый источник правды. Поэтому миграция
016_economic_sectors.sql должна быть применена ДО запуска.

Все показатели заводятся:
  * direction = labor  (Трудовые ресурсы)
  * unit      = тенге
  * sort_order = economic_sectors.sort_order
  * name      = economic_sectors.indicator_name

Идемпотентность: ON CONFLICT (direction_id, name) DO NOTHING — повторный запуск
ничего не дублирует. indicator_values не трогаются.

Запуск (dev):
    backend/.venv/bin/python scripts/register_wage_by_sector_indicators.py [--dry-run]

Подключение к БД — через переменные окружения POSTGRES_* (по умолчанию dev:
localhost:5433/dss_db). На prod задать соответствующие POSTGRES_*.
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg2

DIRECTION_CODE = "labor"
UNIT_NAME = "тенге"


def db_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5433")),
        user=os.environ.get("POSTGRES_USER", "dss_user"),
        password=os.environ.get("POSTGRES_PASSWORD", "dss_local_password"),
        dbname=os.environ.get("POSTGRES_DB", "dss_db"),
    )


def fetch_scalar(cur, sql: str, params: tuple) -> str | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    return str(row[0]) if row else None


def load_sector_indicators(cur) -> list[tuple[str, int]]:
    """[(indicator_name, sort_order), ...] из справочника секторов."""
    cur.execute(
        "SELECT indicator_name, sort_order FROM economic_sectors ORDER BY sort_order"
    )
    return [(name, int(order)) for name, order in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что будет добавлено, без записи в БД",
    )
    args = ap.parse_args()

    conn = db_connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            direction_id = fetch_scalar(
                cur, "SELECT id FROM directions WHERE code = %s", (DIRECTION_CODE,)
            )
            if direction_id is None:
                sys.exit(f"ОШИБКА: направление code='{DIRECTION_CODE}' не найдено в directions")

            unit_id = fetch_scalar(
                cur, "SELECT id FROM units WHERE name = %s", (UNIT_NAME,)
            )
            if unit_id is None:
                sys.exit(f"ОШИБКА: единица '{UNIT_NAME}' не найдена в units")

            sectors = load_sector_indicators(cur)
            if not sectors:
                sys.exit(
                    "ОШИБКА: справочник economic_sectors пуст. "
                    "Сначала примените migrations/016_economic_sectors.sql"
                )

            # Уже существующие показатели этого направления (по имени).
            cur.execute(
                "SELECT name FROM indicators WHERE direction_id = %s", (direction_id,)
            )
            existing_names = {r[0] for r in cur.fetchall()}

            added: list[str] = []
            skipped: list[str] = []
            for name, sort_order in sectors:
                if name in existing_names:
                    skipped.append(name)
                    continue
                added.append(name)
                if not args.dry_run:
                    cur.execute(
                        """
                        INSERT INTO indicators (direction_id, unit_id, name, sort_order)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (direction_id, name) DO NOTHING
                        """,
                        (direction_id, unit_id, name, sort_order),
                    )

            suffix = " (DRY-RUN)" if args.dry_run else ""
            print(f"Направление: {DIRECTION_CODE}  |  Единица: {UNIT_NAME}{suffix}")
            print(f"Всего в справочнике секторов: {len(sectors)}")
            print(f"Будет добавлено: {len(added)}" if args.dry_run else f"Добавлено: {len(added)}")
            for n in added:
                print(f"  + {n}")
            if skipped:
                print(f"Уже существуют (пропущены): {len(skipped)}")
                for n in skipped:
                    print(f"  = {n}")

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
