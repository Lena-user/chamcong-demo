import asyncio
from datetime import date

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.modules.attendance.models import AttendanceEvent
from app.modules.requests.models import Request

OT_BODY = {
    "type": "ot", "work_date": "2025-03-10", "reason": "load",
    "start_at": "2025-03-10T18:00:00+07:00", "end_at": "2025-03-10T20:00:00+07:00",
}


async def _get(req_id):
    async with SessionLocal() as s:
        return await s.get(Request, req_id, populate_existing=True)


async def test_create_and_list_mine(client, make_employee, headers_for):
    emp = await make_employee()
    r = await client.post("/api/requests", json=OT_BODY, headers=headers_for(emp))
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["employee_id"] == emp.id
    body = (await client.get("/api/requests/me", headers=headers_for(emp))).json()
    assert body["total"] == 1 and body["items"][0]["type"] == "ot"


async def test_create_validation(client, make_employee, headers_for):
    h = headers_for(await make_employee())
    assert (await client.post("/api/requests", json={**OT_BODY, "type": "bogus"}, headers=h)).status_code == 422
    assert (await client.post("/api/requests", json={**OT_BODY, "end_at": "2025-03-10T10:00:00+07:00"}, headers=h)).status_code == 422
    assert (await client.post("/api/requests", json={**OT_BODY, "start_at": "2025-03-10T18:00:00"}, headers=h)).status_code == 422
    assert (await client.post("/api/requests", json={"type": "ot", "work_date": "2025-03-10"}, headers=h)).status_code == 422
    leave = {"type": "leave", "work_date": "2025-03-10", "reason": "x"}
    assert (await client.post("/api/requests", json=leave, headers=h)).status_code == 201


async def test_nv3_concurrent_approve(client, make_employee, make_request, headers_for):
    """NV-3: 10 approvers at once -> one 200, nine 409, approved_by is the winner."""
    owner = await make_employee()
    managers = [await make_employee("manager") for _ in range(10)]
    req = await make_request(owner)
    results = await asyncio.gather(*[
        client.post(f"/api/requests/{req.id}/approve", headers=headers_for(m)) for m in managers
    ])
    assert sorted(r.status_code for r in results) == [200] + [409] * 9
    winner = managers[[r.status_code for r in results].index(200)]
    row = await _get(req.id)
    assert row.status == "approved" and row.approved_by == winner.id and row.decided_at is not None
    # a later reject cannot overturn it
    assert (await client.post(f"/api/requests/{req.id}/reject", headers=headers_for(managers[0]))).status_code == 409


async def test_reject_and_not_found(client, make_employee, make_request, headers_for):
    owner, mgr = await make_employee(), await make_employee("manager")
    req = await make_request(owner)
    r = await client.post(f"/api/requests/{req.id}/reject", headers=headers_for(mgr))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert (await client.post("/api/requests/99999/approve", headers=headers_for(mgr))).status_code == 404


async def test_rbac_requests(client, make_employee, make_request, headers_for):
    a = await make_employee(department="A")
    b = await make_employee(department="B")
    mgr_a = await make_employee("manager", department="A")
    mgr_b = await make_employee("manager", department="B")
    hr = await make_employee("hr", department="A")
    ra, rb = await make_request(a), await make_request(b)

    # employees cannot use manager endpoints
    assert (await client.get("/api/requests", headers=headers_for(a))).status_code == 403
    assert (await client.post(f"/api/requests/{ra.id}/approve", headers=headers_for(a))).status_code == 403
    # managers only see their own department
    ids = {i["id"] for i in (await client.get("/api/requests?status=pending", headers=headers_for(mgr_a))).json()["items"]}
    assert ids == {ra.id}
    ids = {i["id"] for i in (await client.get("/api/requests", headers=headers_for(mgr_b))).json()["items"]}
    assert ids == {rb.id}
    # ...and cannot decide other departments' requests
    assert (await client.post(f"/api/requests/{rb.id}/approve", headers=headers_for(mgr_a))).status_code == 403
    assert (await _get(rb.id)).status == "pending"
    # hr sees everything and can decide anywhere
    assert (await client.get("/api/requests", headers=headers_for(hr))).json()["total"] == 2
    assert (await client.post(f"/api/requests/{rb.id}/approve", headers=headers_for(hr))).status_code == 200
    # nobody decides their own request
    own = await make_request(mgr_a)
    assert (await client.post(f"/api/requests/{own.id}/approve", headers=headers_for(mgr_a))).status_code == 403
    # status filter is validated
    assert (await client.get("/api/requests?status=bogus", headers=headers_for(hr))).status_code == 422


async def test_approving_adjustment_never_touches_events(client, make_employee, make_request, headers_for):
    """NV-5: an approved adjustment is applied at payroll time, attendance_events stays as it was."""
    owner, mgr = await make_employee(), await make_employee("manager")
    req = await make_request(owner, type_="adjustment")
    async with SessionLocal() as s:
        before = await s.scalar(select(func.count()).select_from(AttendanceEvent))
    assert (await client.post(f"/api/requests/{req.id}/approve", headers=headers_for(mgr))).status_code == 200
    async with SessionLocal() as s:
        after = await s.scalar(select(func.count()).select_from(AttendanceEvent))
    assert before == after == 0


async def test_nv4_closed_period_rejects_create_and_approve(client, make_employee, make_request, make_period, headers_for):
    owner, mgr = await make_employee(), await make_employee("manager")
    for status in ("calculating", "locked", "paid"):
        await make_period(2025, {"calculating": 1, "locked": 2, "paid": 3}[status], status)
    open_period = await make_period(2025, 4, "draft")
    for month in (1, 2, 3):
        body = {**OT_BODY, "work_date": f"2025-0{month}-10",
                "start_at": f"2025-0{month}-10T18:00:00+07:00", "end_at": f"2025-0{month}-10T20:00:00+07:00"}
        assert (await client.post("/api/requests", json=body, headers=headers_for(owner))).status_code == 409
        req = await make_request(owner, date(2025, month, 10))
        assert (await client.post(f"/api/requests/{req.id}/approve", headers=headers_for(mgr))).status_code == 409
        assert (await client.post(f"/api/requests/{req.id}/reject", headers=headers_for(mgr))).status_code == 409
        assert (await _get(req.id)).status == "pending"
    # draft period and months with no period are fine
    assert open_period.status == "draft"
    for month in (4, 5):
        body = {**OT_BODY, "work_date": f"2025-0{month}-10",
                "start_at": f"2025-0{month}-10T18:00:00+07:00", "end_at": f"2025-0{month}-10T20:00:00+07:00"}
        assert (await client.post("/api/requests", json=body, headers=headers_for(owner))).status_code == 201
