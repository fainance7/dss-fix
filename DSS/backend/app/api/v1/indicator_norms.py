"""
Среднемноголетние нормы показателей (одна константа на показатель/район).

Только чтение из API: используется климатическими дашбордами для горизонтальной
линии нормы на графике «Динамика по годам». Запись — через скрипт
backend/scripts/import_climate_norms.py (идемпотентный upsert).
RBAC: exp_dir-пользователи видят только свои направления (как indicator_values).
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.rbac import get_user_allowed_directions
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import Indicator, IndicatorNorm
from app.models.user import User
from app.schemas.data import IndicatorNormOut

router = APIRouter(prefix="/indicator-norms", tags=["indicator-norms"])


@router.get("", response_model=list[IndicatorNormOut])
async def list_norms(
    indicator_id: Optional[UUID] = Query(None),
    region_id: Optional[UUID] = Query(None),
    direction_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Среднемноголетние нормы. Фильтры: показатель / район / направление."""
    q = (
        select(IndicatorNorm)
        .join(Indicator, IndicatorNorm.indicator_id == Indicator.id)
        .options(
            selectinload(IndicatorNorm.indicator),
            selectinload(IndicatorNorm.region),
        )
    )
    if indicator_id:
        q = q.where(IndicatorNorm.indicator_id == indicator_id)
    if region_id:
        q = q.where(IndicatorNorm.region_id == region_id)
    if direction_id:
        q = q.where(Indicator.direction_id == direction_id)

    allowed = get_user_allowed_directions(current_user)
    if allowed is not None:
        q = q.where(Indicator.direction_id.in_(allowed))

    rows = (await db.execute(q)).scalars().all()
    return [
        IndicatorNormOut(
            id=o.id,
            indicator_id=o.indicator_id,
            indicator_name=o.indicator.name if o.indicator else None,
            region_id=o.region_id,
            region_name=o.region.name if o.region else None,
            norm_value=o.norm_value,
            period_label=o.period_label,
            method=o.method,
            ref_period_start=o.ref_period_start,
            ref_period_end=o.ref_period_end,
            note=o.note,
        )
        for o in rows
    ]
