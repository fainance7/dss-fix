from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import EconomicSector
from app.models.user import User
from app.schemas.data import EconomicSectorOut

router = APIRouter(prefix="/economic-sectors", tags=["economic-sectors"])


@router.get("", response_model=list[EconomicSectorOut])
async def list_economic_sectors(
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Справочник секторов экономики (ОКЭД) для отображения на дашбордах."""
    q = select(EconomicSector)
    if is_active is not None:
        q = q.where(EconomicSector.is_active == is_active)
    q = q.order_by(EconomicSector.sort_order)
    result = await db.execute(q)
    return [EconomicSectorOut.model_validate(s) for s in result.scalars().all()]
