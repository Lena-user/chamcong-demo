import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import auth_cache
from app.core.auth_cache import AuthUser
from app.core.db import get_session
from app.core.security import decode_access_token
from app.modules.employees import repository
from app.modules.employees.models import Employee

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> AuthUser:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if creds is None:
        raise unauthorized
    try:
        payload = decode_access_token(creds.credentials)
        employee_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = auth_cache.get(employee_id)
    if user is None:
        employee = await repository.get_by_id(session, employee_id)
        if employee is None:
            raise unauthorized
        user = AuthUser(employee.id, employee.role, employee.department, employee.left_on, employee.version)
        auth_cache.put(user)
    if user.left_on is not None:
        raise unauthorized
    request.state.user_id = user.id
    return user


async def get_current_employee(
    user: AuthUser = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> Employee:
    """Full employee row, for endpoints that return profile fields (not served from the cache)."""
    employee = await repository.get_by_id(session, user.id)
    if employee is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return employee


def require_roles(*roles: str):
    async def checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return checker


MANAGER_ROLES = ("manager", "hr", "admin")
HR_ROLES = ("hr", "admin")
