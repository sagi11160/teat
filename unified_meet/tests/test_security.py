import logging

from meetbridge.main import redact_path

from .conftest import CSRF, register


def test_security_headers(client):
    res = client.get("/")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"] == "no-referrer"
    csp = res.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_invite_tokens_are_not_logged(client, caplog):
    register(client)
    room = client.post("/api/rooms", json={"title": "Secret room"}, headers=CSRF).json()
    token = room["invite_url"].rsplit("/", 1)[1]
    with caplog.at_level(logging.INFO, logger="meetbridge"):
        client.get(f"/api/invite/{token}")
        client.get(f"/j/{token}")
        client.get(f"/call/{token}")
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "/api/invite/***" in logged and "/j/***" in logged and "/call/***" in logged
    assert token not in logged


def test_redact_path():
    assert redact_path("/ws/call/abcDEF_123") == "/ws/call/***"
    assert redact_path("/api/rooms/abc") == "/api/rooms/abc"


def test_request_id_is_sanitised(client):
    assert client.get("/healthz", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    forged = client.get("/healthz", headers={"X-Request-ID": "x\r\nFAKE LOG LINE"}).headers["x-request-id"]
    assert "FAKE" not in forged and len(forged) == 16


def test_errors_do_not_leak_internals(client):
    res = client.get("/api/rooms/nope", headers=CSRF)
    body = res.json()
    assert set(body["error"]) <= {"code", "message", "request_id", "fields"}
    assert "Traceback" not in res.text


def test_html_in_user_input_is_stored_as_text(client):
    register(client)
    room = client.post("/api/rooms", json={"title": "<img src=x onerror=alert(1)>"}, headers=CSRF).json()
    assert room["title"] == "<img src=x onerror=alert(1)>"  # rendered with textContent in the UI
    token = room["invite_url"].rsplit("/", 1)[1]
    assert client.get(f"/api/invite/{token}").headers["content-type"].startswith("application/json")
