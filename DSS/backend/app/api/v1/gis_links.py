"""Связи каталога GIS (dssgis.dwh.kz) с доменом DSS.

Категория GIS ↔ направление, датасет GIS ↔ показатель. Каталог GIS живёт во внешнем
сервисе (проксируется через /api/v1/gis/*); здесь — только персистентное сопоставление
внешних UUID с нашими directions/indicators. Upsert по внешнему GIS-идентификатору
(идемпотентен). Доступ — те же роли, что и у GIS-прокси.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.models.data import (
    Direction,
    GisCategoryLink,
    GisDatasetLink,
    Indicator,
)
from app.models.user import User
from app.schemas.gis_links import (
    GisCategoryLinkOut,
    GisCategoryLinkUpsert,
    GisDatasetLinkOut,
    GisDatasetLinkUpsert,
)

router = APIRouter(
    prefix="/gis-links",
    tags=["gis-links"],
    dependencies=[Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader"))],
)


def _category_out(obj: GisCategoryLink) -> GisCategoryLinkOut:
    return GisCategoryLinkOut(
        id=obj.id,
        gis_category_id=obj.gis_category_id,
        gis_category_code=obj.gis_category_code,
        gis_category_name=obj.gis_category_name,
        direction_id=obj.direction_id,
        direction_name=obj.direction.name if obj.direction else None,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def _dataset_out(obj: GisDatasetLink) -> GisDatasetLinkOut:
    indicator = obj.indicator
    return GisDatasetLinkOut(
        id=obj.id,
        gis_dataset_id=obj.gis_dataset_id,
        gis_dataset_name=obj.gis_dataset_name,
        gis_category_id=obj.gis_category_id,
        indicator_id=obj.indicator_id,
        indicator_name=indicator.name if indicator else None,
        direction_id=indicator.direction_id if indicator else None,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


# ── Категория GIS → направление ───────────────────────────────

@router.get("/categories", response_model=list[GisCategoryLinkOut])
async def list_category_links(db: AsyncSession = Depends(get_db)):
    q = select(GisCategoryLink).options(selectinload(GisCategoryLink.direction))
    result = await db.execute(q)
    return [_category_out(o) for o in result.scalars().all()]


@router.put("/categories", response_model=GisCategoryLinkOut)
async def upsert_category_link(
    body: GisCategoryLinkUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    direction = (
        await db.execute(select(Direction).where(Direction.id == body.direction_id))
    ).scalar_one_or_none()
    if direction is None:
        raise HTTPException(status_code=400, detail="Направление не найдено")

    obj = (
        await db.execute(
            select(GisCategoryLink).where(
                GisCategoryLink.gis_category_id == body.gis_category_id
            )
        )
    ).scalar_one_or_none()

    if obj is None:
        obj = GisCategoryLink(**body.model_dump())
        db.add(obj)
        action = "create"
    else:
        for k, v in body.model_dump().items():
            setattr(obj, k, v)
        action = "update"

    await db.flush()
    await log_action(
        db, user_id=current_user.id, action=action,
        entity_type="gis_category_link", entity_id=obj.id,
        details=body.model_dump(mode="json"),
    )
    obj = (
        await db.execute(
            select(GisCategoryLink)
            .options(selectinload(GisCategoryLink.direction))
            .where(GisCategoryLink.id == obj.id)
        )
    ).scalar_one()
    return _category_out(obj)


@router.delete("/categories/{gis_category_id}", status_code=204)
async def delete_category_link(
    gis_category_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    obj = (
        await db.execute(
            select(GisCategoryLink).where(
                GisCategoryLink.gis_category_id == gis_category_id
            )
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Связь категории не найдена")

    entity_id = obj.id
    await db.delete(obj)
    await log_action(
        db, user_id=current_user.id, action="delete",
        entity_type="gis_category_link", entity_id=entity_id,
    )


# ── Датасет GIS → показатель ──────────────────────────────────

@router.get("/datasets", response_model=list[GisDatasetLinkOut])
async def list_dataset_links(
    gis_category_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(GisDatasetLink).options(
        selectinload(GisDatasetLink.indicator).selectinload(Indicator.direction)
    )
    if gis_category_id is not None:
        q = q.where(GisDatasetLink.gis_category_id == gis_category_id)
    result = await db.execute(q)
    return [_dataset_out(o) for o in result.scalars().all()]


@router.put("/datasets", response_model=GisDatasetLinkOut)
async def upsert_dataset_link(
    body: GisDatasetLinkUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    indicator = (
        await db.execute(select(Indicator).where(Indicator.id == body.indicator_id))
    ).scalar_one_or_none()
    if indicator is None:
        raise HTTPException(status_code=400, detail="Показатель не найден")

    obj = (
        await db.execute(
            select(GisDatasetLink).where(
                GisDatasetLink.gis_dataset_id == body.gis_dataset_id
            )
        )
    ).scalar_one_or_none()

    if obj is None:
        obj = GisDatasetLink(**body.model_dump())
        db.add(obj)
        action = "create"
    else:
        for k, v in body.model_dump().items():
            setattr(obj, k, v)
        action = "update"

    await db.flush()
    await log_action(
        db, user_id=current_user.id, action=action,
        entity_type="gis_dataset_link", entity_id=obj.id,
        details=body.model_dump(mode="json"),
    )
    obj = (
        await db.execute(
            select(GisDatasetLink)
            .options(selectinload(GisDatasetLink.indicator).selectinload(Indicator.direction))
            .where(GisDatasetLink.id == obj.id)
        )
    ).scalar_one()
    return _dataset_out(obj)


@router.delete("/datasets/{gis_dataset_id}", status_code=204)
async def delete_dataset_link(
    gis_dataset_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader")),
):
    obj = (
        await db.execute(
            select(GisDatasetLink).where(
                GisDatasetLink.gis_dataset_id == gis_dataset_id
            )
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Связь датасета не найдена")

    entity_id = obj.id
    await db.delete(obj)
    await log_action(
        db, user_id=current_user.id, action="delete",
        entity_type="gis_dataset_link", entity_id=entity_id,
    )
