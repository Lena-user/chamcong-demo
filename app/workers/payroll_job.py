import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import engine
from app.modules.payroll import repository
from app.modules.payroll import service as payroll_service

log = logging.getLogger(__name__)


async def calculate_payroll(ctx, period_id: int) -> dict:
    """Arq job: compute payslips for a period in 'calculating' status.

    A Postgres session-level advisory lock (NV-4) makes sure only one payroll job runs at a
    time. The lock lives on a dedicated connection that also runs the job.
    """
    key = get_settings().payroll_lock_key
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        await conn.commit()  # the lock is session-level, so it survives the commit
        try:
            return await _run(conn, period_id)
        finally:
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                await conn.commit()
            except Exception:
                # Never hand a connection that may still hold the lock back to the pool.
                await conn.invalidate()
                raise


async def _run(conn, period_id: int) -> dict:
    async with AsyncSession(conn, expire_on_commit=False) as session:
        period = await repository.get_period(session, period_id)
        if period is None or period.status != "calculating":
            await session.rollback()
            return {"period_id": period_id, "skipped": True}
        try:
            count = await payroll_service.compute_period(session, period_id)
            # Payslips and the status flip commit together: readers never see one without the other.
            await repository.revert_to_draft(session, period_id, calculated=True)
            await session.commit()
        except Exception:
            log.exception("payroll calculation failed for period %s", period_id)
            await session.rollback()
            await repository.revert_to_draft(session, period_id, calculated=False)
            await session.commit()
            raise
    return {"period_id": period_id, "payslips": count}
