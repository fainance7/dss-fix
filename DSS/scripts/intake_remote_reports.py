"""
ETL: чтение reports_db смежной команды (read-only) → DataIntakeJob/Rows в DSS.

Использует yaml-маппинг на одну remote-таблицу. Подключается к remote БД
параметрами из data/.env.remote_report или из переменных окружения.

Только SELECT на remote БД.

Использование:
    backend/.venv/bin/python scripts/intake_remote_reports.py \
        --mapping data/intake_mapping/temperature_monthly_sko.yaml [--commit] [--dry-run]

--dry-run (по умолчанию): печатает сводку, ничего не пишет в DSS.
без --dry-run: создаёт DataIntakeJob/Rows в DSS (status='validated' если все ок).
--commit: дополнительно вызывает intake_service.commit_job → UPSERT в indicator_values.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml
import asyncpg
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.data import Direction, Indicator  # noqa: E402
from app.models.kato import KatoReference  # noqa: E402
from app.models.user import Region, User  # noqa: E402
from app.services.intake_service import (  # noqa: E402
    ParsedRow,
    ParsedTemplate,
    persist_job_with_rows,
    commit_job,
)


REMOTE_ENV_FILE = ROOT / "data" / ".env.remote_report"


def load_remote_credentials() -> dict[str, str]:
    """Креды БД с первичными данными (`reports_db`).

    По умолчанию идём на локальный контейнер `dss-reports-db` (порт 5434 с хоста,
    5432 внутри docker-сети). Прежний SSH-туннель к REMOTE_HOST — только если явно
    указать через REMOTE_PGHOST/REMOTE_PGPORT.
    """
    creds = {
        "host": os.environ.get("REMOTE_PGHOST", "localhost"),
        "port": int(os.environ.get("REMOTE_PGPORT", "5434")),
        "user": os.environ.get("REMOTE_PGUSER", "postgres"),
        "password": os.environ["REMOTE_PGPASSWORD"]  # M-3: required, no insecure default,
        "database": os.environ.get("REMOTE_PGDATABASE", "reports_db"),
    }
    return creds


async def fetch_remote_rows(
    table: str, where: Optional[str] = None
) -> tuple[list[str], list[list[Any]]]:
    """Возвращает (column_names_in_order, rows_as_lists). Только SELECT."""
    creds = load_remote_credentials()
    conn = await asyncpg.connect(
        host=creds["host"],
        port=creds["port"],
        user=creds["user"],
        password=creds["password"],
        database=creds["database"],
        ssl=False,
    )
    try:
        safe_table = table.replace('"', '""')
        col_rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=$1 "
            "ORDER BY ordinal_position",
            table,
        )
        column_names = [r["column_name"] for r in col_rows]
        if not column_names:
            return [], []
        sql = f'SELECT * FROM public."{safe_table}"'
        if where:
            sql += f" WHERE {where}"
        rows = await conn.fetch(sql)
        # Each Record retains positional order; cast to list.
        return column_names, [list(r) for r in rows]
    finally:
        await conn.close()


def normalize_wide_to_long(
    column_names: list[str],
    rows: list[list[Any]],
    mapping: dict[str, Any],
) -> list[dict[str, Any]]:
    """Wide-таблица → long.

    mapping['period_columns_idx'] — [{index: 1, marker: '01'}, ...] (0-based позиции колонок).
    mapping['region_column_idx'] / mapping['year_column_idx'] — 0-based позиции (опционально).
    Возвращает список dict с __period_marker/__period_kind/__value + meta-поля.
    """
    out: list[dict[str, Any]] = []
    period_cols = mapping.get("period_columns_idx", [])
    region_idx = mapping.get("region_column_idx")
    year_idx = mapping.get("year_column_idx")
    period_kind = mapping.get("period_kind", "monthly")
    skip_predicate = mapping.get("skip_if_first_col_in")  # список значений, по которым строка пропускается

    for row in rows:
        if skip_predicate and len(row) > 0 and row[0] in skip_predicate:
            continue
        meta = {}
        if region_idx is not None and region_idx < len(row):
            meta[column_names[region_idx]] = row[region_idx]
        if year_idx is not None and year_idx < len(row):
            meta[column_names[year_idx]] = row[year_idx]
        for spec in period_cols:
            idx = spec["index"]
            if idx >= len(row):
                continue
            v = row[idx]
            if v is None or v == "":
                continue
            entry = dict(meta)
            entry["__period_marker"] = spec["marker"]
            entry["__period_kind"] = period_kind
            entry["__value"] = v
            entry["__year_idx_value"] = row[year_idx] if year_idx is not None and year_idx < len(row) else None
            entry["__region_idx_value"] = row[region_idx] if region_idx is not None and region_idx < len(row) else None
            out.append(entry)
    return out


def build_period(
    period_kind: str, marker: str, year_value: Any
) -> tuple[Optional[date], Optional[date], str]:
    """Возвращает (period_start, period_end, period_type)."""
    if period_kind == "monthly":
        # marker = '01'..'12'; year_value = int year
        try:
            yr = int(year_value)
            mo = int(marker)
        except (ValueError, TypeError):
            return None, None, "month"
        from calendar import monthrange
        last = monthrange(yr, mo)[1]
        return date(yr, mo, 1), date(yr, mo, last), "month"
    if period_kind == "yearly":
        try:
            yr = int(year_value)
        except (ValueError, TypeError):
            return None, None, "year"
        return date(yr, 1, 1), date(yr, 12, 31), "year"
    return None, None, period_kind


async def map_to_parsed(
    db,
    mapping: dict[str, Any],
    column_names: list[str],
    remote_rows: list[list[Any]],
) -> ParsedTemplate:
    """Превращает remote-данные в ParsedTemplate с резолвом indicator/region."""
    # Найдём indicator по (direction_code, indicator_name)
    direction_code = mapping["direction_code"]
    indicator_name = mapping["indicator_name"]
    direction = (
        await db.execute(select(Direction).where(Direction.code == direction_code))
    ).scalar_one_or_none()
    if direction is None:
        raise SystemExit(f"direction '{direction_code}' не найден в DSS")
    indicator = (
        await db.execute(
            select(Indicator).where(
                Indicator.direction_id == direction.id,
                Indicator.name == indicator_name,
            )
        )
    ).scalar_one_or_none()
    if indicator is None:
        raise SystemExit(
            f"indicator '{indicator_name}' для direction '{direction_code}' не найден"
        )

    # Резолв kato → region
    kato_resolution = mapping.get("kato_resolution", {})
    constant_kato = kato_resolution.get("constant")
    name_to_kato_map: dict[str, str] = kato_resolution.get("by_name", {})
    region_cache: dict[str, Optional[Region]] = {}

    async def resolve_region(name_or_const: Optional[str]) -> Optional[Region]:
        kc = constant_kato or name_to_kato_map.get((name_or_const or "").strip())
        if not kc:
            return None
        if kc in region_cache:
            return region_cache[kc]
        kato = (
            await db.execute(
                select(KatoReference).where(KatoReference.kato_code == kc)
            )
        ).scalar_one_or_none()
        if kato is None or kato.region_id is None:
            region_cache[kc] = None
            return None
        region = (
            await db.execute(select(Region).where(Region.id == kato.region_id))
        ).scalar_one_or_none()
        region_cache[kc] = region
        return region

    long_rows = normalize_wide_to_long(column_names, remote_rows, mapping)

    parsed = ParsedTemplate()
    parsed.source_name = mapping.get("source_name") or f"reports_db / {mapping['remote_table']}"
    parsed.source_url = mapping.get("source_url")
    parsed.source_description = mapping.get("source_description")

    for i, row in enumerate(long_rows, start=1):
        marker = row.pop("__period_marker")
        kind = row.pop("__period_kind")
        value = row.pop("__value")
        year_value = row.pop("__year_idx_value", None) or marker
        region_name = row.pop("__region_idx_value", None)
        ps, pe, ptype = build_period(kind, marker, year_value)
        region = await resolve_region(region_name)
        pr = ParsedRow(
            row_no=i,
            raw={**row, "marker": marker, "value": value},
            indicator_id=indicator.id,
            region_id=region.id if region else None,
            period_type=ptype,
            period_start=ps,
            period_end=pe,
        )
        try:
            pr.value = float(value)
        except (TypeError, ValueError):
            pr.value = None
            pr.error = f"value не число: {value!r}"
        if pr.error is None and region is None:
            pr.error = "Регион не определён по kato_resolution"
        if pr.error is None and (ps is None or pe is None):
            pr.error = "Не удалось построить period_start/period_end"
        pr.status = "ok" if pr.error is None else "invalid"
        parsed.rows.append(pr)
    return parsed


async def get_or_create_etl_user(db) -> User:
    user = (
        await db.execute(select(User).where(User.username == "etl_intake"))
    ).scalar_one_or_none()
    if user is None:
        raise SystemExit(
            "Технический пользователь 'etl_intake' не создан. "
            "Создайте через /api/v1/users или seed_dev.py."
        )
    return user


async def run(mapping_path: Path, dry_run: bool, do_commit: bool) -> None:
    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping = yaml.safe_load(f)
    table = mapping["remote_table"]
    print(f"reading remote table: {table}")
    column_names, remote_rows = await fetch_remote_rows(table, mapping.get("where"))
    print(f"remote rows: {len(remote_rows)}, columns: {len(column_names)}")

    async with AsyncSessionLocal() as db:
        parsed = await map_to_parsed(db, mapping, column_names, remote_rows)
        ok = sum(1 for r in parsed.rows if r.status == "ok")
        invalid = sum(1 for r in parsed.rows if r.status == "invalid")
        print(f"parsed: total={len(parsed.rows)} ok={ok} invalid={invalid}")
        if invalid and invalid <= 5:
            for r in parsed.rows:
                if r.status == "invalid":
                    print(f"  row {r.row_no}: {r.error}")
        if dry_run:
            print("dry-run: ничего не записано")
            return
        user = await get_or_create_etl_user(db)
        job = await persist_job_with_rows(
            db,
            parsed=parsed,
            source_kind="remote_reports_db",
            source_ref=table,
            user=user,
        )
        await db.commit()
        print(f"created job {job.id} status={job.status} ok={job.rows_ok}")
        if do_commit and job.rows_ok:
            async with AsyncSessionLocal() as db2:
                fresh_job = (
                    await db2.execute(
                        select(type(job)).where(type(job).id == job.id)
                    )
                ).scalar_one()
                written, skipped, errors = await commit_job(db2, job=fresh_job, user=user)
                await db2.commit()
                print(f"committed: written={written} skipped={skipped}")
                for e in errors[:5]:
                    print(f"  err: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", required=True, help="Путь к yaml-маппингу")
    ap.add_argument("--dry-run", action="store_true", help="ничего не писать в БД")
    ap.add_argument("--commit", action="store_true", help="после создания job — UPSERT в indicator_values")
    args = ap.parse_args()
    asyncio.run(run(Path(args.mapping), args.dry_run, args.commit))


if __name__ == "__main__":
    main()
