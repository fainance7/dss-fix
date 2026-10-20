import math
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import mapping, shape
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_action
from app.core.rbac import get_user_allowed_directions, get_user_region_scope, require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import DataLayer, SpatialData
from app.models.user import Region, User
from app.schemas.data import (
    ListResponse,
    SpatialDataCreate,
    SpatialDataFeatureCollection,
    SpatialDataOut,
    ValidateRequest,
)

router = APIRouter(prefix="/spatial-data", tags=["spatial-data"])


def _geom_to_geojson(geom) -> Optional[dict]:
    if geom is None:
        return None
    try:
        return mapping(to_shape(geom))
    except Exception:
        return None


def _build_out(sd: SpatialData) -> SpatialDataOut:
    return SpatialDataOut(
        id=sd.id,
        layer_id=sd.layer_id,
        layer_name=sd.layer.name if sd.layer else None,
        region_id=sd.region_id,
        region_name=sd.region.name if sd.region else None,
        data_date=sd.data_date,
        geojson=_geom_to_geojson(sd.geom),
        storage_path=sd.storage_path,
        properties=sd.properties,
        validation_status=sd.validation_status,
        validated_by=sd.validated_by,
        validated_by_username=sd.validated_by_user.username if sd.validated_by_user else None,
        validated_at=sd.validated_at,
        created_at=sd.created_at,
    )


async def _get_or_404(db: AsyncSession, sd_id: UUID) -> SpatialData:
    result = await db.execute(
        select(SpatialData)
        .options(
            selectinload(SpatialData.layer),
            selectinload(SpatialData.region),
            selectinload(SpatialData.validated_by_user),
        )
        .where(SpatialData.id == sd_id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="SpatialData not found")
    return obj


@router.get("", response_model=ListResponse)
async def list_spatial_data(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    layer_id: Optional[UUID] = None,
    region_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    validation_status: Optional[str] = Query(None, pattern="^(pending|approved|rejected)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    q = select(SpatialData).options(
        selectinload(SpatialData.layer),
        selectinload(SpatialData.region),
        selectinload(SpatialData.validated_by_user),
    )
    # G9: akim_ray видит только данные своего района
    scope = get_user_region_scope(current_user)
    if scope is not None:
        if region_id is not None and region_id not in scope:
            raise HTTPException(status_code=403, detail="Доступ только к данным своего района")
        q = q.where(SpatialData.region_id.in_(scope))
    if layer_id:
        q = q.where(SpatialData.layer_id == layer_id)
    if region_id:
        q = q.where(SpatialData.region_id == region_id)
    if date_from:
        q = q.where(SpatialData.data_date >= date_from)
    if date_to:
        q = q.where(SpatialData.data_date <= date_to)
    if validation_status:
        q = q.where(SpatialData.validation_status == validation_status)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (
        await db.execute(
            q.order_by(SpatialData.data_date.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return ListResponse(
        items=[_build_out(i) for i in items],
        total=total, page=page, page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/geojson", response_model=SpatialDataFeatureCollection)
async def get_as_geojson(
    layer_id: UUID,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    validation_status: str = Query("approved", pattern="^(pending|approved|rejected)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Возвращает SpatialData в виде GeoJSON FeatureCollection для карты."""
    q = select(SpatialData).options(
        selectinload(SpatialData.layer),
        selectinload(SpatialData.region),
        selectinload(SpatialData.validated_by_user),
    ).where(SpatialData.layer_id == layer_id, SpatialData.validation_status == validation_status)

    # G9: akim_ray видит только данные своего района
    scope = get_user_region_scope(current_user)
    if scope is not None:
        q = q.where(SpatialData.region_id.in_(scope))

    if date_from:
        q = q.where(SpatialData.data_date >= date_from)
    if date_to:
        q = q.where(SpatialData.data_date <= date_to)

    rows = (await db.execute(q.order_by(SpatialData.data_date.desc()))).scalars().all()

    features = []
    for sd in rows:
        geom = _geom_to_geojson(sd.geom)
        features.append({
            "type": "Feature",
            "id": str(sd.id),
            "geometry": geom,
            "properties": {
                **(sd.properties or {}),
                "id": str(sd.id),
                "layer_id": str(sd.layer_id),
                "layer_name": sd.layer.name if sd.layer else None,
                "region_id": str(sd.region_id) if sd.region_id else None,
                "region_name": sd.region.name if sd.region else None,
                "data_date": sd.data_date.isoformat(),
                "validation_status": sd.validation_status,
            },
        })

    return SpatialDataFeatureCollection(features=features, total=len(features))


@router.post("", response_model=SpatialDataOut, status_code=201)
async def create_spatial_data(
    body: SpatialDataCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    # Validate layer exists
    layer = (await db.execute(select(DataLayer).where(DataLayer.id == body.layer_id))).scalar_one_or_none()
    if not layer:
        raise HTTPException(status_code=404, detail="DataLayer not found")

    # G10: exp_dir может загружать пространственные данные только по своему
    # направлению (направление берётся из слоя). admin/exp_ver — без ограничения.
    allowed = get_user_allowed_directions(current_user)
    if allowed is not None and layer.direction_id not in allowed:
        raise HTTPException(
            status_code=403,
            detail="Нет прав на загрузку данных этого направления",
        )

    geom = None
    if body.geojson:
        try:
            geom = from_shape(shape(body.geojson), srid=4326)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Invalid GeoJSON geometry: {e}")

    sd = SpatialData(
        layer_id=body.layer_id,
        region_id=body.region_id,
        data_date=body.data_date,
        geom=geom,
        storage_path=body.storage_path,
        properties=body.properties,
        validation_status="pending",
    )
    db.add(sd)
    await db.flush()
    await log_action(
        db, user_id=current_user.id,
        action="upload", entity_type="spatial_data", entity_id=sd.id,
        details={"layer_id": str(body.layer_id), "data_date": str(body.data_date)},
    )
    return _build_out(await _get_or_404(db, sd.id))


@router.get("/{sd_id}", response_model=SpatialDataOut)
async def get_spatial_data(
    sd_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    obj = await _get_or_404(db, sd_id)
    # M-1: акимат района читает только объекты своего района — как в списке и GeoJSON.
    scope = get_user_region_scope(current_user)
    if scope is not None and obj.region_id not in scope:
        raise HTTPException(status_code=404, detail="SpatialData not found")
    return _build_out(obj)


@router.post("/{sd_id}/validate", response_model=SpatialDataOut)
async def validate_spatial_data(
    sd_id: UUID,
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver")),
):
    """Approve или Reject запись. Только exp_ver / admin."""
    sd = await _get_or_404(db, sd_id)
    if sd.validation_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Record already has status '{sd.validation_status}'",
        )

    sd.validation_status = body.status
    sd.validated_by = current_user.id
    sd.validated_at = datetime.now(timezone.utc)

    await log_action(
        db, user_id=current_user.id,
        action=f"validate_{body.status}", entity_type="spatial_data", entity_id=sd_id,
        details={"comment": body.comment},
    )
    return _build_out(await _get_or_404(db, sd_id))


@router.delete("/{sd_id}", status_code=204)
async def delete_spatial_data(
    sd_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver")),
):
    sd = await _get_or_404(db, sd_id)
    await db.delete(sd)
    await log_action(db, user_id=current_user.id, action="delete", entity_type="spatial_data", entity_id=sd_id)
