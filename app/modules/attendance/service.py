import uuid
from datetime import date, datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.pagination import PageParams
from app.core.retry import retry_on_conflict
from app.modules.attendance import repository
from app.modules.attendance.models import AttendanceDaily
from app.modules.attendance.schemas import CheckResponse


@retry_on_conflict
async def check(
    session: AsyncSession, employee_id: int, key: uuid.UUID, source: str,
    now: datetime | None = None,
) -> tuple[CheckResponse, bool]:
    """NV-1. Returns (response, created). One INSERT, then (first time only) one daily upsert."""
    row = await repository.insert_event(session, employee_id, now, source, key)
    if row is None:
        existing = await repository.get_event_by_key(session, key)
        if existing is None or existing.employee_id != employee_id:
            # The key belongs to someone else: never leak or reuse their event.
            raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key already used")
        return CheckResponse(event_id=existing.id, event_time=existing.event_time, duplicate=True), False
    await repository.upsert_daily_for_event(session, employee_id, row.event_time, get_settings())
    await session.commit()
    return CheckResponse(event_id=row.id, event_time=row.event_time, duplicate=False), True


async def rebuild_daily(session: AsyncSession, employee_id: int, work_date: date) -> None:
    """Recompute one attendance_daily row from the raw events (commits)."""
    await repository.rebuild_daily(session, employee_id, work_date, get_settings())
    await session.commit()


async def my_daily(
    session: AsyncSession, employee_id: int, date_from: date | None, date_to: date | None,
    params: PageParams,
) -> tuple[list[AttendanceDaily], int]:
    return await repository.list_daily(session, employee_id, date_from, date_to, params.offset, params.size)


async def daily_totals(session: AsyncSession, start: date, end: date) -> dict[tuple[int, date], tuple[int, int]]:
    """(employee_id, work_date) -> (worked_minutes, ot_minutes), for payroll."""
    rows = await repository.daily_in_range(session, start, end)
    return {(r.employee_id, r.work_date): (r.worked_minutes, r.ot_minutes) for r in rows}
