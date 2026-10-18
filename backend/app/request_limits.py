"""Bound admin request buffering before FastAPI parses JSON or runs dependencies."""

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class AdminBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = 800_000, body_timeout: float = 5):
        self.app = app
        self.max_bytes = max_bytes
        self.body_timeout = body_timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or not (path == "/api/admin" or path.startswith("/api/admin/"))
                or scope["method"] in {"GET", "HEAD", "OPTIONS"}):
            await self.app(scope, receive, send)
            return

        async def reject(status: int, code: str, message: str):
            response = JSONResponse({"detail": {"code": code, "message": message}}, status_code=status,
                                    headers={"Cache-Control": "no-store"})
            await response(scope, receive, send)

        headers = scope.get("headers", [])
        lengths = [value for name, value in headers if name.lower() == b"content-length"]
        declared = None
        if lengths:
            # Reject ambiguous framing and noncanonical lengths without reading the body.
            if len(lengths) != 1 or not lengths[0].isdigit() or any(name.lower() == b"transfer-encoding" for name, _ in headers):
                await reject(400, "INVALID_CONTENT_LENGTH", "请求长度格式不正确。")
                return
            try:
                declared = int(lengths[0])
            except ValueError:
                await reject(400, "INVALID_CONTENT_LENGTH", "请求长度格式不正确。")
                return
            if declared > self.max_bytes:
                await reject(413, "REQUEST_TOO_LARGE", "管理请求过大，请缩小图标或输入内容。")
                return

        body = bytearray()
        too_large = False
        try:
            # Only receiving is timed; authorized database work is not cancelled.
            async with asyncio.timeout(self.body_timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request":
                        continue
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.max_bytes:
                        too_large = True
                        break
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(408, "REQUEST_TIMEOUT", "管理请求传输超时，请重试。")
            return

        if too_large:
            await reject(413, "REQUEST_TOO_LARGE", "管理请求过大，请缩小图标或输入内容。")
            return

        if declared is not None and declared != len(body):
            await reject(400, "INVALID_CONTENT_LENGTH", "实际请求长度与声明不一致。")
            return

        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                result = bytes(body)
                body.clear()
                return {"type": "http.request", "body": result, "more_body": False}
            return await receive()

        # Existing dependencies still decide authentication, authorization and Origin.
        await self.app(scope, replay, send)
