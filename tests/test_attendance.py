import asyncio
import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.modules.attendance import service
from app.modules.attendance.models import AttendanceDaily, AttendanceEvent, Schedule, Shift

VN = timezone(timedelta(hours=7))


async def _events(emp_id=None):
    async with SessionLocal() as s:
        q = select(AttendanceEvent)
        if emp_id:
            q = q.where(AttendanceEvent.employee_id == emp_id)
        return (await s.execute(q)).scalars().all()


async def _daily(emp_id):
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(AttendanceDaily).where(AttendanceDaily.employee_id == emp_id).order_by(AttendanceDaily.work_date)
        )).scalars().all()
    return rows


async def test_check_first_then_duplicate(client, make_employee, headers_for):
    emp = await make_employee()
    key = str(uuid.uuid4())
    r1 = await client.post("/api/attendance/check", json={"idempotency_key": key, "source": "web"}, headers=headers_for(emp))
    r2 = await client.post("/api/attendance/check", json={"idempotency_key": key, "source": "web"}, headers=headers_for(emp))
    assert (r1.status_code, r1.json()["duplicate"]) == (201, False)
    assert (r2.status_code, r2.json()["duplicate"]) == (200, True)
    assert r1.json()["event_id"] == r2.json()["event_id"]
    assert r1.json()["event_time"] == r2.json()["event_time"]
    assert len(await _events()) == 1


async def test_check_validation_and_auth(client, make_employee, headers_for):
    emp = await make_employee()
    assert (await client.post("/api/attendance/check", json={"idempotency_key": "nope"}, headers=headers_for(emp))).status_code == 422
    assert (await client.post("/api/attendance/check", json={"idempotency_key": str(uuid.uuid4())})).status_code == 401


async def test_check_key_of_another_employee_is_409(client, make_employee, headers_for):
    a, b = await make_employee(), await make_employee()
    key = str(uuid.uuid4())
    assert (await client.post("/api/attendance/check", json={"idempotency_key": key}, headers=headers_for(a))).status_code == 201
    r = await client.post("/api/attendance/check", json={"idempotency_key": key}, headers=headers_for(b))
    assert r.status_code == 409
    evs = await _events()
    assert len(evs) == 1 and evs[0].employee_id == a.id


async def test_nv1_concurrent_same_key(client, make_employee, headers_for):
    """NV-1: 20 simultaneous requests with one key -> one event row, one event_id for everyone."""
    emp = await make_employee()
    key = str(uuid.uuid4())
    results = await asyncio.gather(*[
        client.post("/api/attendance/check", json={"idempotency_key": key, "source": "web"}, headers=headers_for(emp))
        for _ in range(20)
    ])
    assert sorted(r.status_code for r in results) == [200] * 19 + [201]
    assert len({r.json()["event_id"] for r in results}) == 1
    assert sum(1 for r in results if r.json()["duplicate"] is False) == 1
    evs = await _events()
    assert len(evs) == 1 and evs[0].id == results[0].json()["event_id"]
    assert len(await _daily(emp.id)) == 1


async def test_concurrent_checks_keep_daily_consistent(client, make_employee, headers_for):
    """Different keys at once: the daily row must cover min and max of all committed events."""
    emp = await make_employee()
    results = await asyncio.gather(*[
        client.post("/api/attendance/check", json={"idempotency_key": str(uuid.uuid4())}, headers=headers_for(emp))
        for _ in range(12)
    ])
    assert all(r.status_code == 201 for r in results)
    times = sorted(e.event_time for e in await _events())
    assert len(times) == 12
    (daily,) = await _daily(emp.id)
    assert daily.first_in == times[0]
    assert daily.last_out == (times[-1] if times[-1] > times[0] else None)


