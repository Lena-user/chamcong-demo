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


# --- helpers for the business-module tests ---------------------------------------------------

@pytest.fixture
def headers_for():
    """Bearer headers for an employee, minted directly (skips the slow argon2 login)."""
    from app.core.security import create_access_token

    return lambda emp: {"Authorization": f"Bearer {create_access_token(emp.id, emp.role)}"}


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    """Replace the Arq enqueue: a real worker container would run the job against the dev database."""
    from app.modules.payroll import service as payroll_service

    calls: list[int] = []

    async def fake_enqueue(period_id: int) -> str:
        calls.append(period_id)
        return f"test-job-{period_id}-{len(calls)}"

    monkeypatch.setattr(payroll_service, "enqueue_calculation", fake_enqueue)
    return calls


@pytest_asyncio.fixture
async def make_contract():
    from datetime import date

    from app.modules.employees.models import EmployeeSensitive, SalaryContract

    async def _make(emp: Employee, base_salary: int = 10_000_000, allowance: int = 1_000_000, dependents: int = 0):
        async with SessionLocal() as session:
            session.add(SalaryContract(
                employee_id=emp.id, base_salary=base_salary, allowance=allowance, effective_from=date(2024, 1, 1)
            ))
            session.add(EmployeeSensitive(
                employee_id=emp.id, national_id="000000000000", bank_account="000000000000",
                tax_code="0000000000", dependents=dependents,
            ))
            await session.commit()

    return _make


@pytest_asyncio.fixture
async def make_rule():
    from datetime import date

    from app.modules.payroll.models import PayrollRule
    from scripts.seed import PAYROLL_PARAMS

    async def _make(version: int = 1, params: dict | None = None):
        async with SessionLocal() as session:
            session.add(PayrollRule(
                version=version, effective_from=date(2024, 1, 1), params=params or PAYROLL_PARAMS, note="test"
            ))
            await session.commit()

    return _make


@pytest_asyncio.fixture
async def make_period():
    from app.modules.payroll.models import PayrollPeriod

    async def _make(year: int = 2025, month: int = 3, status: str = "draft") -> PayrollPeriod:
        async with SessionLocal() as session:
            period = PayrollPeriod(year=year, month=month, status=status)
            session.add(period)
            await session.commit()
            return period

    return _make


@pytest_asyncio.fixture
async def make_request():
    from datetime import date, datetime, timedelta, timezone

    from app.modules.requests.models import Request

    tz = timezone(timedelta(hours=7))

    async def _make(emp: Employee, work_date: date = date(2025, 3, 10), type_: str = "adjustment") -> Request:
        async with SessionLocal() as session:
            req = Request(
                employee_id=emp.id, type=type_, work_date=work_date, reason="test",
                start_at=datetime(work_date.year, work_date.month, work_date.day, 8, 0, tzinfo=tz),
                end_at=datetime(work_date.year, work_date.month, work_date.day, 16, 0, tzinfo=tz),
            )
            session.add(req)
            await session.commit()
            return req

    return _make
