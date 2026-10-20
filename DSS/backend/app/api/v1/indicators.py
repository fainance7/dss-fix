from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import Direction, Indicator, Unit
from app.models.user import User
from app.schemas.data import IndicatorCreate, IndicatorOut, IndicatorUpdate

router = APIRouter(prefix="/indicators", tags=["indicators"])


def _to_out(obj: Indicator) -> IndicatorOut:
    return IndicatorOut(
        id=obj.id,
        direction_id=obj.direction_id,
        direction_name=obj.direction.name if obj.direction else None,
        unit_id=obj.unit_id,
        unit_name=obj.unit.name if obj.unit else None,
        name=obj.name,
        sort_order=obj.sort_order,
        is_active=obj.is_active,
        created_at=obj.created_at,
    )


@router.get("", response_model=list[IndicatorOut])
async def list_indicators(
    direction_id: UUID | None = Query(None),
    is_active: bool | None = Query(None),
    search: str | None = Query(None, min_length=2),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    q = (
        select(Indicator)
        .options(selectinload(Indicator.direction), selectinload(Indicator.unit))
    )
    if direction_id is not None:
        q = q.where(Indicator.direction_id == direction_id)
    if is_active is not None:
        q = q.where(Indicator.is_active == is_active)
    if search:
        q = q.where(Indicator.name.ilike(f"%{search}%"))
    q = q.order_by(Indicator.sort_order, Indicator.name)

    result = await db.execute(q)
    return [_to_out(o) for o in result.scalars().all()]


@router.post("", response_model=IndicatorOut, status_code=201)
async def create_indicator(
    body: IndicatorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    direction = (
        await db.execute(select(Direction).where(Direction.id == body.direction_id))
    ).scalar_one_or_none()
    if direction is None:
        raise HTTPException(status_code=400, detail="Направление не найдено")

    unit = (
        await db.execute(select(Unit).where(Unit.id == body.unit_id))
    ).scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=400, detail="Единица измерения не найдена")

    duplicate = (
        await db.execute(
            select(Indicator).where(
                Indicator.direction_id == body.direction_id,
                Indicator.name == body.name,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail="Показатель с таким наименованием уже существует в направлении",
        )

    obj = Indicator(**body.model_dump())
    db.add(obj)
    await db.flush()
    await log_action(
        db, user_id=current_user.id, action="create",
        entity_type="indicator", entity_id=obj.id,
        details=body.model_dump(mode="json"),
    )
    await db.refresh(obj, ["direction", "unit"])
    return _to_out(obj)


@router.patch("/{indicator_id}", response_model=IndicatorOut)
async def update_indicator(
    indicator_id: UUID,
    body: IndicatorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    obj = (
        await db.execute(
            select(Indicator)
            .options(selectinload(Indicator.direction), selectinload(Indicator.unit))
            .where(Indicator.id == indicator_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Показатель не найден")

    changes = body.model_dump(exclude_unset=True)
    changes_log = body.model_dump(exclude_unset=True, mode="json")

    if "unit_id" in changes:
        unit = (
            await db.execute(select(Unit).where(Unit.id == changes["unit_id"]))
        ).scalar_one_or_none()
        if unit is None:
            raise HTTPException(status_code=400, detail="Единица измерения не найдена")

    if "direction_id" in changes:
        direction = (
            await db.execute(select(Direction).where(Direction.id == changes["direction_id"]))
        ).scalar_one_or_none()
        if direction is None:
            raise HTTPException(status_code=400, detail="Направление не найдено")

    new_dir_id = changes.get("direction_id", obj.direction_id)
    new_name = changes.get("name", obj.name)
    if "direction_id" in changes or "name" in changes:
        duplicate = (
            await db.execute(
                select(Indicator).where(
                    Indicator.direction_id == new_dir_id,
                    Indicator.name == new_name,
                    Indicator.id != indicator_id,
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail="Показатель с таким наименованием уже существует в направлении",
            )

    for k, v in changes.items():
        setattr(obj, k, v)

    await log_action(
        db, user_id=current_user.id, action="update",
        entity_type="indicator", entity_id=obj.id, details=changes_log,
    )
    await db.flush()
    await db.refresh(obj, ["direction", "unit"])
    return _to_out(obj)


@router.delete("/{indicator_id}", status_code=204)
async def delete_indicator(
    indicator_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    obj = (
        await db.execute(select(Indicator).where(Indicator.id == indicator_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Показатель не найден")

    await db.delete(obj)
    await log_action(
        db, user_id=current_user.id, action="delete",
        entity_type="indicator", entity_id=indicator_id,
    )
