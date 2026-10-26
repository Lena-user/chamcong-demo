from datetime import date, datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Identity, Integer, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models_base import Base


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("role IN ('employee','manager','hr','admin')", name="ck_employees_role"),
        CheckConstraint("version >= 1", name="ck_employees_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    full_name: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(50), index=True)
    hired_on: Mapped[date] = mapped_column(Date)
    left_on: Mapped[date | None] = mapped_column(Date)
    role: Mapped[str] = mapped_column(String(10))
    password_hash: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeeSensitive(Base):
    __tablename__ = "employee_sensitive"
    __table_args__ = (CheckConstraint("dependents >= 0", name="ck_sensitive_dependents"),)

    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True
    )
    national_id: Mapped[str] = mapped_column(String(20))
    bank_account: Mapped[str] = mapped_column(String(30))
    tax_code: Mapped[str] = mapped_column(String(20))
    dependents: Mapped[int] = mapped_column(Integer, server_default="0")


class SalaryContract(Base):
    __tablename__ = "salary_contracts"
    __table_args__ = (
        CheckConstraint("base_salary > 0", name="ck_contracts_base_salary"),
        CheckConstraint("allowance >= 0", name="ck_contracts_allowance"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="ck_contracts_dates"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    base_salary: Mapped[int] = mapped_column(BigInteger)
    allowance: Mapped[int] = mapped_column(BigInteger, server_default="0")
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # No FK on purpose: audit rows must survive deletion of employees.
    actor_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
