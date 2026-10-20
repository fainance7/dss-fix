from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import Direction
from app.models.user import User
from app.schemas.data import DirectionOut

router = APIRouter(prefix="/directions", tags=["directions"])


@router.get("", response_model=list[DirectionOut])
async def list_directions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Список всех направлений (8 фиксированных)."""
    result = await db.execute(select(Direction).order_by(Direction.name))
    return [
        DirectionOut(
            id=d.id, code=d.code, name=d.name, description=d.description,
        )
        for d in result.scalars().all()
    ]
