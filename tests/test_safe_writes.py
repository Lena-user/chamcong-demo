"""TASK-005: safe writes, controlled overload, idempotency, hardening."""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core import auth_cache
from app.core import security
from app.core.config import get_settings
from app.core.db import SessionLocal, engine, get_session
from app.main import app, check_connection_budget
from app.modules.payroll.models import PayrollPeriod
from app.modules.requests.models import Request
from app.workers.settings import recover_stuck_periods
from tests.conftest import TEST_PASSWORD

OT_BODY = {
    "type": "ot", "work_date": "2025-03-10", "reason": "x",
    "start_at": "2025-03-10T18:00:00+07:00", "end_at": "2025-03-10T20:00:00+07:00",
}


@pytest.fixture
def settings(monkeypatch):
    """Patch attributes of the cached Settings object for one test."""
    s = get_settings()

    def _set(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)

    return _set


# --- login under load ----------------------------------------------------------------------

async def test_login_releases_connection_before_hashing(client, make_employee, monkeypatch):
    emp = await make_employee()
    seen = []

    def slow_verify(password_hash, password):
        seen.append(engine.pool.checkedout())
        return True

    monkeypatch.setattr(security, "_verify", slow_verify)
    r = await client.post("/api/auth/login", json={"code": emp.code, "password": "whatever"})
    assert r.status_code == 200 and seen == [0]


async def test_login_burst_beyond_pool_all_200(client, make_employee, monkeypatch):
    emps = [await make_employee() for _ in range(5)]
    running = {"now": 0, "max": 0}

    def slow_verify(password_hash, password):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        time.sleep(0.5)
        running["now"] -= 1
        return True

    monkeypatch.setattr(security, "_verify", slow_verify)
    n = engine.pool.size() + engine.pool._max_overflow + 10
    res = await asyncio.gather(*[
        client.post("/api/auth/login", json={"code": emps[i % 5].code, "password": "x"}) for i in range(n)
    ])
    assert [r.status_code for r in res] == [200] * n
    assert running["max"] <= get_settings().hash_concurrency


async def test_login_queue_full_returns_503(client, make_employee, monkeypatch, settings):
    emp = await make_employee()
    settings(login_max_waiting=3)
    monkeypatch.setattr(security, "_verify", lambda h, p: (time.sleep(0.3), True)[1])
    res = await asyncio.gather(*[
        client.post("/api/auth/login", json={"code": emp.code, "password": "x"}) for _ in range(8)
    ])
    codes = sorted(r.status_code for r in res)
    assert codes == [200] * 3 + [503] * 5
    assert all(r.headers["retry-after"] for r in res if r.status_code == 503)


async def test_old_argon2_hash_still_verifies(settings):
    h = await security.hash_password("pw")
    settings(argon2_time_cost=1, argon2_memory_kib=8, argon2_parallelism=1)
    security._hasher.cache_clear()
    try:
        assert await security.verify_password(h, "pw")
        assert not await security.verify_password(h, "other")
    finally:
        security._hasher.cache_clear()


# --- 503 mapping -----------------------------------------------------------------------------

async def test_pool_timeout_is_503_with_retry_after(client):
    async def broken_session():
        raise PoolTimeoutError("QueuePool limit reached")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = broken_session
    try:
        r = await client.post("/api/auth/login", json={"code": "A", "password": "b"})
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert r.status_code == 503
    assert r.headers["retry-after"] == str(get_settings().retry_after_seconds)
    assert "detail" in r.json()


async def test_lock_timeout_is_503(client, make_employee, make_period, headers_for):
    emp = await make_employee()
    period = await make_period(2025, 3)
    async with engine.connect() as holder:
        await holder.execute(text("SELECT 1 FROM payroll_periods WHERE id = :i FOR UPDATE"), {"i": period.id})
        r = await client.post("/api/requests", json=OT_BODY, headers=headers_for(emp))
        await holder.rollback()
    assert r.status_code == 503 and "retry-after" in r.headers


def _db_error(code: str) -> DBAPIError:
    orig = Exception("boom")
    orig.sqlstate = code
    return DBAPIError("stmt", {}, orig)


