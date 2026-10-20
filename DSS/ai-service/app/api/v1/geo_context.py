"""
GET /api/v1/geo-context/{direction}/{region_id}

Returns a compact JSON payload (simplified GeoJSON + per-region
indicator aggregates) used by the LLM narrative endpoint and by the
frontend dashboards.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.geo_context import (
    DEFAULT_SIMPLIFY_TOLERANCE,
    build_geo_context,
)

router = APIRouter(prefix="/geo-context", tags=["geo-context"])


@router.get("/{direction_code}/{region_id}")
async def get_geo_context(
    direction_code: str,
    region_id: UUID,
    simplify_tolerance: float = Query(DEFAULT_SIMPLIFY_TOLERANCE, ge=0.0001, le=0.5),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await build_geo_context(
            db,
            direction_code=direction_code,
            region_id=region_id,
            simplify_tolerance=simplify_tolerance,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
