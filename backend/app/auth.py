"""Short-lived project sessions; school passwords and cookies are never retained."""

from collections import deque
from collections.abc import Awaitable, Callable
import asyncio
import hashlib
import json
import math
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.school_sso import SchoolAuthError, SchoolIdentity, authenticate
from app.admin_password import parse_password_hash, verify_password

COOKIE = "tts_session"
SESSION_SECONDS = 1800


class AuthManager:
    """Owns a single shared session store for login and protected API dependencies."""

    def __init__(
        self,
        verifier: Callable[[str, str], Awaitable[SchoolIdentity]] = authenticate,
    ) -> None:
        self.router = self._create_router(verifier)

    async def require_user(self, request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        self._prune(time.monotonic())
        session = self._sessions.get(self._session_key(request))
        if session is None:
            raise HTTPException(401, detail={"code": "NO_SESSION", "message": "请先登录。"},
                                headers={"Cache-Control": "no-store"})
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("origin") != self.origin:
            raise HTTPException(403, detail={"code": "ORIGIN_NOT_ALLOWED", "message": "请从本项目页面发起操作。"},
                                headers={"Cache-Control": "no-store"})
        return dict(session[1])

    async def require_admin(self, request: Request, response: Response) -> dict:
        identity = await self.require_user(request, response)
        if identity.get("role") != "admin":
            raise HTTPException(403, detail={"code": "ADMIN_REQUIRED", "message": "此操作需要管理员权限。"},
                                headers={"Cache-Control": "no-store"})
        return identity

    def _create_router(
        self,
        verifier: Callable[[str, str], Awaitable[SchoolIdentity]],
    ) -> APIRouter:
        router = APIRouter(prefix="/api/auth")
        origin = os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:5173").rstrip("/")
        parsed = urlsplit(origin)
        if (parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.path
                or parsed.query or parsed.fragment or parsed.username is not None):
            raise ValueError("PUBLIC_ORIGIN must contain only scheme and host/port")
        self.origin = origin
        admin_username = os.environ.get("ADMIN_USERNAME", "admin").strip()
        if not 1 <= len(admin_username) <= 128:
            raise ValueError("ADMIN_USERNAME must contain 1 to 128 characters")
        admin_password = parse_password_hash(os.environ.get("ADMIN_PASSWORD_HASH", ""))
        sessions: dict[str, tuple[float, dict]] = {}
        self._sessions = sessions
        attempts: dict[str, deque[float]] = {}
        # Hashed account -> consecutive failures, last failure, next allowed attempt.
        failures: dict[str, tuple[int, float, float]] = {}
        in_flight: set[str] = set()
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
            for key, (_, last_failure, _) in list(failures.items()):
                if last_failure <= now - 600 and key not in in_flight:
                    del failures[key]

        def credentials_failed(account_key: str, message: str, code: str = "INVALID_CREDENTIALS") -> JSONResponse:
            now = time.monotonic()
            count, last_failure, _ = failures.get(account_key, (0, now, 0))
            count = min(count + 1, 5) if last_failure > now - 600 else 1
            cooldown = 300 if count >= 5 else 60 if count == 4 else 30 if count == 3 else 0
            failures[account_key] = (count, now, now + cooldown)
            if cooldown:
                message += f" 请等待 {cooldown} 秒后再试。"
            response = error(401, code, message)
            if cooldown:
                response.headers["Retry-After"] = str(cooldown)
            return response

        self._prune = prune
        self._session_key = session_key

        def user_response(identity: dict) -> JSONResponse:
            return JSONResponse(
                {"user": identity},
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
            if (active >= 4 or len(attempts.get(client_key, ())) >= 30
                    or (client_key not in attempts and len(attempts) >= 2000)
                    or (account_key not in failures and len(failures) >= 2000)):
                response = error(429, "LOGIN_RATE_LIMITED", "登录尝试较多，请五分钟后再试。")
                response.headers["Retry-After"] = "300"
                return response
            attempts.setdefault(client_key, deque()).append(now)
            next_allowed = failures.get(account_key, (0, now, 0))[2]
            if next_allowed > now:
                remaining = math.ceil(next_allowed - now)
                response = error(429, "LOGIN_COOLDOWN", f"账号或密码连续输入错误，请等待 {remaining} 秒后再试。")
                response.headers["Retry-After"] = str(remaining)
                return response
            if account_key in in_flight:
                response = error(429, "LOGIN_IN_PROGRESS", "此账号正在验证，请稍后再试。")
                response.headers["Retry-After"] = "1"
                return response
            if len(sessions) >= 1000:
                return error(503, "LOGIN_BUSY", "当前登录人数较多，请稍后再试。")
            # Reserve bounded account state before the first asynchronous operation.
            failures.setdefault(account_key, (0, now, 0))
            in_flight.add(account_key)
            active += 1
            try:
                if username.casefold() == admin_username.casefold():
                    if admin_password is None:
                        return error(503, "ADMIN_NOT_CONFIGURED", "管理员登录尚未配置，请先设置管理员密码哈希。")
                    if not await asyncio.to_thread(verify_password, password, admin_password):
                        return credentials_failed(account_key, "账号或密码不正确。")
                    identity = {"name": "管理员", "school_code": None, "role": "admin"}
                else:
                    school_identity = await verifier(username, password)
                    identity = {"name": school_identity.name, "school_code": school_identity.school_code}
            except SchoolAuthError as exc:
                if exc.status_code == 401 and exc.code in ("INVALID_CREDENTIALS", "SCHOOL_CREDENTIALS_REJECTED"):
                    return credentials_failed(account_key, exc.message, exc.code)
                return error(exc.status_code, exc.code, exc.message)
            except Exception:
                # Upstream exception strings can contain authentication URLs or data.
                return error(503, "SCHOOL_UNAVAILABLE", "暂时无法完成学校验证，请稍后再试。")
            finally:
                password = ""
                active -= 1
                in_flight.discard(account_key)
                if failures.get(account_key, (0, 0, 0))[0] == 0:
                    failures.pop(account_key, None)

            failures.pop(account_key, None)
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


def create_auth_router(
    verifier: Callable[[str, str], Awaitable[SchoolIdentity]] = authenticate,
) -> APIRouter:
    """Compatibility factory; applications with admin routes should share AuthManager."""
    return AuthManager(verifier).router
