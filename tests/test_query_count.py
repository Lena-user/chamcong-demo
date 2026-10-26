"""Every endpoint should stay within a handful of SQL statements (no N+1)."""
import uuid
from datetime import date

import pytest
from sqlalchemy import event

from app.core.db import engine

OT = {"type": "ot", "work_date": "2025-03-10", "start_at": "2025-03-10T18:00:00+07:00",
      "end_at": "2025-03-10T20:00:00+07:00", "reason": "x"}


@pytest.fixture
def statements():
    seen: list[str] = []

    def on_execute(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", on_execute)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", on_execute)


async def test_query_budget(
    client, statements, make_employee, make_contract, make_rule, make_period, make_request, headers_for
):
    """Counts include the auth lookup done by get_current_user (1 statement)."""
    await make_rule()
    hr, mgr, emp = await make_employee("hr"), await make_employee("manager"), await make_employee()
    for _ in range(5):  # more rows must not mean more queries
        await make_employee()
    await make_contract(emp)
    period = await make_period(2025, 3)
    req = await make_request(emp)
    reqs = [await make_request(emp) for _ in range(4)]

    async def used(method, path, headers, **kw):
        statements.clear()
        r = await client.request(method, path, headers=headers_for(headers), **kw)
        return r, len(statements)

    key = str(uuid.uuid4())
    cases = [
        ("GET", "/api/me", emp, 3, {}),
        ("GET", "/api/employees", mgr, 3, {}),
        ("PATCH", f"/api/employees/{emp.id}", emp, 3, {"json": {"version": 1, "full_name": "n"}}),
        ("GET", f"/api/employees/{emp.id}/sensitive", hr, 3, {}),
        ("POST", "/api/attendance/check", emp, 3, {"json": {"idempotency_key": key}}),
        ("POST", "/api/attendance/check", emp, 3, {"json": {"idempotency_key": key}}),
        ("GET", "/api/attendance/me", emp, 3, {}),
        ("POST", "/api/requests", emp, 3, {"json": OT}),
        ("GET", "/api/requests/me", emp, 3, {}),
        ("GET", "/api/requests?status=pending", mgr, 3, {}),
        ("POST", f"/api/requests/{reqs[0].id}/reject", mgr, 4, {}),  # auth + read + period lock + update
        ("POST", f"/api/requests/{req.id}/approve", mgr, 4, {}),
        ("POST", "/api/payroll/periods", hr, 3, {"json": {"year": 2026, "month": 1}}),
        ("POST", f"/api/payroll/periods/{period.id}/calculate", hr, 3, {}),
    ]
    for method, path, who, limit, kw in cases:
        r, n = await used(method, path, who, **kw)
        assert r.status_code < 300, (path, r.status_code, r.text)
        assert n <= limit, f"{method} {path} ran {n} statements"


async def test_auth_lookup_cached_on_second_request(client, statements, make_employee, headers_for):
    """The identity lookup of get_current_user is served from the in-process cache within the TTL."""
    emp = await make_employee()
    h = headers_for(emp)
    statements.clear()
    await client.get("/api/attendance/me", headers=h)
    cold = len(statements)
    statements.clear()
    await client.get("/api/attendance/me", headers=h)
    assert len(statements) == cold - 1 <= 2
