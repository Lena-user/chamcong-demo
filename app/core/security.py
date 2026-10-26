import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.errors import ServiceUnavailable

ALGORITHM = "HS256"

# Hashing runs on its own small pool: argon2 is CPU- and memory-heavy (default 64 MiB per hash),
# so the number of simultaneous hashes per process is capped by HASH_CONCURRENCY.
_executor: ThreadPoolExecutor | None = None
_semaphore: asyncio.Semaphore | None = None
_waiting = 0


@lru_cache
def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost, memory_cost=s.argon2_memory_kib, parallelism=s.argon2_parallelism
    )


def _hash(password: str) -> str:
    return _hasher().hash(password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher().verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def _run_hash(fn, *args):
    global _executor, _semaphore, _waiting
    s = get_settings()
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=s.hash_concurrency, thread_name_prefix="argon2")
        _semaphore = asyncio.Semaphore(s.hash_concurrency)
    if _waiting >= s.login_max_waiting:
        raise ServiceUnavailable("Too many password checks in progress, retry shortly")
    _waiting += 1
    try:
        async with _semaphore:
            return await asyncio.get_running_loop().run_in_executor(_executor, fn, *args)
    finally:
        _waiting -= 1


async def hash_password(password: str) -> str:
    return await _run_hash(_hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await _run_hash(_verify, password_hash, password)


def create_access_token(employee_id: int, role: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(employee_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=s.jwt_expire_hours),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError on invalid or expired tokens."""
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
