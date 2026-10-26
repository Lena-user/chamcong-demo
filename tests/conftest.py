import os

# Point the app at the test database before any app module is imported.
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.modules.employees.models import Employee  # noqa: E402

TEST_PASSWORD = "test-password-123"
_pw_hash: str | None = None


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def client(_schema):
    # ASGITransport does not run lifespan, so run it manually (creates the Redis pool).
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            yield c


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(_schema):
    yield
    tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def make_employee():
    """Factory: create an employee with a role and return (Employee, password)."""
    global _pw_hash
    if _pw_hash is None:
        _pw_hash = await hash_password(TEST_PASSWORD)
    counter = {"n": 0}

    async def _make(role: str = "employee", department: str = "Kinh doanh", code: str | None = None) -> Employee:
        from datetime import date

        counter["n"] += 1
        async with SessionLocal() as session:
            emp = Employee(
                code=code or f"T{os.getpid() % 100:02d}{counter['n']:05d}",
                full_name=f"Test {role} {counter['n']}",
                department=department,
                hired_on=date(2024, 1, 1),
                role=role,
                password_hash=_pw_hash,
            )
            session.add(emp)
            await session.commit()
            return emp

    return _make


@pytest_asyncio.fixture
async def login_token(client):
    async def _login(employee: Employee, password: str = TEST_PASSWORD) -> str:
        r = await client.post("/api/auth/login", json={"code": employee.code, "password": password})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    return _login


@pytest.fixture
def auth_headers():
    return lambda token: {"Authorization": f"Bearer {token}"}
