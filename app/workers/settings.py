from arq.connections import RedisSettings

from app.core.config import get_settings


async def ping(ctx) -> str:
    """Placeholder job: Arq refuses to start with no registered function."""
    return "pong"


class WorkerSettings:
    """Arq worker. Real jobs (payroll) are added by a later task."""

    functions = [ping]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
