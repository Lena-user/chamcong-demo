import asyncio
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_hasher = PasswordHasher()
ALGORITHM = "HS256"


def _hash(password: str) -> str:
    return _hasher.hash(password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(_verify, password_hash, password)


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
