from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import Region
from app.deps import get_current_active_user
from app.models.user import User
from app.core.cache import cache_get, cache_set

router = APIRouter(prefix='/regions', tags=['regions'])

CACHE_KEY = 'regions:all'
CACHE_TTL = 600  # 10 minutes


@router.get('')
async def list_regions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    cached = await cache_get(CACHE_KEY)
    if cached is not None:
        return cached

    result = await db.execute(select(Region).order_by(Region.level, Region.name))
    regions = result.scalars().all()
    data = [{'id': str(r.id), 'name': r.name, 'code': r.code, 'level': r.level} for r in regions]

    await cache_set(CACHE_KEY, data, CACHE_TTL)
    return data
