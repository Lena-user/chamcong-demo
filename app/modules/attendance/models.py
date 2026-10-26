import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    DDL, event,
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Integer,
    String, Time, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models_base import Base


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    crosses_midnight: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="uq_schedules_emp_date"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"))
    work_date: Mapped[date] = mapped_column(Date)
    shift_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shifts.id"))


class AttendanceEvent(Base):
    """Append-only (NV-5): never UPDATE or DELETE rows from application code."""

    __tablename__ = "attendance_events"
    __table_args__ = (
        Index("ix_attendance_events_emp_time", "employee_id", "event_time"),
        # Append-only and time-ordered: a BRIN index is tiny and almost free on insert,
        # and serves "last N seconds" queries for the live monitor.
        Index("ix_attendance_events_time_brin", "event_time", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(20))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttendanceDaily(Base):
    __tablename__ = "attendance_daily"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_attendance_daily_emp_date"),
        CheckConstraint("worked_minutes >= 0 AND ot_minutes >= 0", name="ck_daily_minutes"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="CASCADE"))
    work_date: Mapped[date] = mapped_column(Date)
    first_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worked_minutes: Mapped[int] = mapped_column(Integer, server_default="0")
    ot_minutes: Mapped[int] = mapped_column(Integer, server_default="0")


# NV-5: the database itself rejects UPDATE/DELETE on attendance_events. The same statements
# are in migration 0002; here they are attached so metadata.create_all (tests) gets them too.
# Note: no '%' in these strings (DDL treats it as a format marker).
EVENTS_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION attendance_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'attendance_events is append-only';
END;
$$ LANGUAGE plpgsql
"""
EVENTS_GUARD_TRIGGER = """
CREATE TRIGGER trg_attendance_events_append_only
BEFORE UPDATE OR DELETE ON attendance_events
FOR EACH ROW EXECUTE FUNCTION attendance_events_append_only()
"""
event.listen(AttendanceEvent.__table__, "after_create", DDL(EVENTS_GUARD_FUNCTION))
event.listen(AttendanceEvent.__table__, "after_create", DDL(EVENTS_GUARD_TRIGGER))
