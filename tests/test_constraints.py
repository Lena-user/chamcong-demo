import pathlib
import re
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.db import SessionLocal
from app.main import app


async def _exec(sql: str, params: dict | None = None):
    async with SessionLocal() as s:
        await s.execute(text(sql), params or {})
        await s.commit()


async def _event(emp_id: int) -> uuid.UUID:
    key = uuid.uuid4()
    await _exec(
        "INSERT INTO attendance_events (employee_id, event_time, source, idempotency_key) VALUES (:e, now(), 'web', :k)",
        {"e": emp_id, "k": key},
    )
    return key


# --- NV-5: attendance_events is append-only ---------------------------------------------------

async def test_nv5_trigger_blocks_update_and_delete(make_employee):
    emp = await make_employee()
    key = await _event(emp.id)
    with pytest.raises(DBAPIError, match="append-only"):
        await _exec("UPDATE attendance_events SET source = 'x' WHERE idempotency_key = :k", {"k": key})
    with pytest.raises(DBAPIError, match="append-only"):
        await _exec("DELETE FROM attendance_events WHERE idempotency_key = :k", {"k": key})
    with pytest.raises(DBAPIError, match="append-only"):
        await _exec("DELETE FROM employees WHERE id = :e", {"e": emp.id})  # cascade cannot sneak past either
    async with SessionLocal() as s:
        assert (await s.execute(text("SELECT count(*) FROM attendance_events"))).scalar() == 1


def test_nv5_no_mutating_endpoint_for_events():
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/attendance"):
            assert getattr(route, "methods", set()) <= {"GET", "POST", "HEAD"}, path


def test_nv5_no_code_updates_or_deletes_events():
    pattern = re.compile(
        r"(update|delete)\(\s*AttendanceEvent|UPDATE\s+attendance_events|DELETE\s+FROM\s+attendance_events", re.I
    )
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = [str(p) for p in root.rglob("*.py") if pattern.search(p.read_text()) and "models.py" not in p.name]
    assert offenders == []


# --- NV-6: constraints live in the database ---------------------------------------------------

async def test_nv6_employees(make_employee):
    emp = await make_employee()
    base = ("INSERT INTO employees (code, full_name, department, hired_on, role, password_hash) "
            "VALUES (:c, 'n', 'd', '2024-01-01', :r, 'h')")
    with pytest.raises(IntegrityError):  # role CHECK
        await _exec(base, {"c": "ZZ1", "r": "ceo"})
    with pytest.raises(IntegrityError):  # UNIQUE code
        await _exec(base, {"c": emp.code, "r": "employee"})
    with pytest.raises(IntegrityError):  # version CHECK
        await _exec("UPDATE employees SET version = 0 WHERE id = :e", {"e": emp.id})


async def test_nv6_money_and_dependents(make_employee):
    emp = await make_employee()
    contract = ("INSERT INTO salary_contracts (employee_id, base_salary, allowance, effective_from) "
                "VALUES (:e, :b, 0, '2024-01-01')")
    with pytest.raises(IntegrityError):
        await _exec(contract, {"e": emp.id, "b": 0})
    with pytest.raises(IntegrityError):  # FK to a missing employee
        await _exec(contract, {"e": 999999, "b": 1})
    with pytest.raises(IntegrityError):
        await _exec("INSERT INTO employee_sensitive (employee_id, national_id, bank_account, tax_code, dependents) "
                    "VALUES (:e, '1', '1', '1', -1)", {"e": emp.id})


async def test_nv6_attendance(make_employee):
    emp = await make_employee()
    key = await _event(emp.id)
    with pytest.raises(IntegrityError):  # UNIQUE idempotency_key
        await _exec("INSERT INTO attendance_events (employee_id, event_time, source, idempotency_key) "
                    "VALUES (:e, now(), 'web', :k)", {"e": emp.id, "k": key})
    with pytest.raises(IntegrityError):  # FK
        await _event(999999)
    daily = "INSERT INTO attendance_daily (employee_id, work_date, worked_minutes, ot_minutes) VALUES (:e, '2025-03-01', :w, 0)"
    await _exec(daily, {"e": emp.id, "w": 1})
    with pytest.raises(IntegrityError):  # UNIQUE (employee, date)
        await _exec(daily, {"e": emp.id, "w": 1})
    with pytest.raises(IntegrityError):  # CHECK minutes >= 0
        await _exec(daily.replace("'2025-03-01'", "'2025-03-02'"), {"e": emp.id, "w": -5})


async def test_nv6_requests(make_employee):
    emp = await make_employee()
    ins = ("INSERT INTO requests (employee_id, type, work_date, status, start_at, end_at) "
           "VALUES (:e, :t, '2025-03-01', :s, :a, :b)")
    ok = {"e": emp.id, "t": "ot", "s": "pending", "a": datetime(2025, 3, 1, 8, tzinfo=timezone.utc),
          "b": datetime(2025, 3, 1, 9, tzinfo=timezone.utc)}
    await _exec(ins, ok)
    for bad in ({"t": "bogus"}, {"s": "done"}, {"a": ok["b"], "b": ok["a"]}):
        with pytest.raises(IntegrityError):
            await _exec(ins, {**ok, **bad})


async def test_nv6_payroll():
    with pytest.raises(IntegrityError):
        await _exec("INSERT INTO payroll_periods (year, month, status) VALUES (2025, 13, 'draft')")
    with pytest.raises(IntegrityError):
        await _exec("INSERT INTO payroll_periods (year, month, status) VALUES (2025, 1, 'weird')")
    await _exec("INSERT INTO payroll_periods (year, month, status) VALUES (2025, 1, 'draft')")
    with pytest.raises(IntegrityError):  # UNIQUE (year, month)
        await _exec("INSERT INTO payroll_periods (year, month, status) VALUES (2025, 1, 'draft')")


async def test_nv6_payslip_unique_and_net(make_employee, make_period):
    emp, period = await make_employee(), await make_period()
    ins = "INSERT INTO payslips (period_id, employee_id, net_pay, rule_version) VALUES (:p, :e, :n, 1)"
    with pytest.raises(IntegrityError):
        await _exec(ins, {"p": period.id, "e": emp.id, "n": -1})
    await _exec(ins, {"p": period.id, "e": emp.id, "n": 0})
    with pytest.raises(IntegrityError):
        await _exec(ins, {"p": period.id, "e": emp.id, "n": 0})