@pytest.mark.parametrize("fail_times,expected", [(2, 201), (3, 503)])
async def test_serialization_failure_retried_then_503(
    client, make_employee, headers_for, monkeypatch, fail_times, expected
):
    from app.modules.requests import repository

    emp = await make_employee()
    real, calls = repository.insert_request, {"n": 0}

    async def flaky(session, employee_id, data):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise _db_error("40001")
        return await real(session, employee_id, data)

    monkeypatch.setattr(repository, "insert_request", flaky)
    r = await client.post("/api/requests", json=OT_BODY, headers=headers_for(emp))
    assert r.status_code == expected
    assert calls["n"] == min(fail_times + 1, 3)
    if expected == 503:
        assert "retry-after" in r.headers


async def test_unknown_db_error_is_500_not_503(client, make_employee, headers_for, monkeypatch):
    from app.modules.requests import repository

    async def broken(*a, **k):
        raise _db_error("23505")

    monkeypatch.setattr(repository, "insert_request", broken)
    emp = await make_employee()
    r = await client.post("/api/requests", json=OT_BODY, headers=headers_for(emp))
    assert r.status_code == 500


# --- idempotency -----------------------------------------------------------------------------

async def test_create_request_same_key_one_row(client, make_employee, headers_for):
    emp, other = await make_employee(), await make_employee()
    key = str(uuid.uuid4())
    h = {**headers_for(emp), "Idempotency-Key": key}
    res = await asyncio.gather(*[client.post("/api/requests", json=OT_BODY, headers=h) for _ in range(20)])
    assert sorted(r.status_code for r in res).count(201) == 1
    assert set(r.status_code for r in res) <= {200, 201}
    assert len({r.json()["id"] for r in res}) == 1
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Request)) == 1
    r = await client.post("/api/requests", json=OT_BODY, headers={**headers_for(other), "Idempotency-Key": key})
    assert r.status_code == 409
    # no header still works and creates a new one
    assert (await client.post("/api/requests", json=OT_BODY, headers=headers_for(emp))).status_code == 201
    bad = await client.post("/api/requests", json=OT_BODY, headers={**headers_for(emp), "Idempotency-Key": "nope"})
    assert bad.status_code == 422


async def test_approve_replay_same_manager_200_other_409(client, make_employee, make_request, headers_for):
    owner, mgr, mgr2 = await make_employee(), await make_employee("manager"), await make_employee("manager")
    req = await make_request(owner)
    first = await client.post(f"/api/requests/{req.id}/approve", headers=headers_for(mgr))
    assert first.status_code == 200 and first.json()["replayed"] is False
    again = await client.post(f"/api/requests/{req.id}/approve", headers=headers_for(mgr))
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["decided_at"] == first.json()["decided_at"]
    assert (await client.post(f"/api/requests/{req.id}/approve", headers=headers_for(mgr2))).status_code == 409
    # different outcome by the same person is a conflict, not a replay
    assert (await client.post(f"/api/requests/{req.id}/reject", headers=headers_for(mgr))).status_code == 409


async def test_concurrent_resend_of_same_approval(client, make_employee, make_request, headers_for):
    owner, mgr = await make_employee(), await make_employee("manager")
    req = await make_request(owner)
    res = await asyncio.gather(*[
        client.post(f"/api/requests/{req.id}/reject", headers=headers_for(mgr)) for _ in range(6)
    ])
    assert all(r.status_code == 200 for r in res)
    assert sorted(r.json()["replayed"] for r in res) == [False] + [True] * 5


# --- payroll ---------------------------------------------------------------------------------

async def test_stuck_calculating_period_reset_on_worker_start(make_period):
    stuck = await make_period(2025, 1, "calculating")
    fresh = await make_period(2025, 2, "calculating")
    never = await make_period(2025, 4, "calculating")  # no start time recorded: also treated as stuck
    async with SessionLocal() as s:
        old = datetime.now(timezone.utc) - timedelta(seconds=get_settings().payroll_job_timeout + 60)
        await s.execute(update(PayrollPeriod).where(PayrollPeriod.id == stuck.id).values(calculating_started_at=old))
        await s.execute(update(PayrollPeriod).where(PayrollPeriod.id == fresh.id).values(calculating_started_at=func.now()))
        await s.commit()
    assert await recover_stuck_periods() == 2
    async with SessionLocal() as s:
        status = {p.id: p.status for p in (await s.execute(select(PayrollPeriod))).scalars()}
    assert status == {stuck.id: "draft", fresh.id: "calculating", never.id: "draft"}


