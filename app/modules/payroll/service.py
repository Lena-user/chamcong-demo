import calendar
from datetime import date

from arq.jobs import Job, JobStatus
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_pool
from app.core.audit import write_audit
from app.modules.attendance import service as attendance_service
from app.modules.employees import service as employees_service
from app.modules.employees.models import Employee
from app.modules.payroll import calculator, repository
from app.modules.payroll.guard import assert_period_open  # noqa: F401  (re-exported for callers)
from app.modules.payroll.models import PayrollPeriod, Payslip
from app.modules.requests import service as requests_service

JOB_NAME = "calculate_payroll"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


async def create_period(session: AsyncSession, year: int, month: int) -> PayrollPeriod:
    period = await repository.insert_period(session, year, month)
    if period is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Payroll period already exists")
    await session.commit()
    return period


async def enqueue_calculation(period_id: int) -> str:
    job = await redis_pool.get_redis().enqueue_job(JOB_NAME, period_id)
    if job is None:
        raise RuntimeError("job was not enqueued")
    return job.job_id


async def _not_ready(session: AsyncSession, period_id: int, message: str) -> HTTPException:
    if await repository.get_period(session, period_id) is None:
        return HTTPException(status.HTTP_404_NOT_FOUND, "Payroll period not found")
    return HTTPException(status.HTTP_409_CONFLICT, message)


async def start_calculation(session: AsyncSession, period_id: int) -> str:
    """draft -> calculating (conditional UPDATE), then queue the Arq job. Returns the job id."""
    period = await repository.begin_calculation(session, period_id)
    if period is None:
        raise await _not_ready(session, period_id, "Period is not in draft status")
    await session.commit()
    try:
        return await enqueue_calculation(period_id)
    except Exception:
        # Nothing will ever process the period: hand it back instead of leaving it stuck.
        await repository.revert_to_draft(session, period_id, calculated=False)
        await session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable")


async def job_status(job_id: str) -> dict:
    job = Job(job_id, redis_pool.get_redis())
    state = await job.status()
    if state == JobStatus.not_found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    out: dict = {"job_id": job_id, "status": state.value}
    if state == JobStatus.complete:
        info = await job.result_info()
        if info is not None:
            out["success"] = info.success
            if info.success and isinstance(info.result, dict):
                out["result"] = info.result
            elif not info.success:
                out["error"] = str(info.result)
    return out


async def lock_period(session: AsyncSession, period_id: int) -> PayrollPeriod:
    period = await repository.lock_period(session, period_id)
    if period is None:
        raise await _not_ready(session, period_id, "Period has not been calculated yet")
    await session.commit()
    return period


async def my_payslip(session: AsyncSession, actor: Employee, period_id: int) -> Payslip:
    payslip = await repository.get_payslip(session, period_id, actor.id)
    if payslip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payslip not found")
    write_audit(session, actor.id, "payslip.view", "payslip", payslip.id)
    await session.commit()
    return payslip


async def compute_period(session: AsyncSession, period_id: int) -> int:
    """Calculate and upsert payslips for every active employee. Caller commits. Returns the count."""
    period = await repository.get_period(session, period_id)
    if period is None:
        raise ValueError(f"period {period_id} not found")
    start, end = month_bounds(period.year, period.month)
    rule = await repository.get_rule(session, start)
    if rule is None:
        raise ValueError(f"no payroll rule in effect on {start}")
    params = rule.params

    inputs = await employees_service.payroll_inputs(session, start, end)
    daily = await attendance_service.daily_totals(session, start, end)
    approved = await requests_service.approved_minutes(session, start, end)

    worked_by_emp: dict[int, list[int]] = {}
    for key in daily.keys() | approved.keys():
        w, o = calculator.effective_minutes(daily.get(key), approved.get(key), params)
        acc = worked_by_emp.setdefault(key[0], [0, 0])
        acc[0] += w
        acc[1] += o

    rows = []
    for r in inputs:
        worked, ot = worked_by_emp.get(r.employee_id, (0, 0))
        slip = calculator.compute_payslip(params, r.base_salary, r.allowance, r.dependents, worked, ot)
        rows.append({"employee_id": r.employee_id, "rule_version": rule.version, **slip})
    await repository.upsert_payslips(session, period_id, rows)
    return len(rows)