async def test_attendance_me_filters_and_pagination(client, make_employee, headers_for):
    emp, other = await make_employee(), await make_employee()
    async with SessionLocal() as s:
        for i in range(5):
            s.add(AttendanceDaily(employee_id=emp.id, work_date=date(2025, 3, 1 + i), worked_minutes=480))
        s.add(AttendanceDaily(employee_id=other.id, work_date=date(2025, 3, 1), worked_minutes=1))
        await s.commit()
    h = headers_for(emp)
    body = (await client.get("/api/attendance/me?size=2", headers=h)).json()
    assert body["total"] == 5 and len(body["items"]) == 2
    assert body["items"][0]["work_date"] == "2025-03-05"  # newest first
    body = (await client.get("/api/attendance/me?from=2025-03-02&to=2025-03-03", headers=h)).json()
    assert [i["work_date"] for i in body["items"]] == ["2025-03-03", "2025-03-02"]


async def _night_shift(emp, day: date):
    async with SessionLocal() as s:
        shift = Shift(name="Night", start_time=time(22), end_time=time(6), crosses_midnight=True)
        s.add(shift)
        await s.flush()
        s.add(Schedule(employee_id=emp.id, work_date=day, shift_id=shift.id))
        await s.commit()


async def test_night_shift_counts_for_start_day(make_employee):
    emp = await make_employee()
    day = date(2025, 3, 10)
    await _night_shift(emp, day)
    async with SessionLocal() as s:
        await service.check(s, emp.id, uuid.uuid4(), "web", now=datetime(2025, 3, 10, 22, 5, tzinfo=VN))
    async with SessionLocal() as s:
        await service.check(s, emp.id, uuid.uuid4(), "web", now=datetime(2025, 3, 11, 6, 10, tzinfo=VN))
    rows = await _daily(emp.id)
    assert [r.work_date for r in rows] == [day]  # nothing lands on 2025-03-11
    # 22:05 -> 06:10 is 485 min, minus the 60 min unpaid break
    assert (rows[0].worked_minutes, rows[0].ot_minutes) == (425, 0)
    assert rows[0].first_in == datetime(2025, 3, 10, 22, 5, tzinfo=VN)
    assert rows[0].last_out == datetime(2025, 3, 11, 6, 10, tzinfo=VN)


async def test_rebuild_daily_matches_incremental_upsert(make_employee):
    emp = await make_employee()
    day = date(2025, 3, 10)
    await _night_shift(emp, day)
    # events inserted out of order, plus a normal day without a night shift
    stamps = [
        datetime(2025, 3, 11, 6, 10, tzinfo=VN), datetime(2025, 3, 10, 22, 5, tzinfo=VN),
        datetime(2025, 3, 12, 6, 30, tzinfo=VN), datetime(2025, 3, 12, 17, 0, tzinfo=VN),
    ]
    for t in stamps:
        async with SessionLocal() as s:
            await service.check(s, emp.id, uuid.uuid4(), "web", now=t)
    incremental = [(r.work_date, r.first_in, r.last_out, r.worked_minutes, r.ot_minutes) for r in await _daily(emp.id)]
    assert [r[0] for r in incremental] == [day, date(2025, 3, 12)]

    async with SessionLocal() as s:
        for d in (day, date(2025, 3, 11), date(2025, 3, 12)):
            await service.rebuild_daily(s, emp.id, d)
    rebuilt = [(r.work_date, r.first_in, r.last_out, r.worked_minutes, r.ot_minutes) for r in await _daily(emp.id)]
    assert rebuilt == incremental
    # 06:30 -> 17:00 = 630 min, minus 60 break = 570 -> 90 OT
    assert incremental[1][3:] == (570, 90)


async def test_single_event_has_no_last_out(make_employee):
    emp = await make_employee()
    async with SessionLocal() as s:
        await service.check(s, emp.id, uuid.uuid4(), "web", now=datetime(2025, 3, 10, 8, 0, tzinfo=VN))
    (row,) = await _daily(emp.id)
    assert (row.last_out, row.worked_minutes, row.ot_minutes) == (None, 0, 0)
