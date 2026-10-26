from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None


async def init_redis() -> ArqRedis:
    """Create the shared Arq/Redis pool (called from the app lifespan)."""
    global _pool
    _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def get_redis() -> ArqRedis:
    if _pool is None:
        raise RuntimeError("Redis pool is not initialised")
    return _pool
