from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import limit_login, limit_writes

from app.core.db import get_session
from app.core.deps import HR_ROLES, MANAGER_ROLES, get_current_employee, get_current_user, require_roles
from app.core.pagination import Page, PageParams, page_params
from app.modules.employees import service
from app.modules.employees.models import Employee
from app.modules.employees.schemas import (
    EmployeeOut, EmployeePatch, LoginRequest, LoginResponse, SensitiveOut,
)

router = APIRouter(prefix="/api", tags=["employees"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    await limit_login(body.code)
    token, employee = await service.login(session, body.code, body.password)
    return LoginResponse(access_token=token, employee=EmployeeOut.model_validate(employee))


@router.get("/me", response_model=EmployeeOut)
async def me(user: Employee = Depends(get_current_employee)):
    return user


@router.get("/employees", response_model=Page[EmployeeOut])
async def list_employees(
    department: str | None = None,
    params: PageParams = Depends(page_params),
    _: Employee = Depends(require_roles(*MANAGER_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    items, total = await service.list_employees(session, department, params)
    return Page(items=items, total=total, page=params.page, size=params.size)


@router.patch("/employees/{employee_id}", response_model=EmployeeOut, dependencies=[Depends(limit_writes)])
async def patch_employee(
    employee_id: int,
    body: EmployeePatch,
    user: Employee = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    changes = body.model_dump(exclude={"version"}, exclude_none=True)
    return await service.patch_employee(session, user, employee_id, body.version, changes)


@router.get("/employees/{employee_id}/sensitive", response_model=SensitiveOut)
async def get_sensitive(
    employee_id: int,
    user: Employee = Depends(require_roles(*HR_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    return await service.read_sensitive(session, user, employee_id)
