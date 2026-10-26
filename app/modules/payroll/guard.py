"""Period guard used by the requests module (kept apart from service.py to avoid a circular import)."""
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payroll import repository

CLOSED_STATUSES = ("calculating", "locked", "paid")


async def assert_period_open(session: AsyncSession, work_date: date) -> None:
    """NV-4. Call inside the transaction that changes data for `work_date`.

    Holds a shared lock on the period row until that transaction ends, so the period cannot
    switch to 'calculating' underneath it (the switch is an UPDATE and waits for the lock).
    """
    period_status = await repository.status_for_share(session, work_date.year, work_date.month)
    if period_status in CLOSED_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Payroll period is {period_status}")
