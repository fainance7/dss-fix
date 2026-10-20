import math
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.cache import cache_get, cache_set, cache_delete
from app.core.rbac import get_user_allowed_directions, require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import DataCategory, DataLayer, DataLayerHistory, DataSource, Direction
from app.models.user import User
from app.schemas.data import (
    DataLayerCreate,
    DataLayerHistoryOut,
    DataLayerOut,
    DataLayerUpdate,
    ListResponse,
)

router = APIRouter(prefix="/data-layers", tags=["data-layers"])


def _build_layer_out(layer: DataLayer) -> DataLayerOut:
    return DataLayerOut(
        id=layer.id,
        name=layer.name,
        description=layer.description,
        category_id=layer.category_id,
        category_name=layer.category.name if layer.category else None,
        source_id=layer.source_id,
        source_name=layer.source.name if layer.source else None,
        direction_id=layer.direction_id,
        direction_name=layer.direction.name if layer.direction else None,
        version=layer.version,
        spatial_resolution=layer.spatial_resolution,
        update_frequency=layer.update_frequency,
        owner=layer.owner,
        accuracy_description=layer.accuracy_description,
        valid_from=layer.valid_from,
        valid_to=layer.valid_to,
        status=layer.status,
        created_by=layer.created_by,
        created_at=layer.created_at,
        updated_at=layer.updated_at,
    )


async def _get_or_404(db: AsyncSession, layer_id: UUID) -> DataLayer:
    result = await db.execute(
        select(DataLayer)
        .options(
            selectinload(DataLayer.category),
            selectinload(DataLayer.source),
            selectinload(DataLayer.direction),
        )
        .where(DataLayer.id == layer_id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="DataLayer not found")
    return obj


@router.get("", response_model=ListResponse)
async def list_layers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    category_id: Optional[UUID] = None,
    source_id: Optional[UUID] = None,
    direction_id: Optional[UUID] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Direction-based access for restricted exp_dir users
    allowed_dirs = get_user_allowed_directions(current_user)

    cache_key = f"data_layers:p{page}:ps{page_size}:cat{category_id}:src{source_id}:dir{direction_id}:st{status}:ad{allowed_dirs}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return ListResponse(**cached)

    q = select(DataLayer).options(
        selectinload(DataLayer.category),
        selectinload(DataLayer.source),
        selectinload(DataLayer.direction),
    )
    if category_id:
        q = q.where(DataLayer.category_id == category_id)
    if source_id:
        q = q.where(DataLayer.source_id == source_id)
    if direction_id:
        q = q.where(DataLayer.direction_id == direction_id)
    if status:
        q = q.where(DataLayer.status == status)
    # Auto-filter for restricted exp_dir users
    if allowed_dirs is not None:
        q = q.where(DataLayer.direction_id.in_(allowed_dirs))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (
        await db.execute(
            q.order_by(DataLayer.name).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    result = ListResponse(
        items=[_build_layer_out(i) for i in items],
        total=total, page=page, page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )
    await cache_set(cache_key, result.model_dump(mode='json'), 300)  # 5 min
    return result


@router.post("", response_model=DataLayerOut, status_code=201)
async def create_layer(
    body: DataLayerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    # Verify FK references exist
    cat = (await db.execute(select(DataCategory).where(DataCategory.id == body.category_id))).scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="DataCategory not found")
    src = (await db.execute(select(DataSource).where(DataSource.id == body.source_id))).scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="DataSource not found")
    direction = (await db.execute(select(Direction).where(Direction.id == body.direction_id))).scalar_one_or_none()
    if not direction:
        raise HTTPException(status_code=404, detail="Direction not found")

    # Check exp_dir restriction
    allowed_dirs = get_user_allowed_directions(current_user)
    if allowed_dirs is not None and body.direction_id not in allowed_dirs:
        raise HTTPException(status_code=403, detail="You are not allowed to create layers in this direction")

    layer = DataLayer(**body.model_dump(), created_by=current_user.id)
    db.add(layer)
    await db.flush()

    # Record history
    hist = DataLayerHistory(
        layer_id=layer.id,
        changed_by=current_user.id,
        change_type="create",
        new_values=body.model_dump(mode="json"),
    )
    db.add(hist)

    await log_action(db, user_id=current_user.id, action="create", entity_type="data_layer", entity_id=layer.id)
    # Список каталога кешируется на 5 мин — после изменения сбрасываем.
    await cache_delete("data_layers:*")
    layer = await _get_or_404(db, layer.id)
    return _build_layer_out(layer)


@router.get("/{layer_id}", response_model=DataLayerOut)
async def get_layer(
    layer_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    return _build_layer_out(await _get_or_404(db, layer_id))


@router.patch("/{layer_id}", response_model=DataLayerOut)
async def update_layer(
    layer_id: UUID,
    body: DataLayerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    layer = await _get_or_404(db, layer_id)
    changes = body.model_dump(exclude_none=True)
    old_values = {k: getattr(layer, k) for k in changes}

    for k, v in changes.items():
        setattr(layer, k, v)

    # Record history
    hist = DataLayerHistory(
        layer_id=layer.id,
        changed_by=current_user.id,
        change_type="update",
        old_values={k: str(v) for k, v in old_values.items()},
        new_values={k: str(v) for k, v in changes.items()},
    )
    db.add(hist)
    await log_action(db, user_id=current_user.id, action="update", entity_type="data_layer", entity_id=layer.id, details=changes)
    # Список каталога кешируется на 5 мин — после изменения сбрасываем.
    await cache_delete("data_layers:*")

    layer = await _get_or_404(db, layer_id)
    return _build_layer_out(layer)


@router.delete("/{layer_id}", status_code=204)
async def delete_layer(
    layer_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    layer = await _get_or_404(db, layer_id)
    await db.delete(layer)
    await log_action(db, user_id=current_user.id, action="delete", entity_type="data_layer", entity_id=layer_id)
    # Список каталога кешируется на 5 мин — после изменения сбрасываем.
    await cache_delete("data_layers:*")


@router.get("/{layer_id}/history", response_model=list[DataLayerHistoryOut])
async def get_layer_history(
    layer_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    await _get_or_404(db, layer_id)
    result = await db.execute(
        select(DataLayerHistory)
        .options(selectinload(DataLayerHistory.changed_by_user))
        .where(DataLayerHistory.layer_id == layer_id)
        .order_by(DataLayerHistory.changed_at.desc())
    )
    rows = result.scalars().all()
    return [
        DataLayerHistoryOut(
            id=r.id,
            layer_id=r.layer_id,
            changed_by=r.changed_by,
            changed_by_username=r.changed_by_user.username if r.changed_by_user else None,
            change_type=r.change_type,
            old_values=r.old_values,
            new_values=r.new_values,
            changed_at=r.changed_at,
        )
        for r in rows
    ]
