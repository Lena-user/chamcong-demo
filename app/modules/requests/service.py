import uuid
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_cache import AuthUser
from app.core.pagination import PageParams
from app.core.retry import retry_on_conflict
from app.modules.employees import service as employees_service
from app.modules.payroll.calculator import ApprovedMinutes
from app.modules.payroll.guard import assert_period_open
from app.modules.requests import repository
from app.modules.requests.models import Request
from app.modules.requests.schemas import RequestCreate


@retry_on_conflict
async def create_request(
    session: AsyncSession, user: AuthUser, body: RequestCreate, key: uuid.UUID | None = None
) -> tuple[Request, bool]:
    """Returns (request, created). With an Idempotency-Key a repeat returns the earlier request."""
    if key is None:
        await assert_period_open(session, body.work_date)  # NV-4
        request = await repository.insert_request(session, user.id, body.model_dump())
        await session.commit()
        return request, True
    existing = await repository.get_by_key(session, key)
    if existing is None:
        await assert_period_open(session, body.work_date)  # NV-4
        request = await repository.insert_request_idempotent(session, user.id, key, body.model_dump())
        if request is not None:
            await session.commit()
            return request, True
        existing = await repository.get_by_key(session, key)  # lost the race to a concurrent resend
    if existing is None or existing.employee_id != user.id:
        # The key belongs to someone else: never leak or reuse their request.
        raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key already used")
    if _differs(existing, body):
        # Same key with a different payload is a client bug, not a retry (Stripe rejects it too).
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Idempotency key reused with different parameters"
        )
    return existing, False


def _differs(existing: Request, body: RequestCreate) -> bool:
    return any(getattr(existing, field) != value for field, value in body.model_dump().items())


async def my_requests(session: AsyncSession, user: AuthUser, params: PageParams):
    return await repository.list_mine(session, user.id, params.offset, params.size)


async def list_requests(session: AsyncSession, user: AuthUser, status_filter: str | None, params: PageParams):
    """Managers only see requests from their own department; hr/admin see everything."""
    scope = employees_service.department_ids(user.department) if user.role == "manager" else None
    return await repository.list_all(session, status_filter, scope, params.offset, params.size)


@retry_on_conflict
async def decide_request(
    session: AsyncSession, actor: AuthUser, request_id: int, new_status: str
) -> tuple[Request, bool]:
    """Approve or reject (NV-3, NV-4). Three queries: read, period lock, conditional update."""
    info = await repository.get_for_decision(
        session, request_id, employees_service.department_ids(actor.department)
    )
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if info.employee_id == actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot decide your own request")
    if actor.role == "manager" and not info.in_department:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Request belongs to another department")
    if info.status != "pending":
        return await _replay_or_conflict(session, request_id, new_status, actor)
    await assert_period_open(session, info.work_date)
    request = await repository.decide(session, request_id, new_status, actor.id)
    if request is None:
        return await _replay_or_conflict(session, request_id, new_status, actor)
    await session.commit()
    return request, False


async def _replay_or_conflict(
    session: AsyncSession, request_id: int, new_status: str, actor: AuthUser
) -> tuple[Request, bool]:
    """The request is already decided. Same person, same outcome = a resend after a timeout: answer 200."""
    request = await session.get(Request, request_id, populate_existing=True)
    if request is not None and request.status == new_status and request.approved_by == actor.id:
        return request, True
    raise HTTPException(status.HTTP_409_CONFLICT, "Request already decided")


async def approved_minutes(session: AsyncSession, start: date, end: date) -> dict[tuple[int, date], ApprovedMinutes]:
    """Approved requests folded into minutes per (employee_id, work_date), for payroll."""
    out: dict[tuple[int, date], ApprovedMinutes] = {}
    for r in await repository.approved_in_range(session, start, end):
        minutes = int((r.end_at - r.start_at).total_seconds() // 60) if r.start_at and r.end_at else 0
        cur = out.get((r.employee_id, r.work_date), ApprovedMinutes())
        out[(r.employee_id, r.work_date)] = ApprovedMinutes(
            adjustment=max(cur.adjustment, minutes) if r.type == "adjustment" else cur.adjustment,
            ot=cur.ot + minutes if r.type == "ot" else cur.ot,
            leave=cur.leave or r.type == "leave",
        )
    return out


async def count_recent(session: AsyncSession, seconds: int) -> int:
    return await repository.count_created_since(session, seconds)
