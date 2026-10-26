from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import limit_writes
from app.core.deps import get_current_user
from app.core.pagination import Page, PageParams, page_params
from app.modules.attendance import service
from app.modules.attendance.schemas import CheckRequest, CheckResponse, DailyOut
from app.modules.employees.models import Employee

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


@router.post("/check", response_model=CheckResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(limit_writes)])
async def check(
    body: CheckRequest,
    response: Response,
    user: Employee = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result, created = await service.check(session, user.id, body.idempotency_key, body.source)
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("/me", response_model=Page[DailyOut])
async def my_attendance(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    params: PageParams = Depends(page_params),
    user: Employee = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    items, total = await service.my_daily(session, user.id, date_from, date_to, params)
    return Page(items=items, total=total, page=params.page, size=params.size)
