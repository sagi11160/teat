from sqlalchemy import select

from meetbridge.db import session_scope
from meetbridge.models import AuthSession, User

from .conftest import CSRF, make_settings, register


def test_register_login_logout_flow(client):
    user = register(client)
    assert user["email"] == "owner@example.com"
    assert client.get("/api/auth/me").json()["display_name"] == "Owner"

    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/api/auth/me").status_code == 401

    res = client.post("/api/auth/login", json={"email": "OWNER@example.com ", "password": "correct horse battery"}, headers=CSRF)
    assert res.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_password_is_hashed_and_session_token_not_stored_raw(client):
    register(client)
    token = client.cookies.get("mb_session")
    with session_scope() as db:
        user = db.scalar(select(User))
        assert user.password_hash.startswith("$argon2")
        assert "correct horse battery" not in user.password_hash
        stored = db.scalar(select(AuthSession))
        assert stored.token_hash != token
        assert token not in stored.token_hash


def test_session_cookie_flags(client):
    res = client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "display_name": "A", "password": "correct horse battery"},
        headers=CSRF,
    )
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_logout_revokes_session_server_side(client):
    register(client)
    token = client.cookies.get("mb_session")
    client.post("/api/auth/logout", headers=CSRF)
    client.cookies.set("mb_session", token)
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_unknown_user(client):
    register(client)
    client.cookies.clear()
    bad = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "nope-nope-nope"}, headers=CSRF)
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_credentials"
    unknown = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "whatever123"}, headers=CSRF)
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "invalid_credentials"


def test_login_rate_limit(client):
    register(client)
    client.cookies.clear()
    for _ in range(8):
        client.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong-password"}, headers=CSRF)
    res = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "correct horse battery"}, headers=CSRF)
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "too_many_attempts"


def test_registration_validation(client):
    res = client.post("/api/auth/register", json={"email": "bad", "display_name": "X", "password": "short"}, headers=CSRF)
    assert res.status_code == 422
    fields = {f["field"] for f in res.json()["error"]["fields"]}
    assert {"email", "password"} <= fields


def test_duplicate_email(client):
    register(client)
    client.cookies.clear()
    res = client.post(
        "/api/auth/register",
        json={"email": "Owner@Example.com", "display_name": "Dup", "password": "correct horse battery"},
        headers=CSRF,
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "email_taken"


def test_csrf_header_required_for_mutations(client):
    res = client.post("/api/auth/register", json={"email": "a@example.com", "display_name": "A", "password": "correct horse battery"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "csrf_header_missing"


def test_registration_can_be_closed(tmp_path, runner):
    from fastapi.testclient import TestClient

    from meetbridge.main import create_app

    app = create_app(make_settings(tmp_path, allow_registration=False), runner_factory=lambda *_: runner)
    with TestClient(app) as c:
        res = c.post("/api/auth/register", json={"email": "a@example.com", "display_name": "A", "password": "correct horse battery"}, headers=CSRF)
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "registration_closed"
