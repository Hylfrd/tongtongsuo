"""Short-lived project sessions; school passwords and cookies are never retained."""

from collections import deque
from collections.abc import Awaitable, Callable
import asyncio
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.school_sso import SchoolAuthError, SchoolIdentity, authenticate

COOKIE = "tts_session"
SESSION_SECONDS = 1800


def create_auth_router(
    verifier: Callable[[str, str], Awaitable[SchoolIdentity]] = authenticate,
) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    origin = os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:5173").rstrip("/")
    parsed = urlsplit(origin)
    if (parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.path
            or parsed.query or parsed.fragment or parsed.username is not None):
        raise ValueError("PUBLIC_ORIGIN must contain only scheme and host/port")
    sessions: dict[str, tuple[float, SchoolIdentity]] = {}
    attempts: dict[str, deque[float]] = {}
    active = 0

    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            {"detail": {"code": code, "message": message}},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )

    def session_key(request: Request) -> str:
        value = request.cookies.get(COOKIE, "")
        return hashlib.sha256(value.encode()).hexdigest()

    def prune(now: float) -> None:
        for key, (expires, _) in list(sessions.items()):
            if expires <= now:
                del sessions[key]
        for key, values in list(attempts.items()):
            while values and values[0] <= now - 300:
                values.popleft()
            if not values:
                del attempts[key]

    def user_response(identity: SchoolIdentity) -> JSONResponse:
        return JSONResponse(
            {"user": {"name": identity.name, "school_code": identity.school_code}},
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/me")
    async def current_user(request: Request) -> Response:
        prune(time.monotonic())
        session = sessions.get(session_key(request))
        if session is None:
            return error(401, "NO_SESSION", "请先验证学校账号。")
        return user_response(session[1])

    @router.post("/login")
    async def login(request: Request) -> Response:
        nonlocal active
        if request.headers.get("origin") != origin:
            return error(403, "ORIGIN_NOT_ALLOWED", "请从本项目页面发起登录。")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return error(415, "INVALID_REQUEST", "登录请求格式不正确。")
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        body.clear()
                        return error(413, "INVALID_REQUEST", "登录请求过大。")
        except TimeoutError:
            body.clear()
            return error(408, "REQUEST_TIMEOUT", "登录请求传输超时，请重试。")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            return error(422, "INVALID_REQUEST", "请输入学校账号和密码。")
        finally:
            body.clear()
        if not isinstance(payload, dict):
            return error(422, "INVALID_REQUEST", "请输入学校账号和密码。")
        username, password = payload.get("username"), payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return error(422, "INVALID_REQUEST", "请输入学校账号和密码。")
        username = username.strip()
        if not 1 <= len(username) <= 128 or not 1 <= len(password) <= 256:
            return error(422, "INVALID_REQUEST", "账号或密码长度不正确。")
        del payload

        now = time.monotonic()
        prune(now)
        account_key = "account:" + hashlib.sha256(username.casefold().encode()).hexdigest()
        client_key = "ip:" + (request.client.host if request.client else "unknown")
        limits = ((account_key, 5), (client_key, 30))
        if active >= 4 or len(attempts) >= 2000 or any(
            len(attempts.get(key, ())) >= limit for key, limit in limits
        ):
            response = error(429, "LOGIN_RATE_LIMITED", "登录尝试较多，请五分钟后再试。")
            response.headers["Retry-After"] = "300"
            return response
        if len(sessions) >= 1000:
            return error(503, "LOGIN_BUSY", "当前登录人数较多，请稍后再试。")
        for key, _ in limits:
            attempts.setdefault(key, deque()).append(now)
        active += 1
        try:
            identity = await verifier(username, password)
        except SchoolAuthError as exc:
            return error(exc.status_code, exc.code, exc.message)
        except Exception:
            # Upstream exception strings can contain authentication URLs or data.
            return error(503, "SCHOOL_UNAVAILABLE", "暂时无法完成学校验证，请稍后再试。")
        finally:
            password = ""
            active -= 1

        sessions.pop(session_key(request), None)
        token = secrets.token_urlsafe(32)
        sessions[hashlib.sha256(token.encode()).hexdigest()] = (
            time.monotonic() + SESSION_SECONDS,
            identity,
        )
        response = user_response(identity)
        response.set_cookie(
            COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
            secure=parsed.scheme == "https", samesite="lax", path="/",
        )
        return response

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        if request.headers.get("origin") != origin:
            return error(403, "ORIGIN_NOT_ALLOWED", "请从本项目页面退出登录。")
        sessions.pop(session_key(request), None)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie(
            COOKIE, path="/", secure=parsed.scheme == "https", httponly=True, samesite="lax",
        )
        return response

    return router
