from datetime import date

from sqlalchemy import Row, Select, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.requests.models import Request


async def insert_request(session: AsyncSession, employee_id: int, data: dict) -> Request:
    stmt = insert(Request).values(employee_id=employee_id, **data).returning(Request)
    return (await session.execute(stmt)).scalar_one()


async def _page(session: AsyncSession, where: list, offset: int, limit: int) -> tuple[list[Request], int]:
    total = await session.scalar(select(func.count()).select_from(Request).where(*where))
    result = await session.execute(
        select(Request).where(*where).order_by(Request.created_at.desc(), Request.id.desc())
        .offset(offset).limit(limit)
    )
    return list(result.scalars()), total or 0


async def list_mine(session: AsyncSession, employee_id: int, offset: int, limit: int):
    return await _page(session, [Request.employee_id == employee_id], offset, limit)


async def list_all(
    session: AsyncSession, status: str | None, department_ids: Select | None, offset: int, limit: int
):
    where = []
    if status:
        where.append(Request.status == status)
    if department_ids is not None:
        where.append(Request.employee_id.in_(department_ids))
    return await _page(session, where, offset, limit)


async def get_for_decision(session: AsyncSession, request_id: int, department_ids: Select) -> Row | None:
    stmt = select(
        Request.employee_id, Request.work_date, Request.status,
        Request.employee_id.in_(department_ids).label("in_department"),
    ).where(Request.id == request_id)
    return (await session.execute(stmt)).first()


async def decide(session: AsyncSession, request_id: int, new_status: str, actor_id: int) -> Request | None:
    """NV-3: conditional update, only one caller can move a request out of 'pending'."""
    stmt = (
        update(Request)
        .where(Request.id == request_id, Request.status == "pending")
        .values(status=new_status, approved_by=actor_id, decided_at=func.now())
        .returning(Request)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def approved_in_range(session: AsyncSession, start: date, end: date) -> list[Row]:
    stmt = select(
        Request.employee_id, Request.work_date, Request.type, Request.start_at, Request.end_at
    ).where(Request.status == "approved", Request.work_date.between(start, end))
    return list((await session.execute(stmt)).all())
