import pytest

from tests.conftest import TEST_PASSWORD


async def test_login_ok(client, make_employee):
    emp = await make_employee("hr")
    r = await client.post("/api/auth/login", json={"code": emp.code, "password": TEST_PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["employee"]["code"] == emp.code
    assert body["employee"]["role"] == "hr"
    assert "password_hash" not in body["employee"]


@pytest.mark.parametrize("password", ["wrong-password", ""])
async def test_login_wrong_password(client, make_employee, password):
    emp = await make_employee()
    r = await client.post("/api/auth/login", json={"code": emp.code, "password": password})
    assert r.status_code == 401


async def test_login_unknown_code(client):
    r = await client.post("/api/auth/login", json={"code": "NOPE", "password": "x"})
    assert r.status_code == 401


async def test_me_with_token(client, make_employee, login_token, auth_headers):
    emp = await make_employee("manager")
    token = await login_token(emp)
    r = await client.get("/api/me", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == emp.id
    assert body["code"] == emp.code
    assert body["version"] == 1


async def test_me_without_token(client):
    assert (await client.get("/api/me")).status_code == 401


async def test_me_bad_token(client):
    r = await client.get("/api/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


async def test_untrusted_host_rejected(client):
    r = await client.get("/health", headers={"Host": "evil.com"})
    assert r.status_code == 400
    r = await client.get("/health", headers={"Host": "abc.trycloudflare.com"})
    assert r.status_code == 200
