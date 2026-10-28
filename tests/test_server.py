import pytest
from fastapi.testclient import TestClient

import server

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def fake_upstream(monkeypatch):
    """No network: stub YouTube oEmbed and Supadata, reset limiter state between tests."""
    calls = []
    monkeypatch.setattr(server, "get_video_title", lambda url: calls.append(url) or "Title")
    monkeypatch.setattr(server, "get_transcript", lambda video_id: "line one\nline two")
    monkeypatch.setattr(server, "_SYSTEM_PROMPT", "PROMPT")
    server._last_call.clear()
    server._day.update(date="", count=0)
    return calls


@pytest.fixture
def client():
    return TestClient(server.app)


def post(client, url=VIDEO, ip="1.1.1.1", **headers):
    return client.post("/api/transcript", json={"url": url}, headers={"cf-connecting-ip": ip, **headers})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_page_served(client):
    assert client.get("/").status_code == 200


def test_api_docs_hidden(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_transcript_ok(client, fake_upstream):
    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Title"
    assert body["transcript"] == "line one\nline two"
    assert body["clipboard_text"] == "PROMPT\n\nline one\nline two"
    assert fake_upstream == [VIDEO]


@pytest.mark.parametrize("url", [
    "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
])
def test_youtube_variants_are_canonicalised(client, fake_upstream, url):
    assert post(client, url).status_code == 200
    assert fake_upstream == [VIDEO]


@pytest.mark.parametrize("url", [
    "https://evil.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
    "javascript:alert(1)//youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=short",
    "https://youtu.be/" + "a" * 400,
])
def test_rejects_non_youtube(client, url):
    r = post(client, url)
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str)


def test_rejected_url_does_not_use_the_rate_limit(client):
    post(client, "https://evil.com/?v=dQw4w9WgXcQ")
    assert post(client).status_code == 200


def test_one_request_per_15_seconds_per_ip(client):
    assert post(client, ip="1.1.1.1").status_code == 200
    r = post(client, ip="1.1.1.1")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) <= 15
    assert post(client, ip="2.2.2.2").status_code == 200


def test_rate_limit_window_expires():
    server.check_rate_limit("3.3.3.3", now=1000.0)
    with pytest.raises(server.HTTPException):
        server.check_rate_limit("3.3.3.3", now=1014.0)
    server.check_rate_limit("3.3.3.3", now=1015.0)


def test_daily_cap(client, monkeypatch):
    monkeypatch.setattr(server, "_PER_DAY", 2)
    assert post(client, ip="1.0.0.1").status_code == 200
    assert post(client, ip="1.0.0.2").status_code == 200
    r = post(client, ip="1.0.0.3")
    assert r.status_code == 429
    assert "Daily" in r.json()["detail"]


def test_spoofed_forwarded_for_does_not_bypass_limit(client):
    h = {"x-forwarded-for": "6.6.6.6, 10.0.0.1"}
    assert client.post("/api/transcript", json={"url": VIDEO}, headers=h).status_code == 200
    h = {"x-forwarded-for": "7.7.7.7, 10.0.0.1"}
    assert client.post("/api/transcript", json={"url": VIDEO}, headers=h).status_code == 429


def test_cors_blocks_foreign_origin(client):
    r = client.options("/api/transcript", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_upstream_error_is_generic(client, monkeypatch):
    def boom(video_id):
        raise RuntimeError("secret upstream detail https://api.supadata.ai/?key=abc")
    monkeypatch.setattr(server, "get_transcript", boom)
    r = post(client)
    assert r.status_code == 502
    assert "supadata" not in r.json()["detail"].lower()


def test_no_files_written(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_BASE", tmp_path)
    post(client)
    assert list(tmp_path.iterdir()) == []
