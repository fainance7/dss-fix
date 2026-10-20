import math
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import DataSource
from app.models.user import User
from app.schemas.data import DataSourceCreate, DataSourceOut, DataSourceUpdate, ListResponse

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


async def _get_or_404(db: AsyncSession, source_id: UUID) -> DataSource:
    result = await db.execute(select(DataSource).where(DataSource.id == source_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="DataSource not found")
    return obj


@router.get("", response_model=ListResponse)
async def list_sources(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    q = select(DataSource)
    if is_active is not None:
        q = q.where(DataSource.is_active == is_active)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (await db.execute(q.order_by(DataSource.name).offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return ListResponse(
        items=[DataSourceOut.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.post("", response_model=DataSourceOut, status_code=201)
async def create_source(
    body: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    obj = DataSource(**body.model_dump())
    db.add(obj)
    await db.flush()
    await log_action(db, user_id=current_user.id, action="create", entity_type="data_source", entity_id=obj.id)
    await db.refresh(obj)
    return DataSourceOut.model_validate(obj)


@router.get("/{source_id}", response_model=DataSourceOut)
async def get_source(
    source_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    return DataSourceOut.model_validate(await _get_or_404(db, source_id))


@router.patch("/{source_id}", response_model=DataSourceOut)
async def update_source(
    source_id: UUID,
    body: DataSourceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    obj = await _get_or_404(db, source_id)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await log_action(db, user_id=current_user.id, action="update", entity_type="data_source", entity_id=obj.id, details=changes)
    return DataSourceOut.model_validate(obj)


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    obj = await _get_or_404(db, source_id)
    await db.delete(obj)
    await log_action(db, user_id=current_user.id, action="delete", entity_type="data_source", entity_id=source_id)
