import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import limit_writes
from app.core.deps import MANAGER_ROLES, get_current_user, require_roles
from app.core.pagination import Page, PageParams, page_params
from app.modules.employees.models import Employee
from app.modules.requests import service
from app.modules.requests.schemas import RequestCreate, RequestOut

router = APIRouter(prefix="/api/requests", tags=["requests"])


def _page(result, params: PageParams) -> Page[RequestOut]:
    items, total = result
    return Page(items=items, total=total, page=params.page, size=params.size)


@router.post("", response_model=RequestOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(limit_writes)])
async def create_request(
    body: RequestCreate,
    response: Response,
    idempotency_key: uuid.UUID | None = Header(None, alias="Idempotency-Key"),
    user: Employee = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    request, created = await service.create_request(session, user, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return request


@router.get("/me", response_model=Page[RequestOut])
async def my_requests(
    params: PageParams = Depends(page_params),
    user: Employee = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return _page(await service.my_requests(session, user, params), params)


@router.get("", response_model=Page[RequestOut])
async def list_requests(
    status_filter: Literal["pending", "approved", "rejected"] | None = Query(None, alias="status"),
    params: PageParams = Depends(page_params),
    user: Employee = Depends(require_roles(*MANAGER_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    return _page(await service.list_requests(session, user, status_filter, params), params)


def _decided(result: tuple) -> RequestOut:
    request, replayed = result
    return RequestOut.model_validate(request).model_copy(update={"replayed": replayed})


@router.post("/{request_id}/approve", response_model=RequestOut, dependencies=[Depends(limit_writes)])
async def approve(
    request_id: int, user: Employee = Depends(require_roles(*MANAGER_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    return _decided(await service.decide_request(session, user, request_id, "approved"))


@router.post("/{request_id}/reject", response_model=RequestOut, dependencies=[Depends(limit_writes)])
async def reject(
    request_id: int, user: Employee = Depends(require_roles(*MANAGER_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    return _decided(await service.decide_request(session, user, request_id, "rejected"))