async def test_lock_refused_when_request_decided_after_calculation(
    client, make_employee, make_request, make_period, headers_for
):
    hr, owner = await make_employee("hr"), await make_employee()
    period = await make_period(2025, 3)
    req = await make_request(owner)
    now = datetime.now(timezone.utc)

    async def setup(decided_at):
        async with SessionLocal() as s:
            await s.execute(update(PayrollPeriod).where(PayrollPeriod.id == period.id).values(calculated_at=now))
            await s.execute(update(Request).where(Request.id == req.id).values(status="approved", decided_at=decided_at))
            await s.commit()

    await setup(now + timedelta(minutes=1))
    r = await client.post(f"/api/payroll/periods/{period.id}/lock", headers=headers_for(hr))
    assert r.status_code == 409 and "recalculate" in r.json()["detail"]
    await setup(now - timedelta(minutes=1))
    r = await client.post(f"/api/payroll/periods/{period.id}/lock", headers=headers_for(hr))
    assert r.status_code == 200 and r.json()["status"] == "locked"
    assert (await client.post("/api/payroll/periods/99999/lock", headers=headers_for(hr))).status_code == 404


async def test_payroll_job_rerun_does_not_duplicate_payslips(
    make_employee, make_contract, make_rule, make_period
):
    from app.modules.payroll.models import Payslip
    from app.workers.payroll_job import calculate_payroll

    await make_rule()
    emp = await make_employee()
    await make_contract(emp)
    period = await make_period(2025, 3)
    for _ in range(2):
        async with SessionLocal() as s:
            await s.execute(update(PayrollPeriod).where(PayrollPeriod.id == period.id).values(status="calculating"))
            await s.commit()
        await calculate_payroll({}, period.id)
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Payslip)) == 1


# --- hardening -------------------------------------------------------------------------------

async def test_request_id_echoed_and_generated(client):
    r = await client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    r = await client.get("/health/live", headers={"X-Request-ID": "bad id\twith spaces"})
    assert len(r.headers["x-request-id"]) == 32


async def test_access_log_line_is_json_without_ids_in_route(client, make_employee, make_request, headers_for, caplog):
    owner, mgr = await make_employee(), await make_employee("manager")
    req = await make_request(owner)
    with caplog.at_level(logging.INFO, logger="app.access"):
        await client.post(f"/api/requests/{req.id}/approve", headers=headers_for(mgr))
    line = json.loads(caplog.records[-1].getMessage())
    assert line["route"] == "/api/requests/{request_id}/approve"
    assert line["method"] == "POST" and line["status"] == 200 and line["user_id"] == mgr.id
    assert {"request_id", "duration_ms"} <= line.keys()


async def test_503_is_logged_with_request_id(client, caplog):
    async def broken_session():
        raise PoolTimeoutError("pool")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = broken_session
    try:
        with caplog.at_level(logging.WARNING, logger="app.errors"):
            r = await client.post("/api/auth/login", json={"code": "A", "password": "b"},
                                  headers={"X-Request-ID": "rid-503"})
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert r.status_code == 503
    assert any("rid-503" in rec.getMessage() for rec in caplog.records)


async def test_health_live_does_not_touch_db(client, monkeypatch):
    import app.main as main

    def boom(*a, **k):
        raise AssertionError("health/live must not use the database")

    monkeypatch.setattr(main, "SessionLocal", boom)
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    assert (await client.get("/health/ready")).status_code == 503  # the db check now fails
    assert (await client.get("/health")).status_code == 503


async def test_health_ready_ok(client):
    assert (await client.get("/health/ready")).json() == {"status": "ok", "db": "ok", "redis": "ok"}


