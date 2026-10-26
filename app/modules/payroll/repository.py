from datetime import date, datetime, timedelta

from sqlalchemy import Row, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.requests.models import Request
from app.modules.payroll.models import PayrollPeriod, PayrollRule, Payslip

_PAYSLIP_VALUE_COLUMNS = (
    "worked_minutes", "ot_minutes", "base_pay", "ot_pay", "allowance", "gross", "social_insurance",
    "health_insurance", "unemployment_insurance", "taxable_income", "income_tax", "net_pay", "rule_version",
)


async def insert_period(session: AsyncSession, year: int, month: int) -> PayrollPeriod | None:
    stmt = (
        pg_insert(PayrollPeriod).values(year=year, month=month)
        .on_conflict_do_nothing(constraint="uq_payroll_periods_year_month")
        .returning(PayrollPeriod)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_period(session: AsyncSession, period_id: int) -> PayrollPeriod | None:
    return await session.get(PayrollPeriod, period_id, populate_existing=True)


async def status_for_share(session: AsyncSession, year: int, month: int) -> str | None:
    """NV-4: read the period with a shared row lock so it cannot flip to 'calculating' mid-transaction."""
    stmt = (
        select(PayrollPeriod.status)
        .where(PayrollPeriod.year == year, PayrollPeriod.month == month)
        .with_for_update(read=True)
    )
    return await session.scalar(stmt)


async def begin_calculation(session: AsyncSession, period_id: int) -> PayrollPeriod | None:
    """draft -> calculating; matches 0 rows when anyone else got there first."""
    stmt = (
        update(PayrollPeriod)
        .where(PayrollPeriod.id == period_id, PayrollPeriod.status == "draft")
        .values(status="calculating", calculating_started_at=func.now())
        .returning(PayrollPeriod)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def revert_to_draft(session: AsyncSession, period_id: int, calculated: bool) -> None:
    """calculating -> draft. `calculated` stamps calculated_at; otherwise it is cleared."""
    await session.execute(
        update(PayrollPeriod)
        .where(PayrollPeriod.id == period_id, PayrollPeriod.status == "calculating")
        .values(
            status="draft", calculated_at=func.now() if calculated else None, calculating_started_at=None
        )
    )


async def get_period_for_update(session: AsyncSession, period_id: int) -> PayrollPeriod | None:
    """Row lock: waits for in-flight request decisions (they hold a shared lock on this row)."""
    stmt = (
        select(PayrollPeriod).where(PayrollPeriod.id == period_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def has_decisions_after(session: AsyncSession, start: date, end: date, since: datetime) -> bool:
    stmt = select(
        exists().where(
            Request.work_date.between(start, end),
            Request.status == "approved",  # rejected requests do not change payslips
            Request.decided_at > since,
        )
    )
    return bool(await session.scalar(stmt))


async def mark_locked(session: AsyncSession, period_id: int) -> PayrollPeriod:
    stmt = (
        update(PayrollPeriod).where(PayrollPeriod.id == period_id).values(status="locked")
        .returning(PayrollPeriod).execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def reset_stuck_calculations(session: AsyncSession, older_than_seconds: int) -> int:
    """calculating -> draft for periods whose job must be dead (started longer ago than the job timeout)."""
    stmt = (
        update(PayrollPeriod)
        .where(
            PayrollPeriod.status == "calculating",
            or_(
                PayrollPeriod.calculating_started_at.is_(None),
                PayrollPeriod.calculating_started_at < func.now() - timedelta(seconds=older_than_seconds),
            ),
        )
        .values(status="draft", calculating_started_at=None)
    )
    return (await session.execute(stmt)).rowcount


async def get_rule(session: AsyncSession, as_of: date) -> PayrollRule | None:
    stmt = (
        select(PayrollRule).where(PayrollRule.effective_from <= as_of)
        .order_by(PayrollRule.effective_from.desc(), PayrollRule.version.desc()).limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_payslips(session: AsyncSession, period_id: int, rows: list[dict]) -> None:
    for i in range(0, len(rows), 500):
        chunk = [{"period_id": period_id, **r} for r in rows[i : i + 500]]
        stmt = pg_insert(Payslip).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_payslips_period_emp",
            set_={c: getattr(stmt.excluded, c) for c in _PAYSLIP_VALUE_COLUMNS} | {"created_at": func.now()},
        )
        await session.execute(stmt)


async def get_payslip(session: AsyncSession, period_id: int, employee_id: int) -> Payslip | None:
    stmt = select(Payslip).where(Payslip.period_id == period_id, Payslip.employee_id == employee_id)
    return (await session.execute(stmt)).scalar_one_or_none()
