from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.employees.models import Employee


async def get_by_id(session: AsyncSession, employee_id: int) -> Employee | None:
    return await session.get(Employee, employee_id)


async def get_by_code(session: AsyncSession, code: str) -> Employee | None:
    result = await session.execute(select(Employee).where(Employee.code == code))
    return result.scalar_one_or_none()
