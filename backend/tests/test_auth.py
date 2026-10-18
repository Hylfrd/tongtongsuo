from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth as auth
from app.school_sso import SchoolAuthError, SchoolIdentity

ORIGIN = "https://bnbu.hmcl-helper.cn"
HEADERS = {"Origin": ORIGIN}


def make_client(monkeypatch, verifier):
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    app = FastAPI()
    app.include_router(auth.create_auth_router(verifier))
    return TestClient(app, base_url=ORIGIN)


def test_verified_identity_session_and_logout(monkeypatch):
    async def verify(username, password):
        assert (username, password) == ("demo", "test-password")
        return SchoolIdentity(name="Example Student", school_code="DEMO-001")

    client = make_client(monkeypatch, verify)
    assert client.get("/api/auth/me").status_code == 401
    response = client.post(
        "/api/auth/login", headers=HEADERS,
        json={"username": " demo ", "password": "test-password", "name": "Forged"},
    )
    expected = {"user": {"name": "Example Student", "school_code": "DEMO-001"}}
    assert response.status_code == 200
    assert response.json() == expected
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "test-password" not in cookie
    assert client.get("/api/auth/me").json() == expected
    previous_cookie = client.cookies.get(auth.COOKIE)
    assert client.post("/api/auth/logout", headers=HEADERS).status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Cookie": f"{auth.COOKIE}={previous_cookie}"}).status_code == 401


def test_rejects_cross_origin_and_malformed_inputs_without_school_request(monkeypatch):
    calls = []

    async def verify(*args):
        calls.append(True)
        raise AssertionError("School must not be called")

    client = make_client(monkeypatch, verify)
    payload = {"username": "demo", "password": "sensitive-value"}
    for headers in ({}, {"Origin": "https://unrelated.example"}):
        assert client.post("/api/auth/login", headers=headers, json=payload).status_code == 403
        assert client.post("/api/auth/logout", headers=headers).status_code == 403
    response = client.post("/api/auth/login", headers=HEADERS, json={**payload, "password": "sensitive-value" * 100})
    assert response.status_code == 422
    assert "sensitive-value" not in response.text
    response = client.post("/api/auth/login", headers=HEADERS, json={**payload, "extra": "x" * 5000})
    assert response.status_code == 413
    assert client.post("/api/auth/login", headers=HEADERS, json=[]).status_code == 422
    assert not calls


def test_failed_logins_are_bounded_and_do_not_create_sessions(monkeypatch):
    calls = []

    async def verify(*args):
        calls.append(True)
        raise SchoolAuthError("INVALID_CREDENTIALS", "账号或密码不正确。", 401)

    client = make_client(monkeypatch, verify)
    for _ in range(5):
        assert client.post("/api/auth/login", headers=HEADERS, json={"username": "demo", "password": "test"}).status_code == 401
    response = client.post("/api/auth/login", headers=HEADERS, json={"username": "demo", "password": "test"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    assert len(calls) == 5
    assert client.get("/api/auth/me").status_code == 401


def test_expiry_and_process_restart_invalidate_sessions(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def verify(*args):
        return SchoolIdentity(name="Example Student", school_code=None)

    client = make_client(monkeypatch, verify)
    client.post("/api/auth/login", headers=HEADERS, json={"username": "demo", "password": "test"})
    cookie = client.cookies.get(auth.COOKIE)
    restarted = make_client(monkeypatch, verify)
    assert restarted.get("/api/auth/me", headers={"Cookie": f"{auth.COOKIE}={cookie}"}).status_code == 401
    now[0] += auth.SESSION_SECONDS + 1
    assert client.get("/api/auth/me").status_code == 401


def test_unexpected_upstream_errors_are_redacted(monkeypatch):
    async def verify(*args):
        raise RuntimeError("password=secret; ssoToken=private-school-session")

    client = make_client(monkeypatch, verify)
    response = client.post("/api/auth/login", headers=HEADERS, json={"username": "demo", "password": "test"})
    assert response.status_code == 503
    assert "secret" not in response.text and "ssoToken" not in response.text
    assert "set-cookie" not in response.headers