async def test_login_rate_limit_429(client, settings):
    settings(rate_limit_enabled=True, rate_login_per_min=3)
    code = "RL" + uuid.uuid4().hex[:8]
    codes = [(await client.post("/api/auth/login", json={"code": code, "password": "x"})).status_code
             for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    r = await client.post("/api/auth/login", json={"code": code, "password": "x"})
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60


async def test_write_rate_limit_per_user(client, make_employee, headers_for, settings):
    settings(rate_limit_enabled=True, rate_write_per_min=2)
    emp = await make_employee()
    from app.core import redis as redis_pool

    # ids restart in every test, so clear the counter before and after
    await redis_pool.get_redis().delete(f"rl:write:{emp.id}")
    h = headers_for(emp)
    codes = [(await client.post("/api/requests", json=OT_BODY, headers=h)).status_code for _ in range(4)]
    await redis_pool.get_redis().delete(f"rl:write:{emp.id}")
    assert codes == [201, 201, 429, 429]


async def test_rate_limit_fail_open_and_closed(client, make_employee, headers_for, settings, monkeypatch):
    from app.core import redis as redis_pool

    settings(rate_limit_enabled=True)

    async def broken(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis_pool.get_redis(), "eval", broken)
    assert (await client.post("/api/auth/login", json={"code": "ZZ", "password": "x"})).status_code == 401
    settings(rate_limit_fail_open=False)
    r = await client.post("/api/auth/login", json={"code": "ZZ", "password": "x"})
    assert r.status_code == 503 and "retry-after" in r.headers


async def test_connection_budget_warning(settings, caplog):
    settings(uvicorn_workers=50, db_pool_size=10, db_max_overflow=5)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        await check_connection_budget()
    assert any("exceeds max_connections" in r.getMessage() for r in caplog.records)
    caplog.clear()
    settings(uvicorn_workers=1)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        await check_connection_budget()
    assert not caplog.records


# --- auth cache ------------------------------------------------------------------------------

@pytest.fixture
def statements():
    seen: list[str] = []

    def on_execute(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", on_execute)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", on_execute)


async def test_auth_cache_skips_lookup_within_ttl(client, make_employee, headers_for, statements):
    emp = await make_employee()
    h = headers_for(emp)
    statements.clear()
    assert (await client.get("/api/attendance/me", headers=h)).status_code == 200
    first = len(statements)
    statements.clear()
    assert (await client.get("/api/attendance/me", headers=h)).status_code == 200
    assert len(statements) == first - 1  # the employee lookup is gone


async def test_auth_cache_ttl_zero_and_expiry(client, make_employee, headers_for, statements, settings):
    emp = await make_employee()
    h = headers_for(emp)
    settings(auth_cache_ttl_seconds=0)
    auth_cache.clear()
    await client.get("/api/attendance/me", headers=h)
    statements.clear()
    await client.get("/api/attendance/me", headers=h)
    uncached = len(statements)
    settings(auth_cache_ttl_seconds=30)
    await client.get("/api/attendance/me", headers=h)
    statements.clear()
    await client.get("/api/attendance/me", headers=h)
    assert len(statements) == uncached - 1


async def test_left_employee_blocked_after_cache_expires(client, make_employee, headers_for):
    from datetime import date

    from app.modules.employees.models import Employee

    emp = await make_employee()
    h = headers_for(emp)
    assert (await client.get("/api/attendance/me", headers=h)).status_code == 200
    async with SessionLocal() as s:
        await s.execute(update(Employee).where(Employee.id == emp.id).values(left_on=date(2025, 1, 1)))
        await s.commit()
    assert (await client.get("/api/attendance/me", headers=h)).status_code == 200  # still cached (documented trade-off)
    auth_cache.clear()  # what TTL expiry does
    assert (await client.get("/api/attendance/me", headers=h)).status_code == 401


async def test_patch_invalidates_cache(client, make_employee, headers_for):
    hr, emp = await make_employee("hr"), await make_employee()
    assert (await client.get("/api/attendance/me", headers=headers_for(emp))).status_code == 200
    assert auth_cache.get(emp.id) is not None
    r = await client.patch(f"/api/employees/{emp.id}", json={"version": 1, "department": "Moi"}, headers=headers_for(hr))
    assert r.status_code == 200
    assert auth_cache.get(emp.id) is None
