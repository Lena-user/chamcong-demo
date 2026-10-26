"""Per-process TTL cache of the identity lookup done on every authenticated request.

Trade-off: a change made elsewhere (another worker process, or a direct DB edit) is seen after
at most AUTH_CACHE_TTL_SECONDS. In particular an employee who has left (left_on set) can keep
using a still-valid token for up to that long on a process that cached them. PATCH on the same
process invalidates its own entry at once. Set the TTL to 0 to read the database every time.
"""
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date

from app.core.config import get_settings


@dataclass(frozen=True, slots=True)
class AuthUser:
    id: int
    role: str
    department: str
    left_on: date | None
    version: int


_entries: OrderedDict[int, tuple[float, AuthUser]] = OrderedDict()


def get(employee_id: int) -> AuthUser | None:
    entry = _entries.get(employee_id)
    if entry is None:
        return None
    if entry[0] < time.monotonic():
        _entries.pop(employee_id, None)
        return None
    return entry[1]


def put(user: AuthUser) -> None:
    s = get_settings()
    if s.auth_cache_ttl_seconds <= 0:
        return
    _entries[user.id] = (time.monotonic() + s.auth_cache_ttl_seconds, user)
    _entries.move_to_end(user.id)
    while len(_entries) > s.auth_cache_max:
        _entries.popitem(last=False)


def invalidate(employee_id: int) -> None:
    _entries.pop(employee_id, None)


def clear() -> None:
    _entries.clear()
