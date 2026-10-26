import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models_base import Base


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint("type IN ('leave','adjustment','ot')", name="ck_requests_type"),
        CheckConstraint("status IN ('pending','approved','rejected')", name="ck_requests_status"),
        CheckConstraint("end_at IS NULL OR start_at IS NULL OR end_at >= start_at", name="ck_requests_range"),
        Index("ix_requests_status_created", "status", "created_at"),
        Index("ix_requests_emp_created", "employee_id", "created_at"),
        Index("ix_requests_created_brin", "created_at", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(15))
    work_date: Mapped[date] = mapped_column(Date)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, server_default="")
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    approved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Client-chosen key that makes POST /api/requests safe to resend; NULL for requests without one.
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
