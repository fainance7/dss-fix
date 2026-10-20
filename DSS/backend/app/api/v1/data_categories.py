import math
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import DataCategory
from app.models.user import User
from app.schemas.data import DataCategoryCreate, DataCategoryOut, DataCategoryUpdate, ListResponse

router = APIRouter(prefix="/data-categories", tags=["data-categories"])


async def _get_or_404(db: AsyncSession, cat_id: UUID) -> DataCategory:
    result = await db.execute(select(DataCategory).where(DataCategory.id == cat_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="DataCategory not found")
    return obj


@router.get("", response_model=ListResponse)
async def list_categories(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    parent_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    q = select(DataCategory)
    if parent_id is not None:
        q = q.where(DataCategory.parent_id == parent_id)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (await db.execute(q.order_by(DataCategory.name).offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return ListResponse(
        items=[DataCategoryOut.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.post("", response_model=DataCategoryOut, status_code=201)
async def create_category(
    body: DataCategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    # Verify unique name
    existing = (await db.execute(select(DataCategory).where(DataCategory.name == body.name))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Category name already exists")

    obj = DataCategory(**body.model_dump())
    db.add(obj)
    await db.flush()
    await log_action(db, user_id=current_user.id, action="create", entity_type="data_category", entity_id=obj.id)
    await db.refresh(obj)
    return DataCategoryOut.model_validate(obj)


@router.get("/{cat_id}", response_model=DataCategoryOut)
async def get_category(
    cat_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    return DataCategoryOut.model_validate(await _get_or_404(db, cat_id))


@router.patch("/{cat_id}", response_model=DataCategoryOut)
async def update_category(
    cat_id: UUID,
    body: DataCategoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    obj = await _get_or_404(db, cat_id)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await log_action(db, user_id=current_user.id, action="update", entity_type="data_category", entity_id=obj.id, details=changes)
    return DataCategoryOut.model_validate(obj)


@router.delete("/{cat_id}", status_code=204)
async def delete_category(
    cat_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    obj = await _get_or_404(db, cat_id)
    await db.delete(obj)
    await log_action(db, user_id=current_user.id, action="delete", entity_type="data_category", entity_id=cat_id)
