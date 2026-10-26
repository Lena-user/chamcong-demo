async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "redis": "ok"}


async def test_index_cache_headers(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert '<script src="app.js?v=' in r.text


async def test_assets_long_cache_and_monitor_page(client):
    r = await client.get("/app.js")
    assert r.headers["cache-control"] == "public, max-age=86400"
    m = await client.get("/monitor.html")
    assert m.status_code == 200 and m.headers["cache-control"] == "no-cache"
    assert '<script src="monitor.js?v=' in m.text and 'href="style.css?v=' in m.text

