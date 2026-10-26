import uuid

from sqlalchemy import text

from app.core.db import SessionLocal
from app.modules.attendance import service as attendance_service
from app.modules.requests import service as requests_service


async def test_daily_check_catches_a_corrupted_rollup(client, make_employee, headers_for):
    emp = await make_employee()
    for _ in range(2):
        r = await client.post("/api/attendance/check", json={"idempotency_key": str(uuid.uuid4())},
                              headers=headers_for(emp))
        assert r.status_code == 201
    async with SessionLocal() as s:
        clean = await attendance_service.check_integrity(s, None)
        assert clean["daily"].checked == 1 and clean["daily"].bad == 0
        assert clean["events"].duplicate_keys == 0 and clean["events"].guard_triggers >= 1
        await s.execute(text("UPDATE attendance_daily SET worked_minutes = worked_minutes + 1 WHERE employee_id = :e"),
                        {"e": emp.id})
        await s.commit()
        broken = await attendance_service.check_integrity(s, None)
    assert broken["daily"].bad == 1


async def test_request_rules_catch_self_approval(make_employee, make_request):
    emp = await make_employee()
    req = await make_request(emp)
    async with SessionLocal() as s:
        assert (await requests_service.check_rules(s)).self_decided == 0
        await s.execute(text("UPDATE requests SET status = 'approved', approved_by = employee_id, "
                             "decided_at = now() WHERE id = :id"), {"id": req.id})
        await s.commit()
        assert (await requests_service.check_rules(s)).self_decided == 1
