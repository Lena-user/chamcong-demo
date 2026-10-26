async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "redis": "ok"}


async def test_index_cache_headers(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
