import json
import logging
from typing import Any, Optional
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)
_redis: Optional[aioredis.Redis] = None


async def init_cache():
    global _redis
    try:
        _redis = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        await _redis.ping()
        logger.info("Redis connected: %s", settings.REDIS_URL)
    except Exception as e:
        logger.warning("Redis unavailable (%s) — running without cache", e)
        _redis = None


async def close_cache():
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


async def get(key: str) -> Optional[Any]:
    if not _redis:
        return None
    try:
        val = await _redis.get(key)
        if val:
            logger.info("CACHE  HIT   %s", key)
            return json.loads(val)
        logger.info("CACHE  MISS  %s", key)
        return None
    except Exception as e:
        logger.debug("Cache get error: %s", e)
        return None


async def set(key: str, value: Any, ttl: int = 60):
    if not _redis:
        return
    try:
        await _redis.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.debug("Cache set error: %s", e)


async def delete(key: str):
    if _redis:
        await _redis.delete(key)
