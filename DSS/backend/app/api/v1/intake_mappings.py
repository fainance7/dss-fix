"""CRUD + run для intake_mappings — UI-редактируемых правил ETL из reports_db."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.models.data import Indicator
from app.models.data_intake import IntakeMapping
from app.models.user import User
from app.schemas.data_intake_mapping import (
    IntakeMappingCreate,
    IntakeMappingOut,
    IntakeMappingUpdate,
)
from app.services import remote_mapping_runner

router = APIRouter(prefix="/data-intake/mappings", tags=["data-intake-mappings"])


def _to_out(m: IntakeMapping) -> IntakeMappingOut:
    base = IntakeMappingOut.model_validate(m)
    base.indicator_name = m.indicator.name if m.indicator else None
    return base


@router.get("", response_model=list[IntakeMappingOut])
async def list_mappings(
    indicator_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    q = select(IntakeMapping).options(selectinload(IntakeMapping.indicator))
    if indicator_id:
        q = q.where(IntakeMapping.indicator_id == indicator_id)
    q = q.order_by(IntakeMapping.remote_table, IntakeMapping.created_at.desc())
    items = (await db.execute(q)).scalars().all()
    return [_to_out(m) for m in items]


@router.get("/{mapping_id}", response_model=IntakeMappingOut)
async def get_mapping(
    mapping_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    m = (
        await db.execute(
            select(IntakeMapping)
            .options(selectinload(IntakeMapping.indicator))
            .where(IntakeMapping.id == mapping_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="Mapping не найден")
    return _to_out(m)


@router.post("", response_model=IntakeMappingOut, status_code=201)
async def create_mapping(
    body: IntakeMappingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "data_uploader")),
):
    # Sanity-check indicator exists
    ind = (
        await db.execute(select(Indicator).where(Indicator.id == body.indicator_id))
    ).scalar_one_or_none()
    if ind is None:
        raise HTTPException(status_code=400, detail="Indicator не найден")

    payload = body.model_dump()
    payload["period_columns_idx"] = [pc.model_dump() if hasattr(pc, "model_dump") else pc for pc in body.period_columns_idx]
    payload["created_by"] = current_user.id
    obj = IntakeMapping(**payload)
    db.add(obj)
    try:
        await db.flush()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(e))

    await log_action(
        db, user_id=current_user.id, action="create",
        entity_type="intake_mapping", entity_id=obj.id,
        details={"remote_table": body.remote_table, "indicator_id": str(body.indicator_id)},
    )
    await db.refresh(obj)
    await db.refresh(obj, ["indicator"])
    return _to_out(obj)


@router.patch("/{mapping_id}", response_model=IntakeMappingOut)
async def update_mapping(
    mapping_id: UUID,
    body: IntakeMappingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "data_uploader")),
):
    m = (
        await db.execute(
            select(IntakeMapping).where(IntakeMapping.id == mapping_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="Mapping не найден")

    changes = body.model_dump(exclude_unset=True)
    if "period_columns_idx" in changes and changes["period_columns_idx"] is not None:
        changes["period_columns_idx"] = [
            pc.model_dump() if hasattr(pc, "model_dump") else pc
            for pc in changes["period_columns_idx"]
        ]
    for k, v in changes.items():
        setattr(m, k, v)
    await db.flush()
    await db.refresh(m)
    await db.refresh(m, ["indicator"])
    await log_action(
        db, user_id=current_user.id, action="update",
        entity_type="intake_mapping", entity_id=m.id,
        details=changes,
    )
    return _to_out(m)


@router.post("/{mapping_id}/run")
async def run_mapping(
    mapping_id: UUID,
    dry_run: bool = Query(True, description="true — только валидация, false — UPSERT в indicator_values"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "data_uploader")),
):
    m = (
        await db.execute(
            select(IntakeMapping).where(IntakeMapping.id == mapping_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="Mapping не найден")

    try:
        result = await remote_mapping_runner.run_mapping(
            db, mapping=m, user=current_user, do_commit=not dry_run
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ошибка ETL: {e}")

    await log_action(
        db,
        user_id=current_user.id,
        action="run_dry" if dry_run else "run_commit",
        entity_type="intake_mapping",
        entity_id=m.id,
        details={
            "rows_total": result.rows_total,
            "rows_ok": result.rows_ok,
            "rows_imported": result.rows_imported,
        },
    )
    return {
        "job_id": result.job_id,
        "dry_run": dry_run,
        "rows_total": result.rows_total,
        "rows_ok": result.rows_ok,
        "rows_invalid": result.rows_invalid,
        "rows_imported": result.rows_imported,
        "sample_errors": result.sample_errors,
    }


@router.delete("/{mapping_id}", status_code=204)
async def delete_mapping(
    mapping_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "data_uploader")),
):
    m = (
        await db.execute(select(IntakeMapping).where(IntakeMapping.id == mapping_id))
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="Mapping не найден")
    await db.delete(m)
    await log_action(
        db, user_id=current_user.id, action="delete",
        entity_type="intake_mapping", entity_id=mapping_id,
    )
