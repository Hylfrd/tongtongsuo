import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth as auth
from app.admin_password import create_password_hash, parse_password_hash, verify_password
from app.school_sso import SchoolIdentity
from test_request_limits import call_asgi, status

ORIGIN = "https://bnbu.hmcl-helper.cn"
HEADERS = {"Origin": ORIGIN}
ENDPOINT = "/api/auth/admin/password"
OLD = "fixture-previous-password"
NEW = "admin"


@pytest.fixture(scope="module")
def old_hash():
    return create_password_hash(OLD)


@pytest.fixture
def setup(monkeypatch, tmp_path, old_hash):
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", old_hash)

    async def school(*args):
        return SchoolIdentity("Student", "001")

    path = tmp_path / "inventory.sqlite3"
    manager = auth.AuthManager(school, db_path=path)
    app = FastAPI()
    app.include_router(manager.router)
    return app, manager, path, school


def client_for(app):
    return TestClient(app, base_url=ORIGIN)


def login(client, password=OLD, username="admin"):
    return client.post("/api/auth/login", headers=HEADERS, json={"username": username, "password": password})


def change(client, current=OLD, new=NEW, confirm=NEW):
    return client.post(ENDPOINT, headers=HEADERS, json={"current_password": current, "new_password": new, "confirm_password": confirm})


def test_rotation_revokes_all_admin_sessions_preserves_students_and_survives_restart(setup):
    app, manager, path, school = setup
    first, second, student = (client_for(app) for _ in range(3))
    assert login(first).status_code == login(second).status_code == 200
    assert login(student, "student-test", "student").status_code == 200
    old_cookie = second.cookies.get(auth.COOKIE)
    response = change(first)
    assert response.status_code == 204
    assert response.content == b""
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    assert first.get("/api/auth/me").status_code == second.get("/api/auth/me").status_code == 401
    assert student.get("/api/auth/me").json()["user"]["school_code"] == "001"
    assert second.get("/api/auth/me", headers={"Cookie": f"{auth.COOKIE}={old_cookie}"}).status_code == 401
    assert login(first, OLD).status_code == 401
    assert login(first, NEW).status_code == 200
    restarted = FastAPI()
    restarted.include_router(auth.AuthManager(school, db_path=path).router)
    fresh = client_for(restarted)
    assert login(fresh, OLD).status_code == 401
    assert login(fresh, NEW).status_code == 200
    encoded, _ = manager._credentials.snapshot()
    assert encoded != NEW and verify_password(NEW, parse_password_hash(encoded))


def test_authorization_origin_and_confirmation_are_checked_without_secret_echo(setup):
    app, _, _, _ = setup
    client = client_for(app)
    assert change(client).status_code == 401
    assert login(client, "test", "student").status_code == 200
    assert change(client).status_code == 403
    assert login(client).status_code == 200
    payload = {"current_password": OLD, "new_password": "sensitive-new", "confirm_password": "different-secret"}
    for headers in ({}, {"Origin": ORIGIN + ".evil.example"}):
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code == 403
    response = client.post(ENDPOINT, headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PASSWORD_MISMATCH"
    for bad in ({**payload, "new_password": {"private": "nested-secret"}}, {**payload, "new_password": "x" * 257}, {**payload, "extra": "secret-field"}):
        result = client.post(ENDPOINT, headers=HEADERS, json=bad)
        assert result.status_code == 422
        assert not any(secret in result.text for secret in (OLD, "sensitive-new", "different-secret", "nested-secret", "secret-field"))
    assert client.get("/api/auth/me").status_code == 200
    assert change(client, new="four", confirm="four").status_code == 422
    assert client.post(ENDPOINT, headers=HEADERS, content="secret").status_code == 415
    assert client.post(ENDPOINT, headers=HEADERS, json={"private": "x" * 5000}).status_code == 413


def test_wrong_current_password_shares_admin_login_cooldown(setup, monkeypatch):
    app, _, _, _ = setup
    now = [1000.0]
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: now[0]))
    client = client_for(app)
    assert login(client).status_code == 200
    for attempt in range(3):
        response = change(client, current="incorrect-sensitive")
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "CURRENT_PASSWORD_INVALID"
        assert "incorrect-sensitive" not in response.text
        if attempt == 2:
            assert response.headers["retry-after"] == "30"
    assert client.get("/api/auth/me").status_code == 200
    assert change(client).status_code == 429
    assert login(client_for(app), OLD).status_code == 429
    now[0] += 30
    assert change(client).status_code == 204


def test_password_body_has_total_read_timeout(setup, monkeypatch):
    app, _, _, _ = setup
    client = client_for(app)
    assert login(client).status_code == 200
    timeout = asyncio.timeout
    monkeypatch.setattr(auth.asyncio, "timeout", lambda seconds: timeout(0.01))
    cookie = client.cookies.get(auth.COOKIE)
    messages, _ = asyncio.run(call_asgi(app, [b'{"current_password":"secret"', b'}'], path=ENDPOINT,
                                      headers=[(b"origin", ORIGIN.encode()), (b"cookie", f"{auth.COOKIE}={cookie}".encode())], delay=0.03))
    assert status(messages) == 408
    assert b"secret" not in b"".join(message.get("body", b"") for message in messages)


def test_credential_revision_blocks_inflight_old_password_login(setup, monkeypatch):
    app, manager, _, _ = setup
    original_thread = auth.asyncio.to_thread
    replacement = create_password_hash(NEW)

    async def scenario():
        verifying, release = asyncio.Event(), asyncio.Event()

        async def controlled(function, *args, **kwargs):
            if function is verify_password:
                verifying.set()
                await release.wait()
            return await original_thread(function, *args, **kwargs)

        monkeypatch.setattr(auth.asyncio, "to_thread", controlled)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            pending = asyncio.create_task(client.post("/api/auth/login", headers=HEADERS, json={"username": "admin", "password": OLD}))
            await verifying.wait()
            _, revision = manager._credentials.snapshot()
            assert manager._credentials.replace(revision, replacement)
            release.set()
            response = await pending
            assert response.status_code == 401
            assert response.json()["detail"]["code"] == "ADMIN_PASSWORD_CHANGED"
            assert "set-cookie" not in response.headers
            assert (await client.get("/api/auth/me")).status_code == 401

    asyncio.run(scenario())
