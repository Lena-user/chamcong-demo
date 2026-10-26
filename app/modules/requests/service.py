from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.modules.employees import service as employees_service
from app.modules.employees.models import Employee
from app.modules.payroll.calculator import ApprovedMinutes
from app.modules.payroll.guard import assert_period_open
from app.modules.requests import repository
from app.modules.requests.models import Request
from app.modules.requests.schemas import RequestCreate


async def create_request(session: AsyncSession, user: Employee, body: RequestCreate) -> Request:
    await assert_period_open(session, body.work_date)  # NV-4
    request = await repository.insert_request(session, user.id, body.model_dump())
    await session.commit()
    return request


async def my_requests(session: AsyncSession, user: Employee, params: PageParams):
    return await repository.list_mine(session, user.id, params.offset, params.size)


async def list_requests(session: AsyncSession, user: Employee, status_filter: str | None, params: PageParams):
    """Managers only see requests from their own department; hr/admin see everything."""
    scope = employees_service.department_ids(user.department) if user.role == "manager" else None
    return await repository.list_all(session, status_filter, scope, params.offset, params.size)


async def decide_request(session: AsyncSession, actor: Employee, request_id: int, new_status: str) -> Request:
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
        raise HTTPException(status.HTTP_409_CONFLICT, "Request already decided")
    await assert_period_open(session, info.work_date)
    request = await repository.decide(session, request_id, new_status, actor.id)
    if request is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Request already decided")
    await session.commit()
    return request


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
