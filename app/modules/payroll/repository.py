from datetime import date

from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

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
        .values(status="calculating")
        .returning(PayrollPeriod)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def revert_to_draft(session: AsyncSession, period_id: int, calculated: bool) -> None:
    """calculating -> draft. `calculated` stamps calculated_at; otherwise it is cleared."""
    await session.execute(
        update(PayrollPeriod)
        .where(PayrollPeriod.id == period_id, PayrollPeriod.status == "calculating")
        .values(status="draft", calculated_at=func.now() if calculated else None)
    )


async def lock_period(session: AsyncSession, period_id: int) -> PayrollPeriod | None:
    stmt = (
        update(PayrollPeriod)
        .where(
            PayrollPeriod.id == period_id,
            PayrollPeriod.status == "draft",
            PayrollPeriod.calculated_at.is_not(None),
        )
        .values(status="locked")
        .returning(PayrollPeriod)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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
