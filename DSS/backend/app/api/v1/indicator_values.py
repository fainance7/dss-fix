"""
Time-series values of indicators per region.

Provides CRUD + bulk import + time-series readout for ML training and dashboards.
RBAC: exp_dir users restricted to their direction subset (via indicator.direction_id).
"""
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_action
from app.core.rbac import get_user_allowed_directions, get_user_region_scope, require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import Indicator, IndicatorValue
from app.services.intake_service import COMPUTED_INDICATOR_ERROR, MONTH_SHAPE_ERROR, _is_full_month
from app.models.data_intake import DataIntakeJob, IntakeQualityFlag
from app.models.user import Region, User
from app.schemas.data import (
    BulkValidateRequest,
    BulkValidateResult,
    IndicatorTimeSeriesOut,
    IndicatorValueBulkCreate,
    IndicatorValueBulkResult,
    IndicatorValueCreate,
    IndicatorValueOut,
    ListResponse,
    TimeSeriesPoint,
    ValidateRequest,
)

router = APIRouter(prefix="/indicator-values", tags=["indicator-values"])


# ── helpers ───────────────────────────────────────────────────

async def _load_indicator_or_404(db: AsyncSession, indicator_id: UUID) -> Indicator:
    obj = (
        await db.execute(
            select(Indicator).where(Indicator.id == indicator_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Показатель не найден")
    return obj


def _ensure_user_can_touch_direction(user: User, direction_id: UUID) -> None:
    allowed = get_user_allowed_directions(user)
    if allowed is not None and direction_id not in allowed:
        raise HTTPException(
            status_code=403,
            detail="Нет прав на изменение данных этого направления",
        )


def _to_out(obj: IndicatorValue) -> IndicatorValueOut:
    return IndicatorValueOut(
        id=obj.id,
        indicator_id=obj.indicator_id,
        indicator_name=obj.indicator.name if obj.indicator else None,
        direction_id=obj.indicator.direction_id if obj.indicator else None,
        region_id=obj.region_id,
        region_name=obj.region.name if obj.region else None,
        period_type=obj.period_type,
        period_start=obj.period_start,
        period_end=obj.period_end,
        value=obj.value,
        source_id=obj.source_id,
        validation_status=obj.validation_status,
        validated_by=obj.validated_by,
        validated_at=obj.validated_at,
        created_by=obj.created_by,
        created_at=obj.created_at,
    )


def _flagged_clause():
    """Есть замечание контроля качества из импортированной партии по ключу значения."""
    return (
        select(IntakeQualityFlag.id)
        .join(DataIntakeJob, DataIntakeJob.id == IntakeQualityFlag.job_id)
        .where(
            DataIntakeJob.status == "imported",
            IntakeQualityFlag.indicator_id == IndicatorValue.indicator_id,
            IntakeQualityFlag.region_id == IndicatorValue.region_id,
            IntakeQualityFlag.period_type == IndicatorValue.period_type,
            IntakeQualityFlag.period_start == IndicatorValue.period_start,
        )
        .exists()
    )


async def _quality_flags(db: AsyncSession, items) -> dict[tuple, list[dict]]:
    """Замечания по ключам значений страницы — из самой поздней импортированной партии."""
    if not items:
        return {}
    keys = {(o.indicator_id, o.region_id, o.period_type, o.period_start) for o in items}
    rows = (
        await db.execute(
            select(IntakeQualityFlag, DataIntakeJob.source_ref, DataIntakeJob.uploaded_at)
            .join(DataIntakeJob, DataIntakeJob.id == IntakeQualityFlag.job_id)
            .where(
                DataIntakeJob.status == "imported",
                IntakeQualityFlag.indicator_id.in_({k[0] for k in keys}),
                IntakeQualityFlag.region_id.in_({k[1] for k in keys}),
            )
            .order_by(DataIntakeJob.uploaded_at.desc())
        )
    ).all()
    latest_job: dict[tuple, UUID] = {}
    out: dict[tuple, list[dict]] = {}
    for f, source_ref, _ in rows:
        k = (f.indicator_id, f.region_id, f.period_type, f.period_start)
        if k not in keys:
            continue
        if latest_job.setdefault(k, f.job_id) != f.job_id:
            continue  # замечание из более старой партии — актуальна последняя
        out.setdefault(k, []).append({
            "code": f.code, "severity": f.severity, "message": f.message,
            "job_id": str(f.job_id), "source_ref": source_ref,
        })
    order = {"blocker": 0, "warning": 1, "info": 2}
    for flags in out.values():
        flags.sort(key=lambda f: order.get(f["severity"], 3))
    return out


# ── endpoints ─────────────────────────────────────────────────

def _filtered_query(current_user, indicator_id, region_id, direction_id, period_type,
                    date_from, date_to, validation_status, created_by, has_quality_flags):
    """Выборка значений с фильтрами и ограничениями роли (направления эксперта, район акима)."""
    q = select(IndicatorValue).join(Indicator, IndicatorValue.indicator_id == Indicator.id).options(
        selectinload(IndicatorValue.indicator),
        selectinload(IndicatorValue.region),
    )

    if indicator_id:
        q = q.where(IndicatorValue.indicator_id == indicator_id)
    if region_id:
        q = q.where(IndicatorValue.region_id == region_id)
    if direction_id:
        q = q.where(Indicator.direction_id == direction_id)
    if period_type:
        q = q.where(IndicatorValue.period_type == period_type)
    if date_from:
        q = q.where(IndicatorValue.period_start >= date_from)
    if date_to:
        q = q.where(IndicatorValue.period_end <= date_to)
    if validation_status:
        q = q.where(IndicatorValue.validation_status == validation_status)
    if created_by:
        q = q.where(IndicatorValue.created_by == created_by)
    if has_quality_flags:
        q = q.where(_flagged_clause())

    allowed = get_user_allowed_directions(current_user)
    if allowed is not None:
        q = q.where(Indicator.direction_id.in_(allowed))

    # G9: akim_ray видит только показатели своего района
    scope = get_user_region_scope(current_user)
    if scope is not None:
        if region_id is not None and region_id not in scope:
            raise HTTPException(status_code=403, detail="Доступ только к данным своего района")
        q = q.where(IndicatorValue.region_id.in_(scope))

    return q


@router.get("", response_model=ListResponse)
async def list_values(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    indicator_id: Optional[UUID] = None,
    region_id: Optional[UUID] = None,
    direction_id: Optional[UUID] = None,
    period_type: Optional[str] = Query(None, pattern="^(year|quarter|month|day)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    validation_status: Optional[str] = Query(None, pattern="^(pending|approved|rejected)$"),
    created_by: Optional[UUID] = Query(None, description="Фильтр по автору загрузки (полезно admin'у)"),
    has_quality_flags: bool = Query(False, description="Только значения с замечаниями контроля качества"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    q = _filtered_query(current_user, indicator_id, region_id, direction_id, period_type,
                        date_from, date_to, validation_status, created_by, has_quality_flags)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (
        await db.execute(
            q.order_by(
                IndicatorValue.indicator_id,
                IndicatorValue.region_id,
                IndicatorValue.period_start,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    flags = await _quality_flags(db, items)
    out = []
    for o in items:
        item = _to_out(o)
        item.quality_flags = flags.get((o.indicator_id, o.region_id, o.period_type, o.period_start), [])
        out.append(item.model_dump(mode="json"))

    return ListResponse(
        items=out,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total else 0,
    )


EXPORT_LIMIT = 200_000
_STATUS_RU = {"approved": "утверждено", "pending": "на проверке", "rejected": "отклонено"}


@router.get("/export")
async def export_values(
    indicator_id: Optional[UUID] = None,
    region_id: Optional[UUID] = None,
    direction_id: Optional[UUID] = None,
    period_type: Optional[str] = Query(None, pattern="^(year|quarter|month|day)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    validation_status: Optional[str] = Query(None, pattern="^(pending|approved|rejected)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Отчёт по показателям в Excel (ТЗ Прил. 2, ч. 2 «Экспорт отчётов по показателям»):
    те же фильтры и ограничения роли, что у списка значений."""
    import io

    from fastapi.responses import Response
    from openpyxl import Workbook
    from openpyxl.styles import Font

    q = _filtered_query(current_user, indicator_id, region_id, direction_id, period_type,
                        date_from, date_to, validation_status, None, False)
    q = q.options(selectinload(IndicatorValue.indicator).selectinload(Indicator.unit))
    rows = (await db.execute(
        q.order_by(Indicator.name, IndicatorValue.region_id, IndicatorValue.period_start).limit(EXPORT_LIMIT)
    )).scalars().all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Показатели"
    ws.append(["Показатель", "Единица", "Территория", "Период", "Начало периода", "Окончание периода",
               "Значение", "Статус проверки"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for v in rows:
        unit = v.indicator.unit.name if v.indicator and v.indicator.unit else ""
        ws.append([
            v.indicator.name if v.indicator else "",
            unit,
            v.region.name if v.region else "",
            {"year": "год", "quarter": "квартал", "month": "месяц", "day": "день"}.get(v.period_type, v.period_type),
            v.period_start,
            v.period_end,
            float(v.value) if v.value is not None else None,
            _STATUS_RU.get(v.validation_status, v.validation_status),
        ])
    for col, width in zip("ABCDEFGH", (55, 14, 28, 10, 15, 17, 14, 16)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="indicators.xlsx"'},
    )


@router.get("/timeseries", response_model=IndicatorTimeSeriesOut)
async def get_timeseries(
    indicator_id: UUID = Query(...),
    region_id: UUID = Query(...),
    period_type: str = Query("year", pattern="^(year|quarter|month|day)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    only_approved: bool = Query(False, description="Если true — отдавать только validation_status='approved'"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    indicator = await _load_indicator_or_404(db, indicator_id)
    allowed = get_user_allowed_directions(current_user)
    if allowed is not None and indicator.direction_id not in allowed:
        raise HTTPException(status_code=403, detail="Нет доступа к этому направлению")

    # G9: akim_ray видит только показатели своего района
    scope = get_user_region_scope(current_user)
    if scope is not None and region_id not in scope:
        raise HTTPException(status_code=403, detail="Доступ только к данным своего района")

    region = (
        await db.execute(select(Region).where(Region.id == region_id))
    ).scalar_one_or_none()

    q = (
        select(IndicatorValue)
        .where(
            IndicatorValue.indicator_id == indicator_id,
            IndicatorValue.region_id == region_id,
            IndicatorValue.period_type == period_type,
        )
        .order_by(IndicatorValue.period_start)
    )
    if date_from:
        q = q.where(IndicatorValue.period_start >= date_from)
    if date_to:
        q = q.where(IndicatorValue.period_end <= date_to)
    if only_approved:
        q = q.where(IndicatorValue.validation_status == "approved")

    rows = (await db.execute(q)).scalars().all()
    return IndicatorTimeSeriesOut(
        indicator_id=indicator_id,
        indicator_name=indicator.name,
        region_id=region_id,
        region_name=region.name if region else None,
        period_type=period_type,
        points=[
            TimeSeriesPoint(
                period_start=r.period_start,
                period_end=r.period_end,
                value=r.value,
                validation_status=r.validation_status,
            )
            for r in rows
        ],
    )


@router.post("", response_model=IndicatorValueOut, status_code=201)
async def create_value(
    body: IndicatorValueCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    indicator = await _load_indicator_or_404(db, body.indicator_id)
    _ensure_user_can_touch_direction(current_user, indicator.direction_id)
    if indicator.is_computed:
        raise HTTPException(status_code=400, detail=COMPUTED_INDICATOR_ERROR.format(indicator.name))
    if body.period_type == "month" and not _is_full_month(body.period_start, body.period_end):
        raise HTTPException(status_code=400, detail=MONTH_SHAPE_ERROR)

    region = (
        await db.execute(select(Region).where(Region.id == body.region_id))
    ).scalar_one_or_none()
    if region is None:
        raise HTTPException(status_code=400, detail="Регион не найден")

    if body.period_end < body.period_start:
        raise HTTPException(status_code=400, detail="period_end < period_start")

    obj = IndicatorValue(
        **body.model_dump(),
        validation_status="pending",
        created_by=current_user.id,
    )
    db.add(obj)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Значение для этой комбинации (показатель/регион/период) уже существует",
        )
    await log_action(
        db,
        user_id=current_user.id,
        action="create",
        entity_type="indicator_value",
        entity_id=obj.id,
        details=body.model_dump(mode="json"),
    )
    await db.refresh(obj, ["indicator", "region"])
    return _to_out(obj)


@router.post("/bulk", response_model=IndicatorValueBulkResult)
async def bulk_create(
    body: IndicatorValueBulkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    """
    Идемпотентная массовая загрузка значений.
    На дубликате (indicator_id, region_id, period_type, period_start) — UPSERT (обновление value/source).
    """
    if not body.items:
        return IndicatorValueBulkResult(inserted=0, updated=0, skipped=0, errors=[])

    # Pre-fetch indicators for direction RBAC + validation
    indicator_ids = {it.indicator_id for it in body.items}
    indicators = (
        await db.execute(select(Indicator).where(Indicator.id.in_(indicator_ids)))
    ).scalars().all()
    by_id = {i.id: i for i in indicators}

    allowed = get_user_allowed_directions(current_user)

    inserted = updated = skipped = 0
    errors: list[str] = []

    for idx, item in enumerate(body.items):
        ind = by_id.get(item.indicator_id)
        if ind is None:
            errors.append(f"#{idx}: indicator {item.indicator_id} not found")
            skipped += 1
            continue
        if allowed is not None and ind.direction_id not in allowed:
            errors.append(f"#{idx}: forbidden direction for indicator {ind.name}")
            skipped += 1
            continue
        if ind.is_computed:
            errors.append(f"#{idx}: " + COMPUTED_INDICATOR_ERROR.format(ind.name))
            skipped += 1
            continue
        if item.period_end < item.period_start:
            errors.append(f"#{idx}: period_end < period_start")
            skipped += 1
            continue
        if item.period_type == "month" and not _is_full_month(item.period_start, item.period_end):
            errors.append(f"#{idx}: " + MONTH_SHAPE_ERROR)
            skipped += 1
            continue

        stmt = pg_insert(IndicatorValue).values(
            indicator_id=item.indicator_id,
            region_id=item.region_id,
            period_type=item.period_type,
            period_start=item.period_start,
            period_end=item.period_end,
            value=item.value,
            source_id=item.source_id,
            validation_status="pending",
            created_by=current_user.id,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="iv_unique_value",
            set_={
                "value": stmt.excluded.value,
                "period_end": stmt.excluded.period_end,
                "source_id": stmt.excluded.source_id,
            },
        ).returning(IndicatorValue.id, IndicatorValue.created_at == IndicatorValue.created_at)

        try:
            res = (await db.execute(stmt)).first()
            # rough heuristic: PG doesn't easily tell insert vs update; rely on rowcount check
            if res is not None:
                inserted += 1  # treated as inserted-or-upserted; we don't split for now
        except IntegrityError as e:
            await db.rollback()
            errors.append(f"#{idx}: {e.orig}")
            skipped += 1

    await log_action(
        db,
        user_id=current_user.id,
        action="bulk_create",
        entity_type="indicator_value",
        details={"total": len(body.items), "inserted": inserted, "skipped": skipped},
    )
    return IndicatorValueBulkResult(
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        errors=errors,
    )


@router.post("/validate-bulk", response_model=BulkValidateResult)
async def validate_bulk(
    body: BulkValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver")),
):
    """Утверждение/отклонение N значений одним запросом.

    Принимает либо ids (список UUID), либо job_id (резолвит все imported-строки
    партии в indicator_values по уникальному (indicator, region, period_type, period_start)).
    """
    from sqlalchemy import update
    from app.models.data_intake import DataIntakeRow

    if not body.ids and not body.job_id:
        raise HTTPException(status_code=400, detail="Нужно передать ids или job_id")

    update_values = {
        "validation_status": body.status,
        "validated_by": current_user.id,
        "validated_at": datetime.utcnow(),
    }
    updated = 0
    not_found = 0

    if body.job_id:
        # UPDATE через коррелированный SELECT по DataIntakeRow — без переноса
        # тысяч ключей через параметры (лимит PostgreSQL 65535 параметров).
        imported_total = (
            await db.execute(
                select(func.count())
                .select_from(DataIntakeRow)
                .where(
                    DataIntakeRow.job_id == body.job_id,
                    DataIntakeRow.status == "imported",
                )
            )
        ).scalar() or 0
        if imported_total == 0:
            return BulkValidateResult(updated=0, not_found=0)

        matched_subq = (
            select(IndicatorValue.id)
            .select_from(DataIntakeRow)
            .join(
                IndicatorValue,
                (IndicatorValue.indicator_id == DataIntakeRow.indicator_id)
                & (IndicatorValue.region_id == DataIntakeRow.region_id)
                & (IndicatorValue.period_type == DataIntakeRow.period_type)
                & (IndicatorValue.period_start == DataIntakeRow.period_start),
            )
            .where(
                DataIntakeRow.job_id == body.job_id,
                DataIntakeRow.status == "imported",
            )
        )
        res = await db.execute(
            update(IndicatorValue)
            .where(IndicatorValue.id.in_(matched_subq))
            .values(**update_values)
        )
        updated = res.rowcount or 0
        # Декады метеостанций партии (meteo_station_values.job_id) — тем же решением,
        # затем пересчёт месяцев станций и районов.
        from app.models.data import MeteoStationValue
        from app.services import climate_dekads

        st_rows = (await db.execute(
            update(MeteoStationValue)
            .where(MeteoStationValue.job_id == body.job_id, MeteoStationValue.period_type == "dekad")
            .values(**update_values)
            .returning(MeteoStationValue.station_id, MeteoStationValue.indicator_id,
                       MeteoStationValue.period_start)
        )).all()
        if st_rows:
            updated += len(st_rows)
            await climate_dekads.recompute(db, climate_dekads.month_keys_from_values(st_rows))
        not_found = max(0, imported_total - updated)
    else:
        # Явный список ids — батчируем (PostgreSQL лимит = 65535 параметров).
        ids = list(body.ids)
        batch_size = 10_000
        for start in range(0, len(ids), batch_size):
            chunk = ids[start : start + batch_size]
            res = await db.execute(
                update(IndicatorValue)
                .where(IndicatorValue.id.in_(chunk))
                .values(**update_values)
            )
            updated += res.rowcount or 0
        not_found = max(0, len(ids) - updated)

    await log_action(
        db,
        user_id=current_user.id,
        action=f"validate_bulk_{body.status}",
        entity_type="indicator_value",
        details={
            "count": updated,
            "via": "job_id" if body.job_id else "ids",
            "job_id": str(body.job_id) if body.job_id else None,
            "comment": body.comment,
        },
    )
    return BulkValidateResult(updated=updated, not_found=not_found)


@router.post("/{value_id}/validate", response_model=IndicatorValueOut)
async def validate_value(
    value_id: UUID,
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver")),
):
    obj = (
        await db.execute(
            select(IndicatorValue)
            .options(selectinload(IndicatorValue.indicator), selectinload(IndicatorValue.region))
            .where(IndicatorValue.id == value_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Значение не найдено")

    obj.validation_status = body.status
    obj.validated_by = current_user.id
    obj.validated_at = datetime.utcnow()
    await db.flush()

    await log_action(
        db,
        user_id=current_user.id,
        action="validate",
        entity_type="indicator_value",
        entity_id=obj.id,
        details={"status": body.status, "comment": body.comment},
    )
    return _to_out(obj)


@router.delete("/{value_id}", status_code=204)
async def delete_value(
    value_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver")),
):
    obj = (
        await db.execute(select(IndicatorValue).where(IndicatorValue.id == value_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Значение не найдено")

    await db.delete(obj)
    await log_action(
        db,
        user_id=current_user.id,
        action="delete",
        entity_type="indicator_value",
        entity_id=value_id,
    )
