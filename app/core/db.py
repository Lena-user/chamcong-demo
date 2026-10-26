from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=10,
        max_overflow=5,
        pool_timeout=10,
        pool_pre_ping=True,
    )


engine = make_engine(get_settings().database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Services own commit/rollback; this only closes it."""
    async with SessionLocal() as session:
        yield session
