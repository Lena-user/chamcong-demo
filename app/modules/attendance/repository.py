import uuid
from datetime import date, datetime

from sqlalchemy import Row, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attendance.models import AttendanceDaily, AttendanceEvent


async def insert_event(
    session: AsyncSession, employee_id: int, event_time: datetime | None, source: str, key: uuid.UUID
) -> Row | None:
    """NV-1: INSERT ... ON CONFLICT DO NOTHING. Returns None when the key already exists.

    `event_time=None` means the database clock (now()), so all app processes share one time source.
    """
    stmt = (
        pg_insert(AttendanceEvent)
        .values(
            employee_id=employee_id, event_time=func.now() if event_time is None else event_time,
            source=source, idempotency_key=key,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(AttendanceEvent.id, AttendanceEvent.event_time)
    )
    return (await session.execute(stmt)).first()


async def get_event_by_key(session: AsyncSession, key: uuid.UUID) -> Row | None:
    stmt = select(
        AttendanceEvent.id, AttendanceEvent.event_time, AttendanceEvent.employee_id
    ).where(AttendanceEvent.idempotency_key == key)
    return (await session.execute(stmt)).first()


# --- attendance_daily SQL ------------------------------------------------------------------
# All timestamps are interpreted in :tz. A night-shift event after midnight (local hour <
# :cutoff) belongs to the previous day when that day's scheduled shift crosses midnight.

def _work_date_sql(time_expr: str, emp_expr: str) -> str:
    local = f"({time_expr} AT TIME ZONE CAST(:tz AS text))"
    return f"""CASE WHEN EXTRACT(HOUR FROM {local}) < CAST(:cutoff AS int) AND EXISTS (
        SELECT 1 FROM schedules s JOIN shifts sh ON sh.id = s.shift_id
        WHERE s.employee_id = {emp_expr} AND s.work_date = {local}::date - 1 AND sh.crosses_midnight
    ) THEN {local}::date - 1 ELSE {local}::date END"""


def _worked_sql(first_expr: str, last_expr: str) -> str:
    minutes = f"FLOOR(EXTRACT(EPOCH FROM ({last_expr} - {first_expr})) / 60)::int"
    return f"GREATEST(0, {minutes} - CASE WHEN {minutes} > :break_after THEN :break_minutes ELSE 0 END)"


# Incremental form: first/last are merged with the row already locked by ON CONFLICT, so two
# concurrent checks of one employee cannot overwrite each other with a stale aggregate.
_FIRST = "LEAST(d.first_in, EXCLUDED.first_in)"
_LAST = "GREATEST(COALESCE(d.last_out, d.first_in), EXCLUDED.first_in)"
_WORKED = _worked_sql(_FIRST, _LAST)

_UPSERT_FOR_EVENT = text(f"""
INSERT INTO attendance_daily AS d (employee_id, work_date, first_in, last_out, worked_minutes, ot_minutes)
SELECT CAST(:emp AS bigint), x.wd, CAST(:t AS timestamptz), NULL, 0, 0
FROM (SELECT {_work_date_sql("CAST(:t AS timestamptz)", "CAST(:emp AS bigint)")} AS wd) x
ON CONFLICT (employee_id, work_date) DO UPDATE SET
    first_in = {_FIRST},
    last_out = CASE WHEN {_LAST} > {_FIRST} THEN {_LAST} END,
    worked_minutes = {_WORKED},
    ot_minutes = GREATEST(0, {_WORKED} - :standard)
""")

_REBUILD = text(f"""
WITH ev AS (
    SELECT e.event_time FROM attendance_events e
    WHERE e.employee_id = CAST(:emp AS bigint)
      AND e.event_time >= (CAST(:wd AS date)::timestamp AT TIME ZONE CAST(:tz AS text))
      AND e.event_time <  ((CAST(:wd AS date) + 2)::timestamp AT TIME ZONE CAST(:tz AS text))
      AND {_work_date_sql("e.event_time", "e.employee_id")} = CAST(:wd AS date)
), agg AS (
    SELECT min(event_time) AS first_in, max(event_time) AS last_out FROM ev
)
INSERT INTO attendance_daily AS d (employee_id, work_date, first_in, last_out, worked_minutes, ot_minutes)
SELECT CAST(:emp AS bigint), CAST(:wd AS date), first_in,
       CASE WHEN last_out > first_in THEN last_out END,
       {_worked_sql("first_in", "last_out")},
       GREATEST(0, {_worked_sql("first_in", "last_out")} - :standard)
FROM agg WHERE first_in IS NOT NULL
ON CONFLICT (employee_id, work_date) DO UPDATE SET
    first_in = EXCLUDED.first_in, last_out = EXCLUDED.last_out,
    worked_minutes = EXCLUDED.worked_minutes, ot_minutes = EXCLUDED.ot_minutes
""")


def _params(tz: str, s) -> dict:
    return {
        "tz": tz, "cutoff": s.night_shift_cutoff_hour, "standard": s.standard_daily_minutes,
        "break_after": s.unpaid_break_after_minutes, "break_minutes": s.unpaid_break_minutes,
    }


async def upsert_daily_for_event(session: AsyncSession, employee_id: int, event_time: datetime, settings) -> None:
    await session.execute(
        _UPSERT_FOR_EVENT, {**_params(settings.timezone, settings), "emp": employee_id, "t": event_time}
    )


async def rebuild_daily(session: AsyncSession, employee_id: int, work_date: date, settings) -> None:
    await session.execute(
        _REBUILD, {**_params(settings.timezone, settings), "emp": employee_id, "wd": work_date}
    )


async def list_daily(
    session: AsyncSession, employee_id: int, date_from: date | None, date_to: date | None,
    offset: int, limit: int,
) -> tuple[list[AttendanceDaily], int]:
    where = [AttendanceDaily.employee_id == employee_id]
    if date_from:
        where.append(AttendanceDaily.work_date >= date_from)
    if date_to:
        where.append(AttendanceDaily.work_date <= date_to)
    total = await session.scalar(select(func.count()).select_from(AttendanceDaily).where(*where))
    result = await session.execute(
        select(AttendanceDaily).where(*where)
        .order_by(AttendanceDaily.work_date.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars()), total or 0


async def daily_in_range(session: AsyncSession, start: date, end: date) -> list[Row]:
    stmt = select(
        AttendanceDaily.employee_id, AttendanceDaily.work_date,
        AttendanceDaily.worked_minutes, AttendanceDaily.ot_minutes,
    ).where(AttendanceDaily.work_date.between(start, end))
    return list((await session.execute(stmt)).all())


_LIVE_SQL = text("""
    SELECT date_trunc('second', event_time) AS ts, count(*) AS events,
           count(DISTINCT employee_id) AS employees
    FROM attendance_events
    WHERE event_time > now() - make_interval(secs => :seconds) AND event_time <= now()
    GROUP BY GROUPING SETS ((date_trunc('second', event_time)), ())
""")


async def live_window(session: AsyncSession, seconds: int) -> list[Row]:
    """Per-second check-in counts for the last `seconds`, plus one total row (ts IS NULL)."""
    return list((await session.execute(_LIVE_SQL, {"seconds": seconds})).all())


async def latest_events(session: AsyncSession, limit: int) -> list[Row]:
    stmt = (
        select(AttendanceEvent.id, AttendanceEvent.employee_id, AttendanceEvent.event_time, AttendanceEvent.source)
        .order_by(AttendanceEvent.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).all())


def _daily_check_sql(scoped: bool):
    """Recompute attendance_daily from the raw events and compare every row with what is stored.

    Scoped: only the (employee, work day) pairs that got events since :since.
    """
    emp_scope = ("WHERE e.employee_id IN (SELECT employee_id FROM attendance_events WHERE created_at >= :since)"
                 if scoped else "")
    pair_scope = "WHERE created_at >= :since" if scoped else ""
    stored_join = "JOIN pairs p ON p.employee_id = d.employee_id AND p.wd = d.work_date" if scoped else ""
    return text(f"""
WITH ev AS (
    SELECT e.employee_id, {_work_date_sql("e.event_time", "e.employee_id")} AS wd, e.event_time, e.created_at
    FROM attendance_events e {emp_scope}
), pairs AS (
    SELECT DISTINCT employee_id, wd FROM ev {pair_scope}
), agg AS (
    SELECT ev.employee_id, ev.wd, min(ev.event_time) AS first_in, max(ev.event_time) AS last_out
    FROM ev JOIN pairs USING (employee_id, wd) GROUP BY 1, 2
), expected AS (
    SELECT employee_id, wd, first_in, CASE WHEN last_out > first_in THEN last_out END AS last_out,
           {_worked_sql("first_in", "last_out")} AS worked
    FROM agg
), stored AS (
    SELECT d.* FROM attendance_daily d {stored_join}
), cmp AS (
    SELECT coalesce(x.employee_id, s.employee_id) AS employee_id, coalesce(x.wd, s.work_date) AS work_date,
           (x.employee_id IS NULL OR s.employee_id IS NULL
            OR s.first_in IS DISTINCT FROM x.first_in OR s.last_out IS DISTINCT FROM x.last_out
            OR s.worked_minutes IS DISTINCT FROM x.worked
            OR s.ot_minutes IS DISTINCT FROM GREATEST(0, x.worked - :standard)) AS bad
    FROM expected x FULL JOIN stored s ON s.employee_id = x.employee_id AND s.work_date = x.wd
)
SELECT count(*) AS checked, count(*) FILTER (WHERE bad) AS bad,
       (array_agg(employee_id::text || ' ' || work_date::text) FILTER (WHERE bad))[1:5] AS sample
FROM cmp
""")


async def check_daily(session: AsyncSession, since: datetime | None, settings) -> Row:
    params = _params(settings.timezone, settings)
    if since is not None:
        params["since"] = since
    return (await session.execute(_daily_check_sql(since is not None), params)).one()


async def check_events(session: AsyncSession) -> Row:
    """Raw check-ins: duplicate keys, rows in the future, and proof the table was never updated/deleted."""
    return (await session.execute(text("""
        SELECT
          (SELECT count(*) FROM (SELECT 1 FROM attendance_events GROUP BY idempotency_key
                                 HAVING count(*) > 1) d) AS duplicate_keys,
          (SELECT count(*) FROM attendance_events WHERE event_time > now() + interval '5 seconds') AS future_rows,
          (SELECT n_tup_upd + n_tup_del FROM pg_stat_user_tables WHERE relname = 'attendance_events') AS updates_deletes,
          (SELECT count(*) FROM pg_trigger WHERE tgrelid = 'attendance_events'::regclass
                                             AND NOT tgisinternal AND tgenabled <> 'D') AS guard_triggers,
          (SELECT count(*) FROM attendance_events) AS total
    """))).one()
