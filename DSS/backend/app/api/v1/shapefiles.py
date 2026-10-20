import asyncio
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.core.cache import cache_get, cache_set
from app.deps import get_current_active_user
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/shapefiles', tags=['shapefiles'])
MAPS_DIR = Path(settings.MAPS_DIR)


def _safe_name(name: str) -> bool:
    return bool(re.fullmatch(r'[^/\\<>:"|?*\x00-\x1f]+', name)) and '..' not in name


def _list_shapefiles() -> list[dict]:
    if not MAPS_DIR.is_dir():
        return []
    results = []
    for p in sorted(MAPS_DIR.glob('*.shp')):
        stem = p.stem
        results.append({'name': stem, 'display_name': stem.replace('_', ' ')})
    results.sort(key=lambda x: x['display_name'])
    return results


def _read_geojson(name: str) -> dict:
    import geopandas as gpd

    shp_path = MAPS_DIR / f'{name}.shp'
    if not shp_path.exists():
        raise FileNotFoundError(f'Shapefile not found: {name}')
    try:
        gdf = gpd.read_file(str(shp_path))
    except Exception as e:
        raise ValueError(f'Cannot read shapefile: {e}') from e
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    return json.loads(gdf.to_json(ensure_ascii=False))


@router.get('')
async def list_shapefiles(_: User = Depends(get_current_active_user)):
    cached = await cache_get('shapefiles:list')
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _list_shapefiles)
    await cache_set('shapefiles:list', data, 300)
    return data


@router.get('/geojson')
async def shapefile_geojson(
    name: str = Query(...),
    _: User = Depends(get_current_active_user),
):
    if not _safe_name(name):
        raise HTTPException(status_code=422, detail='Invalid shapefile name')
    cache_key = f'shapefiles:geojson:{name}'
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    try:
        geojson = await loop.run_in_executor(None, _read_geojson, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f'Shapefile not found: {name}')
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await cache_set(cache_key, geojson, 1800)
    return geojson
