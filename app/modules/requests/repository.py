import uuid
from datetime import date

from sqlalchemy import Row, Select, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.requests.models import Request


async def insert_request(session: AsyncSession, employee_id: int, data: dict) -> Request:
    stmt = insert(Request).values(employee_id=employee_id, **data).returning(Request)
    return (await session.execute(stmt)).scalar_one()


async def insert_request_idempotent(
    session: AsyncSession, employee_id: int, key: uuid.UUID, data: dict
) -> Request | None:
    """INSERT ... ON CONFLICT DO NOTHING on the key. None means the key already exists."""
    stmt = (
        pg_insert(Request).values(employee_id=employee_id, idempotency_key=key, **data)
        .on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(Request)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_key(session: AsyncSession, key: uuid.UUID) -> Request | None:
    stmt = select(Request).where(Request.idempotency_key == key).execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


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
        Request.employee_id, Request.work_date, Request.status, Request.approved_by,
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
