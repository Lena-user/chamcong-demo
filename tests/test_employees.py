import asyncio

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.modules.employees.models import AuditLog, Employee, EmployeeSensitive


async def test_list_requires_manager(client, make_employee, headers_for):
    emp = await make_employee("employee")
    assert (await client.get("/api/employees", headers=headers_for(emp))).status_code == 403
    assert (await client.get("/api/employees")).status_code == 401


async def test_list_pagination_and_department_filter(client, make_employee, headers_for):
    mgr = await make_employee("manager", department="A")
    for _ in range(4):
        await make_employee("employee", department="A")
    for _ in range(2):
        await make_employee("employee", department="B")
    h = headers_for(mgr)

    r = await client.get("/api/employees?size=3&page=2", headers=h)
    body = r.json()
    assert r.status_code == 200
    assert (body["total"], body["page"], body["size"], len(body["items"])) == (7, 2, 3, 3)

    body = (await client.get("/api/employees?department=B", headers=h)).json()
    assert body["total"] == 2 and {i["department"] for i in body["items"]} == {"B"}
    assert "password_hash" not in body["items"][0]

    # size is capped at the configured maximum, default applies when omitted
    assert (await client.get("/api/employees?size=100000", headers=h)).json()["size"] == 100
    assert (await client.get("/api/employees", headers=h)).json()["size"] == 20


async def test_patch_self_and_audit(client, make_employee, headers_for):
    emp = await make_employee()
    r = await client.patch(
        f"/api/employees/{emp.id}", json={"version": 1, "full_name": "New Name"}, headers=headers_for(emp)
    )
    assert r.status_code == 200
    assert r.json()["version"] == 2 and r.json()["full_name"] == "New Name"
    async with SessionLocal() as s:
        logs = (await s.execute(select(AuditLog).where(AuditLog.action == "employee.update"))).scalars().all()
    assert [(a.actor_id, a.target_id) for a in logs] == [(emp.id, emp.id)]


async def test_patch_permissions_and_validation(client, make_employee, headers_for):
    a, b = await make_employee(), await make_employee()
    hr = await make_employee("hr")
    # an employee cannot edit someone else, nor move themselves to another department
    r = await client.patch(f"/api/employees/{b.id}", json={"version": 1, "full_name": "x"}, headers=headers_for(a))
    assert r.status_code == 403
    r = await client.patch(f"/api/employees/{a.id}", json={"version": 1, "department": "Z"}, headers=headers_for(a))
    assert r.status_code == 403
    # hr can edit anyone, including the department
    r = await client.patch(f"/api/employees/{b.id}", json={"version": 1, "department": "Z"}, headers=headers_for(hr))
    assert r.status_code == 200 and r.json()["department"] == "Z"
    # unknown id, empty change, missing version
    assert (await client.patch("/api/employees/99999", json={"version": 1, "full_name": "x"}, headers=headers_for(hr))).status_code == 404
    assert (await client.patch(f"/api/employees/{b.id}", json={"version": 2}, headers=headers_for(hr))).status_code == 422
    assert (await client.patch(f"/api/employees/{b.id}", json={"full_name": "x"}, headers=headers_for(hr))).status_code == 422


async def test_nv2_concurrent_patch_same_version(client, make_employee, headers_for):
    """NV-2: 10 PATCH carrying the same version -> exactly one 200, nine 409, version +1."""
    emp = await make_employee()
    h = headers_for(emp)
    results = await asyncio.gather(*[
        client.patch(f"/api/employees/{emp.id}", json={"version": 1, "full_name": f"Name {i}"}, headers=h)
        for i in range(10)
    ])
    codes = sorted(r.status_code for r in results)
    assert codes == [200] + [409] * 9
    winner = next(r for r in results if r.status_code == 200).json()
    async with SessionLocal() as s:
        row = await s.get(Employee, emp.id, populate_existing=True)
        audits = await s.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "employee.update"))
    assert row.version == 2
    assert row.full_name == winner["full_name"]
    assert audits == 1  # only the winner is audited


async def test_sensitive_hr_only_and_audited(client, make_employee, make_contract, headers_for):
    target = await make_employee()
    await make_contract(target, dependents=2)
    hr, mgr = await make_employee("hr"), await make_employee("manager")
    assert (await client.get(f"/api/employees/{target.id}/sensitive", headers=headers_for(mgr))).status_code == 403
    assert (await client.get(f"/api/employees/{target.id}/sensitive", headers=headers_for(target))).status_code == 403
    r = await client.get(f"/api/employees/{target.id}/sensitive", headers=headers_for(hr))
    assert r.status_code == 200 and r.json()["dependents"] == 2
    assert (await client.get("/api/employees/99999/sensitive", headers=headers_for(hr))).status_code == 404
    async with SessionLocal() as s:
        logs = (await s.execute(select(AuditLog).where(AuditLog.action == "sensitive.view"))).scalars().all()
    assert [(a.actor_id, a.target_id) for a in logs] == [(hr.id, target.id)]
