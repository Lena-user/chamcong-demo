import asyncio
import time
from datetime import date

import pytest
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.modules.payroll import service as payroll_service
from app.modules.payroll.models import PayrollPeriod, Payslip
from app.workers.payroll_job import calculate_payroll


async def _count_slips(pid):
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(Payslip).where(Payslip.period_id == pid))


async def _calculating(make_period, month):
    period = await make_period(2025, month, "calculating")
    return period.id


async def _staff(make_employee, make_contract, n=3):
    for _ in range(n):
        await make_contract(await make_employee())


async def test_two_jobs_same_period_do_not_duplicate(make_employee, make_contract, make_rule, make_period):
    await make_rule()
    await _staff(make_employee, make_contract, 5)
    pid = await _calculating(make_period, 3)
    results = await asyncio.gather(calculate_payroll({}, pid), calculate_payroll({}, pid))
    assert sorted(("skipped" in r) for r in results) == [False, True]  # the second sees it already done
    assert await _count_slips(pid) == 5


async def test_rerun_updates_in_place(make_employee, make_contract, make_rule, make_period):
    await make_rule()
    await _staff(make_employee, make_contract, 2)
    pid = await _calculating(make_period, 3)
    await calculate_payroll({}, pid)
    async with SessionLocal() as s:  # re-open for recalculation
        await s.execute(text("UPDATE payroll_periods SET status='calculating' WHERE id=:i"), {"i": pid})
        await s.commit()
    await calculate_payroll({}, pid)
    assert await _count_slips(pid) == 2


async def test_advisory_lock_serialises_jobs(make_employee, make_contract, make_rule, make_period, monkeypatch):
    await make_rule()
    await _staff(make_employee, make_contract, 2)
    p1, p2 = await _calculating(make_period, 1), await _calculating(make_period, 2)
    spans = []
    original = payroll_service.compute_period

    async def slow(session, period_id):
        start = time.monotonic()
        await asyncio.sleep(0.4)
        out = await original(session, period_id)
        spans.append((start, time.monotonic()))
        return out

    monkeypatch.setattr(payroll_service, "compute_period", slow)
    await asyncio.gather(calculate_payroll({}, p1), calculate_payroll({}, p2))
    (a0, a1), (b0, b1) = sorted(spans)
    assert a1 <= b0, "payroll jobs overlapped: the advisory lock did not serialise them"
    assert await _count_slips(p1) == 2 and await _count_slips(p2) == 2


async def test_job_waits_for_held_lock(make_employee, make_contract, make_rule, make_period):
    await make_rule()
    await _staff(make_employee, make_contract, 2)
    pid = await _calculating(make_period, 3)
    key = get_settings().payroll_lock_key
    async with engine.connect() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        task = asyncio.create_task(calculate_payroll({}, pid))
        await asyncio.sleep(0.6)
        assert not task.done() and await _count_slips(pid) == 0
        await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        await holder.commit()
    assert (await asyncio.wait_for(task, 10)) == {"period_id": pid, "payslips": 2}


async def test_failed_job_returns_period_to_draft(make_employee, make_contract, make_period):
    await _staff(make_employee, make_contract, 1)  # no payroll rule -> the job must fail
    pid = await _calculating(make_period, 3)
    with pytest.raises(ValueError):
        await calculate_payroll({}, pid)
    async with SessionLocal() as s:
        period = await s.get(PayrollPeriod, pid, populate_existing=True)
    assert period.status == "draft" and period.calculated_at is None
    assert await _count_slips(pid) == 0


async def test_job_skips_period_not_calculating(make_period, make_rule):
    await make_rule()
    period = await make_period(2025, 3, "locked")
    assert await calculate_payroll({}, period.id) == {"period_id": period.id, "skipped": True}


async def test_lock_is_released_after_job(make_employee, make_contract, make_rule, make_period):
    await make_rule()
    await _staff(make_employee, make_contract, 1)
    pid = await _calculating(make_period, 3)
    await calculate_payroll({}, pid)
    async with engine.connect() as other:
        got = await other.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": get_settings().payroll_lock_key})
        assert got is True
        await other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": get_settings().payroll_lock_key})
        await other.commit()


async def test_nv4_approve_races_calculate(
    client, make_employee, make_contract, make_rule, make_period, make_request, headers_for
):
    """Approve vs calculate at the same moment: the payslip must match the final request status."""
    await make_rule()
    hr = await make_employee("hr")
    for month in range(1, 9):
        owner = await make_employee()
        await make_contract(owner)
        period = await make_period(2025, month)
        req = await make_request(owner, date(2025, month, 10), "adjustment")
        approve, calc = await asyncio.gather(
            client.post(f"/api/requests/{req.id}/approve", headers=headers_for(hr)),
            client.post(f"/api/payroll/periods/{period.id}/calculate", headers=headers_for(hr)),
        )
        assert calc.status_code == 202
        assert approve.status_code in (200, 409)
        await calculate_payroll({}, period.id)
        (slip,) = [p for p in await _slips(period.id) if p.employee_id == owner.id]
        # 08:00-16:00 adjustment = 480 minutes, only if the approval got in before the freeze
        assert slip.worked_minutes == (480 if approve.status_code == 200 else 0)


async def _slips(pid):
    async with SessionLocal() as s:
        return (await s.execute(select(Payslip).where(Payslip.period_id == pid))).scalars().all()
