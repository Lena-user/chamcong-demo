from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.deps import HR_ROLES
from app.core.pagination import PageParams
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.employees import repository
from app.modules.employees.models import Employee, EmployeeSensitive

# Valid argon2 hash of a random string; verified against when the code is unknown so
# response time does not reveal which employee codes exist.
_DUMMY_HASH: str | None = None


async def login(session: AsyncSession, code: str, password: str) -> tuple[str, Employee]:
    global _DUMMY_HASH
    employee = await repository.get_by_code(session, code)
    if employee is None or employee.left_on is not None:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = await hash_password("dummy-password")
        await verify_password(_DUMMY_HASH, password)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code or password")
    if not await verify_password(employee.password_hash, password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code or password")
    return create_access_token(employee.id, employee.role), employee


async def list_employees(
    session: AsyncSession, department: str | None, params: PageParams
) -> tuple[list[Employee], int]:
    return await repository.list_page(session, department, params.offset, params.size)


async def patch_employee(
    session: AsyncSession, actor: Employee, employee_id: int, version: int, changes: dict
) -> Employee:
    is_hr = actor.role in HR_ROLES
    if employee_id != actor.id and not is_hr:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    if "department" in changes and not is_hr:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only HR can change the department")
    updated = await repository.update_versioned(session, employee_id, version, changes)
    if updated is None:
        if not await repository.exists(session, employee_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "Version conflict: reload and retry")
    write_audit(session, actor.id, "employee.update", "employee", employee_id)
    await session.commit()
    return updated


async def read_sensitive(session: AsyncSession, actor: Employee, employee_id: int) -> EmployeeSensitive:
    data = await repository.get_sensitive(session, employee_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    write_audit(session, actor.id, "sensitive.view", "employee", employee_id)
    await session.commit()
    return data


def department_ids(department: str):
    """Subquery of employee ids in a department (for other modules' scoping filters)."""
    return repository.ids_in_department(department)


async def payroll_inputs(session: AsyncSession, period_start: date, period_end: date):
    return await repository.payroll_inputs(session, period_start, period_end)
