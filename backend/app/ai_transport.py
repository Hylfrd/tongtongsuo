"""Bounded, one-shot HTTPS requests to explicitly configured public AI providers."""

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit, urlunsplit

import httpx


class UpstreamError(Exception):
    """Messages are safe to display; never retain an upstream body or credential."""


def _is_public(address: str) -> bool:
    value = ipaddress.ip_address(address)
    if not value.is_global or value.is_multicast:
        return False
    if isinstance(value, ipaddress.IPv6Address):
        # Do not let IPv6 transition addresses tunnel into a private IPv4 target.
        if value.ipv4_mapped or value.sixtofour or value.teredo:
            return False
        if value in ipaddress.IPv6Network("64:ff9b::/96") or value in ipaddress.IPv6Network("64:ff9b:1::/48"):
            return False
    return True


def validate_base_url(value: str) -> str:
    message = "API 地址必须为公网 HTTPS 地址，端口仅支持 443，且不能包含账号、查询参数或片段。"
    try:
        if not isinstance(value, str) or len(value) > 500 or any(ord(c) <= 32 for c in value):
            raise ValueError()
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or url.port not in (None, 443)
                or "\\" in value or "?" in value or "#" in value):
            raise ValueError()
        host = url.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or any(not part or len(part) > 63 for part in host.split(".")):
                raise ValueError()
            if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in host):
                raise ValueError()
        else:
            if not _is_public(str(address)):
                raise ValueError()
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit(("https", authority, url.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError):
        raise ValueError(message) from None


async def _public_address(host: str) -> str:
    try:
        results = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=10,
        )
        addresses = list(dict.fromkeys(item[4][0] for item in results))
        if not addresses or any(not _is_public(item) for item in addresses):
            raise UpstreamError("API 地址必须解析到公网地址。")
        # The selected literal IP is used for the TCP connection. DNS is not consulted again.
        return addresses[0]
    except (OSError, ValueError, TimeoutError):
        raise UpstreamError("无法解析模型提供商的公网地址，请检查 API 地址。") from None


async def post_json(settings: dict, endpoint: str, payload: dict, *, max_response_bytes: int = 1024 * 1024) -> dict:
    """No redirects, environment proxies, endpoint fallback, or network retries."""
    if endpoint not in {"responses", "images/generations"}:
        raise ValueError("Unsupported AI endpoint")
    try:
        base = validate_base_url(settings["base_url"])
    except (KeyError, ValueError):
        raise UpstreamError("模型提供商 API 地址配置不正确。") from None
    host = urlsplit(base).hostname
    key = settings.get("api_key", "")
    if not key or not isinstance(key, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in key):
        raise UpstreamError("请重新配置有效的 API key。")
    timeout = max(10, min(900, int(settings.get("timeout_seconds", 600))))
    try:
        async with asyncio.timeout(timeout):
            address = await _public_address(host)
            url = httpx.URL(f"{base}/{endpoint}").copy_with(host=address)
            # HTTP Host and TLS verification/SNI retain the configured hostname while TCP
            # connects only to the validated literal IP (preventing DNS rebinding).
            authority = f"[{host}]" if ":" in host else host
            headers = {"Authorization": f"Bearer {key}", "Host": authority,
                       "Accept": "application/json", "Accept-Encoding": "identity"}
            transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
            async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(timeout, connect=20)) as client:
                async with client.stream("POST", url, headers=headers, json=payload,
                                         extensions={"sni_hostname": host}) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        if response.status_code in {401, 403}:
                            raise UpstreamError("模型提供商拒绝访问，请检查 API key 和模型权限。")
                        if response.status_code == 429:
                            raise UpstreamError("模型提供商限流或额度不足，请稍后手动重试。")
                        raise UpstreamError(f"模型提供商请求失败（HTTP {response.status_code}），未自动重试。")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise UpstreamError("模型提供商返回了不支持的压缩响应。")
                    length = response.headers.get("content-length", "")
                    if length.isdigit() and int(length) > max_response_bytes:
                        raise UpstreamError("模型提供商返回的数据过大。")
                    content = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                        if len(content) + len(chunk) > max_response_bytes:
                            raise UpstreamError("模型提供商返回的数据过大。")
                        content.extend(chunk)
                    try:
                        result = json.loads(content)
                    except (ValueError, UnicodeError, RecursionError):
                        raise UpstreamError("模型提供商未返回有效的 JSON 数据。") from None
                    if not isinstance(result, dict):
                        raise UpstreamError("模型提供商返回的数据格式不正确。")
                    return result
    except (httpx.HTTPError, OSError, TimeoutError):
        raise UpstreamError("模型请求超时或连接失败，未自动重试；可查看提供商用量后决定是否再生成。") from None
