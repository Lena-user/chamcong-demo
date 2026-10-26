from datetime import date, datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Identity, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models_base import Base


class PayrollPeriod(Base):
    __tablename__ = "payroll_periods"
    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_payroll_periods_year_month"),
        CheckConstraint("status IN ('draft','calculating','locked','paid')", name="ck_periods_status"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_periods_month"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), server_default="draft")
    # Set when a calculation finished successfully; required before the period can be locked.
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payslip(Base):
    __tablename__ = "payslips"
    __table_args__ = (
        UniqueConstraint("period_id", "employee_id", name="uq_payslips_period_emp"),
        CheckConstraint("net_pay >= 0", name="ck_payslips_net"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    period_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payroll_periods.id", ondelete="CASCADE"))
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"))
    worked_minutes: Mapped[int] = mapped_column(Integer, server_default="0")
    ot_minutes: Mapped[int] = mapped_column(Integer, server_default="0")
    base_pay: Mapped[int] = mapped_column(BigInteger, server_default="0")
    ot_pay: Mapped[int] = mapped_column(BigInteger, server_default="0")
    allowance: Mapped[int] = mapped_column(BigInteger, server_default="0")
    gross: Mapped[int] = mapped_column(BigInteger, server_default="0")
    social_insurance: Mapped[int] = mapped_column(BigInteger, server_default="0")
    health_insurance: Mapped[int] = mapped_column(BigInteger, server_default="0")
    unemployment_insurance: Mapped[int] = mapped_column(BigInteger, server_default="0")
    taxable_income: Mapped[int] = mapped_column(BigInteger, server_default="0")
    income_tax: Mapped[int] = mapped_column(BigInteger, server_default="0")
    net_pay: Mapped[int] = mapped_column(BigInteger, server_default="0")
    rule_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PayrollRule(Base):
    __tablename__ = "payroll_rules"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    effective_from: Mapped[date] = mapped_column(Date)
    params: Mapped[dict] = mapped_column(JSONB)
    note: Mapped[str] = mapped_column(Text, server_default="")
