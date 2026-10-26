"""Fixed-window rate limit in Redis. One Lua script = one atomic round trip."""
import logging

from fastapi import Depends, HTTPException, Request, status

from app.core import redis as redis_pool
from app.core.config import get_settings
from app.core.deps import get_current_user
from app.modules.employees.models import Employee

log = logging.getLogger("app.ratelimit")

# KEYS[1] = counter, ARGV[1] = window seconds. Returns {count, ttl}.
_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {n, redis.call('TTL', KEYS[1])}
"""
WINDOW_SECONDS = 60


async def check(scope: str, identity: str, limit: int) -> None:
    """Raises 429 (with Retry-After) when `identity` exceeded `limit` hits in the window."""
    s = get_settings()
    if not s.rate_limit_enabled:
        return
    try:
        count, ttl = await redis_pool.get_redis().eval(_SCRIPT, 1, f"rl:{scope}:{identity}", WINDOW_SECONDS)
    except Exception:
        log.exception("rate limiter unavailable (fail_open=%s)", s.rate_limit_fail_open)
        if s.rate_limit_fail_open:
            return
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Rate limiter unavailable",
                            headers={"Retry-After": str(s.retry_after_seconds)})
    if count > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests",
                            headers={"Retry-After": str(max(1, int(ttl)))})


async def limit_login(code: str) -> None:
    await check("login", code.strip().lower(), get_settings().rate_login_per_min)


async def limit_writes(user: Employee = Depends(get_current_user)) -> None:
    """Dependency for write endpoints: per-user cap."""
    await check("write", str(user.id), get_settings().rate_write_per_min)
