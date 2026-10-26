from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def make_engine(url: str, pool_size: int | None = None, max_overflow: int | None = None,
                server_settings: dict[str, str] | None = None) -> AsyncEngine:
    s = get_settings()
    if server_settings is None:
        # Fail fast instead of piling requests up behind a slow query, a lock or a stuck transaction.
        server_settings = {
            "statement_timeout": str(s.db_statement_timeout_ms),
            "lock_timeout": str(s.db_lock_timeout_ms),
            "idle_in_transaction_session_timeout": str(s.db_idle_in_tx_timeout_ms),
        }
    return create_async_engine(
        url,
        pool_size=s.db_pool_size if pool_size is None else pool_size,
        max_overflow=s.db_max_overflow if max_overflow is None else max_overflow,
        pool_timeout=s.db_pool_timeout,
        pool_pre_ping=True,
        connect_args={"server_settings": server_settings},
    )


engine = make_engine(get_settings().database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

_job_engine: AsyncEngine | None = None


def get_job_engine() -> AsyncEngine:
    """Engine for the Arq worker: small pool, and a statement timeout that fits a long job.

    The payroll job waits on an advisory lock and runs bulk statements, so the 5 s request-level
    timeouts of `engine` would kill it.
    """
    global _job_engine
    if _job_engine is None:
        s = get_settings()
        ms = str(s.payroll_job_timeout * 1000)
        _job_engine = make_engine(
            s.database_url, pool_size=s.worker_db_pool_size, max_overflow=0,
            server_settings={"statement_timeout": ms, "lock_timeout": "0", "idle_in_transaction_session_timeout": ms},
        )
    return _job_engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Services own commit/rollback; this only closes it."""
    async with SessionLocal() as session:
        yield session
