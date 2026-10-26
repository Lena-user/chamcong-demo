"""Controlled overload: temporary failures answer 503 + Retry-After, never a bare 500."""
import logging

import redis.exceptions
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.config import get_settings

log = logging.getLogger("app.errors")

# 40001 serialization failure, 40P01 deadlock: retried by the service layer first.
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})
# 57014 query canceled (statement_timeout), 55P03 lock not available (lock_timeout),
# 53300 too many connections, 57P01/57P02/57P03 server shutting down / not ready.
TRANSIENT_SQLSTATES = RETRYABLE_SQLSTATES | {"57014", "55P03", "53300", "57P01", "57P02", "57P03"}


class ServiceUnavailable(Exception):
    def __init__(self, detail: str = "Service temporarily unavailable"):
        super().__init__(detail)
        self.detail = detail


def sqlstate(exc: BaseException) -> str | None:
    """SQLSTATE of a database error (SQLAlchemy wrapper, its adapter error or the asyncpg error)."""
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        for attr in ("sqlstate", "pgcode"):
            code = getattr(cur, attr, None)
            if isinstance(code, str):
                return code
        cur = getattr(cur, "orig", None) or cur.__cause__
    return None


def is_transient_db_error(exc: DBAPIError) -> bool:
    return exc.connection_invalidated or sqlstate(exc) in TRANSIENT_SQLSTATES or _is_connection_error(exc)


def _is_connection_error(exc: BaseException) -> bool:
    # asyncpg connection-level failures carry no SQLSTATE of interest; match by class name.
    names = {c.__name__ for c in type(getattr(exc, "orig", exc)).__mro__}
    return bool(names & {"InterfaceError", "ConnectionDoesNotExistError", "CannotConnectNowError",
                         "TooManyConnectionsError", "PostgresConnectionError"})


def unavailable(request: Request, detail: str, reason: str) -> JSONResponse:
    retry_after = str(get_settings().retry_after_seconds)
    log.warning("503 %s request_id=%s %s %s", reason, getattr(request.state, "request_id", "-"),
                request.method, request.url.path)
    return JSONResponse({"detail": detail}, status_code=503, headers={"Retry-After": retry_after})


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceUnavailable)
    async def _unavailable(request: Request, exc: ServiceUnavailable):
        return unavailable(request, exc.detail, "overloaded")

    @app.exception_handler(PoolTimeoutError)
    async def _pool(request: Request, exc: PoolTimeoutError):
        return unavailable(request, "Database busy, retry shortly", "db pool timeout")

    @app.exception_handler(DBAPIError)
    async def _db(request: Request, exc: DBAPIError):
        if is_transient_db_error(exc):
            return unavailable(request, "Database busy, retry shortly", f"db error {sqlstate(exc)}")
        log.exception("database error request_id=%s", getattr(request.state, "request_id", "-"), exc_info=exc)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.exception_handler(ConnectionError)
    async def _conn(request: Request, exc: ConnectionError):
        return unavailable(request, "Backend unreachable, retry shortly", "connection error")

    @app.exception_handler(redis.exceptions.ConnectionError)
    @app.exception_handler(redis.exceptions.TimeoutError)
    async def _redis(request: Request, exc: Exception):
        return unavailable(request, "Queue unavailable, retry shortly", "redis unavailable")
