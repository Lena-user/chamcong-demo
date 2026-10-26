import asyncio
import functools
import random

from sqlalchemy.exc import DBAPIError

from app.core.errors import RETRYABLE_SQLSTATES, sqlstate

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.05


def retry_on_conflict(fn):
    """Re-run a service function (first argument: the AsyncSession) on serialization failure or deadlock.

    Safe because every write behind it is idempotent. After the last attempt the error propagates
    and the global handler answers 503.
    """

    @functools.wraps(fn)
    async def wrapper(session, *args, **kwargs):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await fn(session, *args, **kwargs)
            except DBAPIError as exc:
                if sqlstate(exc) not in RETRYABLE_SQLSTATES or attempt == MAX_ATTEMPTS:
                    raise
                await session.rollback()
                await asyncio.sleep(BACKOFF_SECONDS * attempt * (1 + random.random()))

    return wrapper
