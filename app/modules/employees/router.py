from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_current_user
from app.modules.employees import service
from app.modules.employees.models import Employee
from app.modules.employees.schemas import EmployeeOut, LoginRequest, LoginResponse

router = APIRouter(prefix="/api", tags=["employees"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    token, employee = await service.login(session, body.code, body.password)
    return LoginResponse(access_token=token, employee=EmployeeOut.model_validate(employee))


@router.get("/me", response_model=EmployeeOut)
async def me(user: Employee = Depends(get_current_user)):
    return user
