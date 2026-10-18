from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

import app.auth as auth
from app.school_sso import SchoolAuthError, SchoolIdentity
from app.admin_password import create_password_hash

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
    for index in range(3):
        response = client.post("/api/auth/login", headers=HEADERS, json={"username": "demo", "password": "test"})
        assert response.status_code == 401
        if index == 2:
            assert response.headers["retry-after"] == "30"
    response = client.post("/api/auth/login", headers=HEADERS, json={"username": "demo", "password": "test"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.json()["detail"]["code"] == "LOGIN_COOLDOWN"
    assert len(calls) == 3
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


@pytest.fixture
def admin_hash():
    return create_password_hash("fixture-admin-password")


def make_protected_client(monkeypatch, verifier):
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    manager = auth.AuthManager(verifier)
    app = FastAPI()
    app.include_router(manager.router)

    @app.get("/admin-test")
    def read(identity=Depends(manager.require_admin)):
        return identity

    @app.post("/admin-test")
    def write(identity=Depends(manager.require_admin)):
        return {"accepted": identity["role"] == "admin"}

    return TestClient(app, base_url=ORIGIN)


def test_admin_uses_local_hash_restores_session_and_logout_revokes(monkeypatch, admin_hash):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_hash)
    monkeypatch.setenv("ADMIN_USERNAME", "cabinet-admin")

    async def never_school(*args):
        pytest.fail("Administrator credentials must never reach school")

    client = make_protected_client(monkeypatch, never_school)
    response = client.get("/admin-test")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    response = client.post("/api/auth/login", headers=HEADERS,
                           json={"username": " Cabinet-Admin ", "password": "fixture-admin-password"})
    expected = {"name": "管理员", "school_code": None, "role": "admin"}
    assert response.status_code == 200
    assert response.json() == {"user": expected}
    assert client.get("/api/auth/me").json() == {"user": expected}
    response = client.get("/admin-test")
    assert response.json() == expected
    assert response.headers["cache-control"] == "no-store"
    token = client.cookies.get(auth.COOKIE)
    assert client.post("/api/auth/logout", headers=HEADERS).status_code == 204
    assert client.get("/admin-test", headers={"Cookie": f"{auth.COOKIE}={token}"}).status_code == 401


@pytest.mark.parametrize("encoded", ["", "plaintext-password", "pbkdf2_sha256$1$abcd$abcd",
                                     "pbkdf2_sha256$600000$" + "a" * 32 + "$" + "z" * 64])
def test_unconfigured_or_malformed_admin_hash_never_falls_back_to_school(monkeypatch, encoded):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", encoded)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)

    async def never_school(*args):
        pytest.fail("Administrator credentials must never reach school")

    client = make_protected_client(monkeypatch, never_school)
    response = client.post("/api/auth/login", headers=HEADERS,
                           json={"username": "admin", "password": "private-password"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ADMIN_NOT_CONFIGURED"
    assert "private-password" not in response.text
    assert client.get("/admin-test").status_code == 401


def test_wrong_admin_password_fails_without_school_call(monkeypatch, admin_hash):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_hash)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)

    async def never_school(*args):
        pytest.fail("Administrator credentials must never reach school")

    client = make_protected_client(monkeypatch, never_school)
    response = client.post("/api/auth/login", headers=HEADERS,
                           json={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert client.get("/admin-test").status_code == 401


def test_student_cannot_claim_admin_role_or_access_admin_routes(monkeypatch, admin_hash):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_hash)

    async def school(*args):
        return SchoolIdentity(name="Example Student", school_code="DEMO-001")

    client = make_protected_client(monkeypatch, school)
    response = client.post("/api/auth/login", headers=HEADERS,
                           json={"username": "demo", "password": "test", "role": "admin"})
    assert response.status_code == 200
    assert "role" not in response.json()["user"]
    for response in (client.get("/admin-test"), client.post("/admin-test", headers=HEADERS)):
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "ADMIN_REQUIRED"
        assert response.headers["cache-control"] == "no-store"


def test_admin_mutations_require_exact_origin(monkeypatch, admin_hash):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_hash)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)

    async def never_school(*args):
        pytest.fail("Administrator credentials must never reach school")

    client = make_protected_client(monkeypatch, never_school)
    client.post("/api/auth/login", headers=HEADERS,
                json={"username": "admin", "password": "fixture-admin-password"})
    for headers in ({}, {"Origin": "https://unrelated.example"}, {"Origin": ORIGIN + ".evil.example"}):
        response = client.post("/admin-test", headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "ORIGIN_NOT_ALLOWED"
    response = client.post("/admin-test", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("account", ["demo", "admin"])
def test_failure_cooldown_progresses_and_survives_new_browser(monkeypatch, admin_hash, account):
    now = [1000.0]
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", admin_hash)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    calls = []

    async def rejected(*args):
        calls.append(True)
        raise SchoolAuthError("SCHOOL_CREDENTIALS_REJECTED", "学校未接受此账号或密码。", 401)

    client = make_client(monkeypatch, rejected)
    payload = {"username": account, "password": "wrong-password"}
    for _ in range(2):
        response = client.post("/api/auth/login", headers=HEADERS, json=payload)
        assert response.status_code == 401
        assert "retry-after" not in response.headers
    for cooldown in (30, 60, 300, 300):
        response = client.post("/api/auth/login", headers=HEADERS, json=payload)
        assert response.status_code == 401
        assert response.headers["retry-after"] == str(cooldown)
        before = len(calls)
        allowed_at = now[0] + cooldown
        now[0] += 10.2
        # A fresh browser and account casing cannot reset server-side state.
        new_browser = TestClient(client.app, base_url=ORIGIN)
        response = new_browser.post("/api/auth/login", headers=HEADERS,
                                    json={**payload, "username": account.upper()})
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "LOGIN_COOLDOWN"
        assert response.headers["retry-after"] == str(cooldown - 10)
        assert f"{cooldown - 10} 秒" in response.json()["detail"]["message"]
        assert response.headers["cache-control"] == "no-store"
        assert len(calls) == before
        now[0] = allowed_at


@pytest.mark.parametrize("reset", ["success", "ten_minutes"])
def test_cooldown_resets_after_success_or_ten_minutes_without_failures(monkeypatch, reset):
    now = [1000.0]
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def verify(username, password):
        if password == "correct":
            return SchoolIdentity(name="Example Student", school_code="DEMO-001")
        raise SchoolAuthError("SCHOOL_CREDENTIALS_REJECTED", "账号或密码错误。", 401)

    client = make_client(monkeypatch, verify)
    payload = {"username": "demo", "password": "wrong"}
    for _ in range(3):
        response = client.post("/api/auth/login", headers=HEADERS, json=payload)
    assert response.headers["retry-after"] == "30"
    if reset == "success":
        now[0] += 30
        assert client.post("/api/auth/login", headers=HEADERS,
                           json={**payload, "password": "correct"}).status_code == 200
    else:
        now[0] += 600
    for _ in range(2):
        response = client.post("/api/auth/login", headers=HEADERS, json=payload)
        assert response.status_code == 401
        assert "retry-after" not in response.headers
    response = client.post("/api/auth/login", headers=HEADERS, json=payload)
    assert response.status_code == 401
    assert response.headers["retry-after"] == "30"


def test_upstream_outages_and_unconfigured_admin_do_not_trigger_account_cooldown(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)

    async def unavailable(*args):
        raise SchoolAuthError("SCHOOL_UNAVAILABLE", "学校暂时无法连接。", 503)

    client = make_client(monkeypatch, unavailable)
    for username in ("demo", "admin"):
        for _ in range(6):
            response = client.post("/api/auth/login", headers=HEADERS,
                                   json={"username": username, "password": "test"})
            assert response.status_code == 503
            assert "retry-after" not in response.headers


def test_ip_flood_limit_still_applies_across_distinct_accounts(monkeypatch):
    calls = []

    async def unavailable(*args):
        calls.append(True)
        raise SchoolAuthError("SCHOOL_UNAVAILABLE", "学校暂时无法连接。", 503)

    client = make_client(monkeypatch, unavailable)
    for index in range(30):
        response = client.post("/api/auth/login", headers=HEADERS,
                               json={"username": f"account{index}", "password": "test"})
        assert response.status_code == 503
    response = client.post("/api/auth/login", headers=HEADERS,
                           json={"username": "another", "password": "test"})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "LOGIN_RATE_LIMITED"
    assert response.headers["retry-after"] == "300"
    assert len(calls) == 30
