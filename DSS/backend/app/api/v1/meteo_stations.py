from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import MeteoStation
from app.models.user import User
from app.schemas.data import MeteoStationOut

router = APIRouter(prefix="/meteo-stations", tags=["meteo-stations"])


@router.get("", response_model=list[MeteoStationOut])
async def list_meteo_stations(
    is_active: bool | None = Query(None),
    region_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Справочник метеостанций СКО (станция → район + координаты)."""
    q = select(MeteoStation)
    if is_active is not None:
        q = q.where(MeteoStation.is_active == is_active)
    if region_id is not None:
        q = q.where(MeteoStation.region_id == region_id)
    q = q.order_by(MeteoStation.name)
    result = await db.execute(q)
    return [MeteoStationOut.model_validate(s) for s in result.scalars().all()]
