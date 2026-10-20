import uuid as uuid_lib
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.database import get_db
from app.models.data import DataAnnotation
from app.models.user import User

router = APIRouter(prefix="/annotations", tags=["annotations"])


class AnnotationCreate(BaseModel):
    comment: str = Field(min_length=1, max_length=4000)
    # Точка на карте — для аннотации на карте; для значения в таблице не нужна.
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    layer_id: Optional[UUID] = None
    spatial_data_id: Optional[UUID] = None
    indicator_value_id: Optional[UUID] = None


class AnnotationOut(BaseModel):
    id: UUID
    comment: str
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    layer_id: Optional[UUID] = None
    spatial_data_id: Optional[UUID] = None
    indicator_value_id: Optional[UUID] = None
    author_id: UUID
    author_name: Optional[str] = None
    created_at: Optional[datetime] = None


def _out(ann: DataAnnotation, author_name: Optional[str]) -> AnnotationOut:
    lon = lat = None
    if ann.annotation_point is not None:
        from geoalchemy2.shape import to_shape
        pt = to_shape(ann.annotation_point)
        lon, lat = pt.x, pt.y
    return AnnotationOut(
        id=ann.id,
        comment=ann.comment,
        longitude=lon,
        latitude=lat,
        layer_id=ann.layer_id,
        spatial_data_id=ann.spatial_data_id,
        indicator_value_id=ann.indicator_value_id,
        author_id=ann.author_id,
        author_name=author_name,
        created_at=ann.created_at,
    )


@router.post("", response_model=AnnotationOut, status_code=status.HTTP_201_CREATED)
async def create_annotation(
    body: AnnotationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir")),
):
    """Аннотация (комментарий, отметка о подозрительных данных) к точке на карте,
    объекту, слою или значению показателя в таблице (ТЗ ФFE.1). Доступно admin,
    exp_ver и exp_dir."""
    has_point = body.longitude is not None and body.latitude is not None
    if not (has_point or body.layer_id or body.spatial_data_id or body.indicator_value_id):
        raise HTTPException(status_code=422, detail="Укажите точку на карте, объект или значение показателя")
    ann = DataAnnotation(
        id=uuid_lib.uuid4(),
        comment=body.comment.strip(),
        annotation_point=f"SRID=4326;POINT({body.longitude} {body.latitude})" if has_point else None,
        layer_id=body.layer_id,
        spatial_data_id=body.spatial_data_id,
        indicator_value_id=body.indicator_value_id,
        author_id=current_user.id,
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)
    return _out(ann, current_user.username)


@router.get("", response_model=list[AnnotationOut])
async def list_annotations(
    layer_id: Optional[UUID] = None,
    indicator_value_ids: Optional[str] = Query(None, description="UUID значений через запятую"),
    on_map: bool = Query(False, description="Только аннотации с точкой на карте"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("exp_ver", "exp_dir", "admin")),
):
    """Список аннотаций с автором и датой (фильтры: слой, значения показателя, карта)."""
    q = select(DataAnnotation, User.username).outerjoin(User, DataAnnotation.author_id == User.id)
    if layer_id:
        q = q.where(DataAnnotation.layer_id == layer_id)
    if indicator_value_ids:
        ids = [UUID(x) for x in indicator_value_ids.split(",") if x.strip()][:500]
        q = q.where(DataAnnotation.indicator_value_id.in_(ids))
    if on_map:
        q = q.where(DataAnnotation.annotation_point.isnot(None))
    rows = (await db.execute(q.order_by(DataAnnotation.created_at.desc()).limit(1000))).all()
    return [_out(ann, name) for ann, name in rows]


@router.delete("/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_annotation(
    annotation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("exp_ver", "exp_dir", "admin")),
):
    result = await db.execute(select(DataAnnotation).where(DataAnnotation.id == annotation_id))
    ann = result.scalar_one_or_none()
    if ann is None:
        raise HTTPException(status_code=404, detail="Аннотация не найдена")
    if ann.author_id != current_user.id and not any(ur.role.name == "admin" for ur in current_user.user_roles):
        raise HTTPException(status_code=403, detail="Нельзя удалить чужую аннотацию")
    await db.delete(ann)
    await db.commit()
