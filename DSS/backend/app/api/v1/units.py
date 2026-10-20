from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import Indicator, Unit
from app.models.user import User
from app.schemas.data import UnitCreate, UnitOut, UnitUpdate

router = APIRouter(prefix="/units", tags=["units"])


@router.get("", response_model=list[UnitOut])
async def list_units(
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    q = select(Unit)
    if is_active is not None:
        q = q.where(Unit.is_active == is_active)
    q = q.order_by(Unit.name)
    result = await db.execute(q)
    return [UnitOut.model_validate(u) for u in result.scalars().all()]


@router.post("", response_model=UnitOut, status_code=201)
async def create_unit(
    body: UnitCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    existing = (
        await db.execute(select(Unit).where(Unit.name == body.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Единица «{body.name}» уже существует")

    obj = Unit(**body.model_dump())
    db.add(obj)
    await db.flush()
    await log_action(
        db, user_id=current_user.id, action="create",
        entity_type="unit", entity_id=obj.id,
        details=body.model_dump(mode="json"),
    )
    await db.refresh(obj)
    return UnitOut.model_validate(obj)


@router.patch("/{unit_id}", response_model=UnitOut)
async def update_unit(
    unit_id: UUID,
    body: UnitUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    obj = (await db.execute(select(Unit).where(Unit.id == unit_id))).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Единица не найдена")

    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)

    await log_action(
        db, user_id=current_user.id, action="update",
        entity_type="unit", entity_id=obj.id,
        details=body.model_dump(exclude_unset=True, mode="json"),
    )
    await db.flush()
    await db.refresh(obj)
    return UnitOut.model_validate(obj)


@router.delete("/{unit_id}", status_code=204)
async def delete_unit(
    unit_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    obj = (await db.execute(select(Unit).where(Unit.id == unit_id))).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Единица не найдена")

    in_use = (
        await db.execute(select(Indicator.id).where(Indicator.unit_id == unit_id).limit(1))
    ).scalar_one_or_none()
    if in_use is not None:
        raise HTTPException(
            status_code=409,
            detail="Невозможно удалить: единица используется в показателях",
        )

    await db.delete(obj)
    await log_action(
        db, user_id=current_user.id, action="delete",
        entity_type="unit", entity_id=unit_id,
    )
