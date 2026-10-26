from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import limit_writes
from app.core.deps import HR_ROLES, get_current_user, require_roles
from app.modules.employees.models import Employee
from app.modules.payroll import service
from app.modules.payroll.schemas import CalculateOut, JobOut, PayslipOut, PeriodCreate, PeriodOut

router = APIRouter(prefix="/api/payroll", tags=["payroll"])
_hr = require_roles(*HR_ROLES)


@router.post("/periods", response_model=PeriodOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(limit_writes)])
async def create_period(
    body: PeriodCreate, _: Employee = Depends(_hr), session: AsyncSession = Depends(get_session)
):
    return await service.create_period(session, body.year, body.month)


@router.post("/periods/{period_id}/calculate", response_model=CalculateOut, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(limit_writes)])
async def calculate(period_id: int, _: Employee = Depends(_hr), session: AsyncSession = Depends(get_session)):
    return CalculateOut(job_id=await service.start_calculation(session, period_id))


@router.get("/jobs/{job_id}", response_model=JobOut)
async def job_status(job_id: str, _: Employee = Depends(_hr)):
    return await service.job_status(job_id)


@router.post("/periods/{period_id}/lock", response_model=PeriodOut, dependencies=[Depends(limit_writes)])
async def lock(period_id: int, _: Employee = Depends(_hr), session: AsyncSession = Depends(get_session)):
    return await service.lock_period(session, period_id)


@router.get("/periods/{period_id}/payslips/me", response_model=PayslipOut)
async def my_payslip(
    period_id: int, user: Employee = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    return await service.my_payslip(session, user, period_id)
