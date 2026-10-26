import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import Scope

from app.core import redis as redis_pool
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.core.errors import install_handlers
from app.core.middleware import RequestContextMiddleware, setup_logging
from app.modules.attendance.router import router as attendance_router
from app.modules.employees.router import router as employees_router
from app.modules.payroll.router import router as payroll_router
from app.modules.monitor.router import router as monitor_router
from app.modules.requests.router import router as requests_router


log = logging.getLogger("app.main")


async def check_connection_budget() -> None:
    """Warn (never block) when the configured pools could exceed Postgres max_connections."""
    s = get_settings()
    try:
        async with SessionLocal() as session:
            max_connections = int(await session.scalar(text("SHOW max_connections")))
            await session.rollback()
    except Exception:
        log.warning("could not read max_connections; connection budget not checked")
        return
    api = s.uvicorn_workers * (s.db_pool_size + s.db_max_overflow)
    worker = s.worker_db_pool_size * s.arq_max_jobs
    total = api + worker + s.db_reserved_connections
    if total > max_connections:
        log.warning(
            "connection budget %s exceeds max_connections %s: api %s workers x (pool %s + overflow %s) = %s, "
            "arq %s pool x %s jobs = %s, reserved %s",
            total, max_connections, s.uvicorn_workers, s.db_pool_size, s.db_max_overflow, api,
            s.worker_db_pool_size, s.arq_max_jobs, worker, s.db_reserved_connections,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_pool.init_redis()
    await check_connection_budget()
    yield
    await redis_pool.close_redis()
    await engine.dispose()


class CachedStaticFiles(StaticFiles):
    """Static files with a 1-day cache, except index.html which is always revalidated."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            is_index = path in ("", ".", "index.html") or response.headers.get(
                "content-type", ""
            ).startswith("text/html")
            response.headers["Cache-Control"] = "no-cache" if is_index else "public, max-age=86400"
        return response


setup_logging()
app = FastAPI(title="Chamcong demo", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().allowed_host_list)
app.add_middleware(RequestContextMiddleware)  # added last = outermost: also wraps rejected hosts
install_handlers(app)
app.include_router(employees_router)
app.include_router(attendance_router)
app.include_router(requests_router)
app.include_router(payroll_router)
app.include_router(monitor_router)


@app.get("/health/live")
async def health_live():
    """Process is up. Deliberately touches neither the database nor Redis."""
    return {"status": "ok"}


@app.get("/health/ready")
@app.get("/health")  # kept for compatibility (compose healthcheck): same as ready
async def health_ready():
    db_ok = redis_ok = True
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    try:
        await redis_pool.get_redis().ping()
    except Exception:
        redis_ok = False
    body = {
        "status": "ok" if db_ok and redis_ok else "error",
        "db": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }
    return JSONResponse(body, status_code=200 if db_ok and redis_ok else 503)


# Mounted last so it never shadows /api or /health.
app.mount("/", CachedStaticFiles(directory="app/static", html=True), name="static")
