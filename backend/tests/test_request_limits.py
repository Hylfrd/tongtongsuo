import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import create_admin_router
from app.admin_password import create_password_hash
from app.auth import AuthManager
from app.request_limits import AdminBodyLimitMiddleware


def protected_app(tmp_path):
    manager = AuthManager()
    app = FastAPI()
    app.include_router(manager.router)
    app.include_router(create_admin_router(manager.require_admin, tmp_path / "bounded.sqlite3"))
    return app


async def call_asgi(app, chunks, *, headers=(), path="/api/admin/cabinets", method="POST", delay=0):
    sent = []
    reads = 0

    async def receive():
        nonlocal reads
        if delay:
            await asyncio.sleep(delay)
        reads += 1
        if not chunks:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"content-type", b"application/json"), *headers], "client": ("127.0.0.1", 123), "server": ("testserver", 80)}
    await app(scope, receive, send)
    return sent, reads


def status(messages):
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


@pytest.mark.parametrize("length,expected", [(b"800001", 413), (b"-1", 400), (b"bad-private-value", 400), (b"1.5", 400), (b"", 400)])
def test_rejects_unauthenticated_content_length_before_read(tmp_path, length, expected):
    app = AdminBodyLimitMiddleware(protected_app(tmp_path))
    messages, reads = asyncio.run(call_asgi(app, [b"not-read"], headers=[(b"content-length", length)]))
    assert status(messages) == expected
    assert reads == 0
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    assert b"bad-private-value" not in b"".join(item.get("body", b"") for item in messages)


def test_rejects_actual_unauthenticated_chunked_bytes_and_stops_reading(tmp_path):
    app = AdminBodyLimitMiddleware(protected_app(tmp_path))
    chunks = [b"x" * 400000, b"x" * 400001, b"never-read"]
    messages, reads = asyncio.run(call_asgi(app, chunks, headers=[(b"transfer-encoding", b"chunked")]))
    assert status(messages) == 413
    assert reads == 2
    assert chunks == [b"never-read"]


def test_actual_size_enforced_when_content_length_lies(tmp_path):
    app = AdminBodyLimitMiddleware(protected_app(tmp_path), max_bytes=20)
    messages, reads = asyncio.run(call_asgi(app, [b"x" * 21], headers=[(b"content-length", b"1")]))
    assert status(messages) == 413
    assert reads == 1


@pytest.mark.parametrize("headers", [
    [(b"content-length", b"2"), (b"content-length", b"2")],
    [(b"content-length", b"2"), (b"transfer-encoding", b"chunked")],
])
def test_ambiguous_framing_rejected_without_body_read(tmp_path, headers):
    app = AdminBodyLimitMiddleware(protected_app(tmp_path))
    messages, reads = asyncio.run(call_asgi(app, [b"{}"], headers=headers))
    assert status(messages) == 400
    assert reads == 0


def test_mismatching_length_rejected_after_bounded_read(tmp_path):
    app = AdminBodyLimitMiddleware(protected_app(tmp_path))
    messages, reads = asyncio.run(call_asgi(app, [b"{}"], headers=[(b"content-length", b"1")]))
    assert status(messages) == 400
    assert reads == 1


def test_chunked_small_body_preserves_authentication(tmp_path):
    app = AdminBodyLimitMiddleware(protected_app(tmp_path))
    messages, reads = asyncio.run(call_asgi(app, [b'{"code":"A",', b'"name":"Box"}']))
    assert status(messages) == 401
    assert reads == 2


def test_body_timeout_is_total_and_does_not_cover_handler(tmp_path):
    app = AdminBodyLimitMiddleware(protected_app(tmp_path), body_timeout=0.15)
    messages, reads = asyncio.run(call_asgi(app, [b"{", b"}", b" "], delay=0.07))
    assert status(messages) == 408
    assert reads < 3

    async def slow_handler(scope, receive, send):
        assert (await receive())["body"] == b"{}"
        await asyncio.sleep(0.03)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    messages, _ = asyncio.run(call_asgi(AdminBodyLimitMiddleware(slow_handler, body_timeout=0.01), [b"{}"]))
    assert status(messages) == 204


def test_small_authenticated_request_replays_body_and_keeps_origin_check(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", create_password_hash("fixture-admin-password"))
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://example.test")
    app = protected_app(tmp_path)
    app.add_middleware(AdminBodyLimitMiddleware)
    client = TestClient(app, base_url="https://example.test")
    response = client.post("/api/auth/login", json={"username": "admin", "password": "fixture-admin-password"}, headers={"Origin": "https://example.test"})
    assert response.status_code == 200
    payload = {"code": "C", "name": "一号柜"}
    assert client.post("/api/admin/cabinets", json=payload).status_code == 403
    response = client.post("/api/admin/cabinets", content=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Origin": "https://example.test"})
    assert response.status_code == 201
    assert response.json()["name"] == "一号柜"
