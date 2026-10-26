from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers.payroll_job import calculate_payroll


async def ping(ctx) -> str:
    """Health-check job."""
    return "pong"


class WorkerSettings:
    """Arq worker."""

    functions = [ping, calculate_payroll]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = get_settings().payroll_job_timeout
