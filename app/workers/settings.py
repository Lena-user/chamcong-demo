import logging

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import get_job_engine
from app.modules.payroll import repository
from app.workers.payroll_job import calculate_payroll

log = logging.getLogger(__name__)


async def ping(ctx) -> str:
    """Health-check job."""
    return "pong"


async def recover_stuck_periods() -> int:
    """Periods left in 'calculating' by a worker that died go back to draft so they can be retried."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(get_job_engine(), expire_on_commit=False) as session:
        count = await repository.reset_stuck_calculations(session, get_settings().payroll_job_timeout)
        await session.commit()
    return count


async def on_startup(ctx) -> None:
    count = await recover_stuck_periods()
    if count:
        log.warning("reset %s payroll period(s) stuck in 'calculating' back to draft", count)


class WorkerSettings:
    """Arq worker."""

    functions = [ping, calculate_payroll]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = on_startup
    max_tries = 3
    max_jobs = get_settings().arq_max_jobs
    job_timeout = get_settings().payroll_job_timeout
