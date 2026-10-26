from datetime import date

from sqlalchemy import Row, Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.employees.models import Employee, EmployeeSensitive, SalaryContract


async def get_by_id(session: AsyncSession, employee_id: int) -> Employee | None:
    return await session.get(Employee, employee_id)


async def get_by_code(session: AsyncSession, code: str) -> Employee | None:
    result = await session.execute(select(Employee).where(Employee.code == code))
    return result.scalar_one_or_none()


async def list_page(
    session: AsyncSession, department: str | None, offset: int, limit: int
) -> tuple[list[Employee], int]:
    where = [Employee.department == department] if department else []
    total = await session.scalar(select(func.count()).select_from(Employee).where(*where))
    result = await session.execute(
        select(Employee).where(*where).order_by(Employee.id).offset(offset).limit(limit)
    )
    return list(result.scalars()), total or 0


async def update_versioned(
    session: AsyncSession, employee_id: int, version: int, values: dict
) -> Employee | None:
    """Optimistic lock (NV-2): matches only when the version is still the one the client read."""
    stmt = (
        update(Employee)
        .where(Employee.id == employee_id, Employee.version == version)
        .values(**values, version=Employee.version + 1)
        .returning(Employee)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def exists(session: AsyncSession, employee_id: int) -> bool:
    return await session.scalar(select(Employee.id).where(Employee.id == employee_id)) is not None


async def get_sensitive(session: AsyncSession, employee_id: int) -> EmployeeSensitive | None:
    return await session.get(EmployeeSensitive, employee_id)


def ids_in_department(department: str) -> Select:
    """Subquery of employee ids, for other modules to filter by department without a join."""
    return select(Employee.id).where(Employee.department == department)


async def payroll_inputs(session: AsyncSession, period_start: date, period_end: date) -> list[Row]:
    """Active employees in the period with their contract (latest valid one) and dependents."""
    stmt = (
        select(
            Employee.id.label("employee_id"),
            SalaryContract.base_salary,
            SalaryContract.allowance,
            func.coalesce(EmployeeSensitive.dependents, 0).label("dependents"),
        )
        .join(SalaryContract, SalaryContract.employee_id == Employee.id)
        .outerjoin(EmployeeSensitive, EmployeeSensitive.employee_id == Employee.id)
        .where(
            Employee.hired_on <= period_end,
            or_(Employee.left_on.is_(None), Employee.left_on >= period_start),
            SalaryContract.effective_from <= period_end,
            or_(SalaryContract.effective_to.is_(None), SalaryContract.effective_to >= period_start),
        )
        .ext(distinct_on(Employee.id))
        .order_by(Employee.id, SalaryContract.effective_from.desc())
    )
    return list((await session.execute(stmt)).all())


async def names_by_ids(session: AsyncSession, ids: list[int]) -> dict[int, tuple[str, str]]:
    if not ids:
        return {}
    rows = await session.execute(select(Employee.id, Employee.code, Employee.full_name).where(Employee.id.in_(ids)))
    return {r.id: (r.code, r.full_name) for r in rows}
