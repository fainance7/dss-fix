"""
Redis cache utility for DSS backend.
Usage:
    from app.core.cache import get_cache, cache_get, cache_set, cache_delete
"""
import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def get_cache() -> aioredis.Redis | None:
    """Return a Redis connection, or None if Redis is unavailable."""
    global _redis
    if _redis is None:
        try:
            _redis = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await _redis.ping()
        except Exception as e:
            logger.warning("Redis unavailable, caching disabled: %s", e)
            _redis = None
    return _redis


async def cache_get(key: str) -> Any | None:
    """Return cached value or None."""
    r = await get_cache()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception as e:
        logger.warning("Cache GET error for %s: %s", key, e)
        return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    """Store value in cache with TTL (seconds)."""
    r = await get_cache()
    if r is None:
        return
    try:
        await r.set(key, json.dumps(value), ex=ttl)
    except Exception as e:
        logger.warning("Cache SET error for %s: %s", key, e)


async def cache_delete(pattern: str) -> None:
    """Delete all keys matching pattern (e.g. 'regions:*')."""
    r = await get_cache()
    if r is None:
        return
    try:
        keys = await r.keys(pattern)
        if keys:
            await r.delete(*keys)
    except Exception as e:
        logger.warning("Cache DELETE error for %s: %s", pattern, e)
