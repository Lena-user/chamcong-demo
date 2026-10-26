from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.modules.employees import repository
from app.modules.employees.models import Employee

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
