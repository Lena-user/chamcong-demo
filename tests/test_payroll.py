import asyncio
from datetime import date

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.modules.employees.models import AuditLog
from app.modules.payroll.models import PayrollPeriod, Payslip
from app.modules.attendance.models import AttendanceDaily
from app.workers.payroll_job import calculate_payroll


async def _period(pid):
    async with SessionLocal() as s:
        return await s.get(PayrollPeriod, pid, populate_existing=True)


async def _payslips(pid):
    async with SessionLocal() as s:
        return (await s.execute(select(Payslip).where(Payslip.period_id == pid))).scalars().all()


async def test_period_create_and_rbac(client, make_employee, headers_for):
    hr, emp = await make_employee("hr"), await make_employee()
    body = {"year": 2025, "month": 3}
    assert (await client.post("/api/payroll/periods", json=body, headers=headers_for(emp))).status_code == 403
    r = await client.post("/api/payroll/periods", json=body, headers=headers_for(hr))
    assert r.status_code == 201 and r.json()["status"] == "draft"
    assert (await client.post("/api/payroll/periods", json=body, headers=headers_for(hr))).status_code == 409
    assert (await client.post("/api/payroll/periods", json={"year": 2025, "month": 13}, headers=headers_for(hr))).status_code == 422
    for path in ("/calculate", "/lock"):
        assert (await client.post(f"/api/payroll/periods/1{path}", headers=headers_for(emp))).status_code == 403
    assert (await client.get("/api/payroll/jobs/x", headers=headers_for(emp))).status_code == 403


async def test_concurrent_period_create_and_calculate(client, make_employee, make_period, headers_for, enqueued):
    hr = await make_employee("hr")
    h = headers_for(hr)
    res = await asyncio.gather(*[client.post("/api/payroll/periods", json={"year": 2025, "month": 5}, headers=h) for _ in range(8)])
    assert sorted(r.status_code for r in res) == [201] + [409] * 7

    period = await make_period(2025, 6)
    res = await asyncio.gather(*[client.post(f"/api/payroll/periods/{period.id}/calculate", headers=h) for _ in range(8)])
    assert sorted(r.status_code for r in res) == [202] + [409] * 7
    assert enqueued == [period.id]  # a single job was queued
    assert (await _period(period.id)).status == "calculating"
    assert (await client.post("/api/payroll/periods/99999/calculate", headers=h)).status_code == 404


async def test_calculate_lock_flow_and_payslip(client, make_employee, make_contract, make_rule, make_period, headers_for):
    await make_rule()
    hr = await make_employee("hr")
    emps = [await make_employee() for _ in range(3)]
    for e in emps:
        await make_contract(e)
    period = await make_period(2025, 3)
    async with SessionLocal() as s:  # full month of attendance for the first employee
        for d in range(1, 27):
            s.add(AttendanceDaily(employee_id=emps[0].id, work_date=date(2025, 3, d), worked_minutes=480))
        await s.commit()
    h = headers_for(hr)

    # cannot lock before a calculation has finished
    assert (await client.post(f"/api/payroll/periods/{period.id}/lock", headers=h)).status_code == 409
    r = await client.post(f"/api/payroll/periods/{period.id}/calculate", headers=h)
    assert r.status_code == 202 and r.json()["job_id"]
    assert (await _period(period.id)).status == "calculating"
    assert (await client.post(f"/api/payroll/periods/{period.id}/lock", headers=h)).status_code == 409

    result = await calculate_payroll({}, period.id)  # what the Arq worker runs
    assert result == {"period_id": period.id, "payslips": 3}  # only staff with a contract
    done = await _period(period.id)
    assert done.status == "draft" and done.calculated_at is not None
    slips = {p.employee_id: p for p in await _payslips(period.id)}
    assert set(slips) == {e.id for e in emps} and all(p.rule_version == 1 for p in slips.values())
    assert (slips[emps[0].id].base_pay, slips[emps[0].id].net_pay) == (10_000_000, 9_950_000)
    assert slips[emps[1].id].base_pay == 0 and slips[emps[1].id].allowance == 1_000_000
    assert slips[emps[1].id].net_pay == 0  # insurance exceeds a zero-attendance month; floored at 0

    # my payslip: own data only, audited
    r = await client.get(f"/api/payroll/periods/{period.id}/payslips/me", headers=headers_for(emps[0]))
    assert r.status_code == 200 and r.json()["net_pay"] == 9_950_000
    assert (await client.get(f"/api/payroll/periods/{period.id}/payslips/me", headers=h)).status_code == 404
    async with SessionLocal() as s:
        n = await s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "payslip.view"))
    assert n == 1

    # lock once, then the period is final
    assert (await client.post(f"/api/payroll/periods/{period.id}/lock", headers=h)).json()["status"] == "locked"
    assert (await client.post(f"/api/payroll/periods/{period.id}/lock", headers=h)).status_code == 409
    assert (await client.post(f"/api/payroll/periods/{period.id}/calculate", headers=h)).status_code == 409
    assert (await client.post("/api/payroll/periods/99999/lock", headers=h)).status_code == 404


async def test_job_status_unknown_is_404(client, make_employee, headers_for):
    hr = await make_employee("hr")
    assert (await client.get("/api/payroll/jobs/does-not-exist", headers=headers_for(hr))).status_code == 404
