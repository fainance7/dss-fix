"""Запуск IntakeMapping (из БД) против reports_db.

В отличие от scripts/intake_remote_reports.py (CLI-инструмент с yaml),
эта версия читает конфиг из БД (таблица intake_mappings) и используется
backend-эндпоинтами (UI-кнопки «Dry-run» и «Commit»).
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data import Indicator
from app.models.data_intake import DataIntakeJob, IntakeMapping
from app.models.kato import KatoReference
from app.models.user import Region, User
from app.services import reports_db as reports_db_svc
from app.services import intake_quality
from app.services.intake_service import (
    ParsedRow,
    ParsedTemplate,
    commit_job,
    persist_job_with_rows,
)


@dataclass
class RunResult:
    job_id: Optional[str]
    rows_total: int
    rows_ok: int
    rows_invalid: int
    rows_imported: int
    sample_errors: list[str]


def _build_period(period_kind: str, marker: str, year_value: Any) -> tuple[Optional[date], Optional[date], str]:
    if period_kind == "monthly":
        try:
            yr = int(year_value)
            mo = int(marker)
        except (ValueError, TypeError):
            return None, None, "month"
        last = monthrange(yr, mo)[1]
        return date(yr, mo, 1), date(yr, mo, last), "month"
    if period_kind == "quarterly":
        try:
            yr = int(year_value)
            q = int(marker)
        except (ValueError, TypeError):
            return None, None, "quarter"
        m_start = (q - 1) * 3 + 1
        m_end = m_start + 2
        return date(yr, m_start, 1), date(yr, m_end, monthrange(yr, m_end)[1]), "quarter"
    if period_kind == "yearly":
        try:
            yr = int(year_value)
        except (ValueError, TypeError):
            return None, None, "year"
        return date(yr, 1, 1), date(yr, 12, 31), "year"
    if period_kind == "daily":
        try:
            d = date.fromisoformat(str(marker))
            return d, d, "day"
        except ValueError:
            return None, None, "day"
    return None, None, period_kind


def _normalize_wide_to_long(
    columns: list[dict[str, Any]],
    rows: list[list[Any]],
    mapping: IntakeMapping,
) -> list[dict[str, Any]]:
    """Wide-таблица → long: одна row × N period_columns → N точек."""
    out: list[dict[str, Any]] = []
    period_cols: list[dict[str, Any]] = list(mapping.period_columns_idx or [])
    region_idx = mapping.region_column_idx
    year_idx = mapping.year_column_idx
    skip_predicate = list(mapping.skip_if_first_col_in or [])

    for row in rows:
        if skip_predicate and len(row) > 0 and row[0] in skip_predicate:
            continue
        for spec in period_cols:
            idx = spec["index"]
            if idx >= len(row):
                continue
            v = row[idx]
            if v is None or v == "":
                continue
            entry = {
                "__period_marker": spec["marker"],
                "__value": v,
                "__year_idx_value": (
                    row[year_idx] if year_idx is not None and year_idx < len(row) else None
                ),
                "__region_idx_value": (
                    row[region_idx] if region_idx is not None and region_idx < len(row) else None
                ),
            }
            if region_idx is not None and region_idx < len(row):
                entry[columns[region_idx]["column_name"]] = row[region_idx]
            if year_idx is not None and year_idx < len(row):
                entry[columns[year_idx]["column_name"]] = row[year_idx]
            out.append(entry)
    return out


async def _resolve_indicator(db: AsyncSession, mapping: IntakeMapping) -> Indicator:
    obj = (
        await db.execute(select(Indicator).where(Indicator.id == mapping.indicator_id))
    ).scalar_one_or_none()
    if obj is None:
        raise ValueError(f"Indicator {mapping.indicator_id} не найден в DSS")
    return obj


async def _resolve_region_constant(db: AsyncSession, kato_code: str) -> Optional[Region]:
    kato = (
        await db.execute(select(KatoReference).where(KatoReference.kato_code == kato_code))
    ).scalar_one_or_none()
    if kato is None or kato.region_id is None:
        return None
    return (
        await db.execute(select(Region).where(Region.id == kato.region_id))
    ).scalar_one_or_none()


async def _resolve_region_by_name(
    db: AsyncSession, name: Optional[str], name_to_kato: dict[str, str]
) -> Optional[Region]:
    if not name:
        return None
    code = name_to_kato.get(str(name).strip())
    if not code:
        return None
    return await _resolve_region_constant(db, code)


async def run_mapping(
    db: AsyncSession,
    *,
    mapping: IntakeMapping,
    user: User,
    do_commit: bool,
) -> RunResult:
    """Прогоняет mapping: читает remote → парсит → DataIntakeJob; опционально commit."""
    indicator = await _resolve_indicator(db, mapping)

    # 1. Прочитать remote-таблицу
    cols_raw = await reports_db_svc.get_table_columns(mapping.remote_table)
    if not cols_raw:
        raise ValueError(f"Таблица «{mapping.remote_table}» не найдена в reports_db")
    preview = await reports_db_svc.preview_rows(mapping.remote_table, limit=100000)
    columns = preview["columns"]
    rows = preview["rows"]

    long_rows = _normalize_wide_to_long(columns, rows, mapping)

    # 2. Кэш регионов (для by_name)
    region_cache: dict[str, Optional[Region]] = {}

    parsed = ParsedTemplate(
        source_name=mapping.source_name or f"reports_db / {mapping.remote_table}",
        source_url=mapping.source_url,
        source_description=mapping.source_description,
    )

    for i, row in enumerate(long_rows, start=1):
        marker = row.pop("__period_marker")
        value = row.pop("__value")
        year_value = row.pop("__year_idx_value", None) or marker
        region_name = row.pop("__region_idx_value", None)

        ps, pe, ptype = _build_period(mapping.period_kind, marker, year_value)

        # Резолв региона по правилу из mapping
        region: Optional[Region]
        if mapping.region_resolution_kind == "constant":
            kc = mapping.kato_constant
            if kc not in region_cache:
                region_cache[kc] = await _resolve_region_constant(db, kc)
            region = region_cache[kc]
        else:
            cache_key = f"name::{region_name}"
            if cache_key not in region_cache:
                region_cache[cache_key] = await _resolve_region_by_name(
                    db, region_name, mapping.region_name_to_kato or {}
                )
            region = region_cache[cache_key]

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
            pr.value = float(value) if value is not None else None
        except (TypeError, ValueError):
            pr.error = f"value не число: {value!r}"
            pr.value = None
        if pr.error is None and region is None:
            pr.error = "Регион не определён по region_resolution_kind"
        if pr.error is None and (ps is None or pe is None):
            pr.error = "Не удалось построить период"
        pr.status = "ok" if pr.error is None else "invalid"
        parsed.rows.append(pr)

    job = await persist_job_with_rows(
        db,
        parsed=parsed,
        source_kind="remote_reports_db",
        source_ref=mapping.remote_table,
        user=user,
    )

    rows_imported = 0
    sample_errors: list[str] = []
    # Блокеры контроля качества требуют подтверждения эксперта — автоимпорт не делаем,
    # партия остаётся «validated» и подтверждается на странице партии.
    blocked = bool(intake_quality.blockers(job.quality_report))
    if do_commit and job.rows_ok and not blocked:
        rows_imported, _skipped, errs = await commit_job(db, job=job, user=user)
        sample_errors = errs[:5]
    elif do_commit and blocked:
        sample_errors = ["Импорт отложен: в партии есть замечания уровня «блокер», нужно подтверждение эксперта"]

    # Запомним result в самом mapping для UI
    from datetime import datetime as _dt
    mapping.last_run_at = _dt.utcnow()
    mapping.last_run_status = job.status
    mapping.last_run_summary = {
        "rows_total": job.rows_total,
        "rows_ok": job.rows_ok,
        "rows_invalid": job.rows_invalid,
        "rows_imported": rows_imported,
        "dry_run": not do_commit,
    }
    await db.flush()

    return RunResult(
        job_id=str(job.id),
        rows_total=job.rows_total,
        rows_ok=job.rows_ok,
        rows_invalid=job.rows_invalid,
        rows_imported=rows_imported,
        sample_errors=sample_errors,
    )
