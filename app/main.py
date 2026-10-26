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
from app.modules.employees.router import router as employees_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_pool.init_redis()
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


app = FastAPI(title="Chamcong demo", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().allowed_host_list)
app.include_router(employees_router)


@app.get("/health")
async def health():
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
