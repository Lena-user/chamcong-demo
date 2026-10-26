import asyncio
import uuid


async def test_live_monitor_shows_recent_checkins(client, make_employee, headers_for):
    hr = await make_employee("hr")
    emps = [await make_employee() for _ in range(3)]
    res = await asyncio.gather(*[
        client.post("/api/attendance/check", json={"idempotency_key": str(uuid.uuid4())}, headers=headers_for(e))
        for e in emps
    ])
    assert all(r.status_code == 201 for r in res)

    r = await client.get("/api/monitor/live?seconds=60&feed=10", headers=headers_for(hr))
    assert r.status_code == 200
    body = r.json()
    assert body["checkins"] == 3
    assert body["active_employees"] == 3
    assert sum(p["events"] for p in body["per_second"]) == 3
    assert {i["code"] for i in body["latest"]} == {e.code for e in emps}


async def test_live_monitor_is_hr_only(client, make_employee, headers_for):
    emp, mgr = await make_employee(), await make_employee("manager")
    assert (await client.get("/api/monitor/live", headers=headers_for(emp))).status_code == 403
    assert (await client.get("/api/monitor/live", headers=headers_for(mgr))).status_code == 403
    assert (await client.get("/api/monitor/live")).status_code == 401
