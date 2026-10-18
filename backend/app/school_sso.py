"""Isolated, short-lived BNBU school-account authentication.

This adapts the school's currently observed web login, not an official integration
contract. It never follows login redirects or exports upstream session material.
"""

import asyncio
import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from gmssl import func, sm2


SCHOOL_ORIGIN = "https://sso.bnbu.edu.cn"
_SM2_ORDER = int(sm2.default_ecc_table["n"], 16)


def _secure_random_hex(length: int) -> str:
    if length == 64:
        return f"{secrets.randbelow(_SM2_ORDER - 1) + 1:064x}"
    return secrets.token_hex((length + 1) // 2)[:length]


# gmssl 3.2.2 uses random.choice for encryption nonces. Replace only its random
# source, once, with cryptographic randomness; all SM2 operations stay in gmssl.
func.random_hex = _secure_random_hex


@dataclass(frozen=True)
class SchoolIdentity:
    name: str
    school_code: str | None


class SchoolAuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _unavailable() -> SchoolAuthError:
    return SchoolAuthError(
        "SCHOOL_AUTH_UNAVAILABLE", "学校认证暂时不可用，请稍后重试。"
    )


def _challenge() -> SchoolAuthError:
    return SchoolAuthError(
        "SCHOOL_CHALLENGE_REQUIRED",
        "学校要求额外验证，当前暂不支持，请先使用学校官方登录入口。",
    )


def _headers(client: httpx.AsyncClient) -> dict[str, str]:
    stamp = str(time.time_ns() // 1_000_000)
    rt = client.cookies.get("rt") or ""
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": SCHOOL_ORIGIN + "/",
        "tencent-Request-T": stamp,
        # This MD5 is the school's request-format checksum, not password storage.
        "tencent-Request-Key": hashlib.md5(
            (rt + stamp).encode(), usedforsecurity=False
        ).hexdigest(),
    }


async def _json_request(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> dict[str, Any]:
    response = await client.request(method, path, headers=_headers(client), **kwargs)
    if response.status_code != 200 or len(response.content) > 256_000:
        raise _unavailable()
    try:
        result = response.json()
    except ValueError:
        raise _unavailable() from None
    if not isinstance(result, dict):
        raise _unavailable()
    return result


def _login_path(result: dict[str, Any]) -> str:
    entries = result.get("data")
    if result.get("httpStatus") != "OK" or not isinstance(entries, list):
        raise _unavailable()
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("authType") == "LDAP"
        and entry.get("enable") is True
    ]
    if len(matches) != 1:
        raise _unavailable()
    entry = matches[0]
    suffix = entry.get("url")
    if (
        entry.get("baseUrl") != "/auth/pwd"
        or not isinstance(suffix, str)
        or not re.fullmatch(r"/ldap-[A-Za-z0-9_-]+", suffix)
    ):
        raise _unavailable()
    path = "/auth/pwd" + suffix
    if entry.get("fullUrl", path) != path:
        raise _unavailable()
    return path


def _encrypt_password(password: str, public_key: Any) -> str:
    if not isinstance(public_key, str):
        raise _unavailable()
    if len(public_key) == 130 and public_key.startswith("04"):
        public_key = public_key[2:]
    if not re.fullmatch(r"[0-9a-fA-F]{128}", public_key):
        raise _unavailable()
    cipher = sm2.CryptSM2(private_key="", public_key=public_key, mode=1)
    # The library constructor uses lstrip('04'), which can trim coordinate digits.
    cipher.public_key = public_key
    try:
        ciphertext = cipher.encrypt(password.encode("utf-8"))
    except (ValueError, TypeError, IndexError, ZeroDivisionError):
        raise _unavailable() from None
    if not ciphertext:
        raise _unavailable()
    # The school's SM2Cipher(1) wire format includes the uncompressed point tag.
    return "04" + ciphertext.hex()


def _identity(result: dict[str, Any], username: str) -> SchoolIdentity:
    data = result.get("data")
    if result.get("httpStatus") != "OK" or not isinstance(data, dict):
        raise _unavailable()
    actual_username = data.get("individualUserName")
    name = data.get("individualDisplayName")
    school_code = data.get("subjectCode")
    identity_id = data.get("individualId")
    if (
        not isinstance(actual_username, str)
        or actual_username.casefold() != username.casefold()
        or not isinstance(identity_id, str)
        or not identity_id.strip()
        or not isinstance(name, str)
        or not name.strip()
        or len(name) > 200
        or (
            school_code is not None
            and (not isinstance(school_code, str) or len(school_code) > 200)
        )
    ):
        raise _unavailable()
    # subjectCode is a school personnel code, not verified student status.
    return SchoolIdentity(name=name.strip(), school_code=school_code or None)


async def authenticate(
    username: str,
    password: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SchoolIdentity:
    """Authenticate once and return only name/personnel code; discard cookies."""
    try:
        async with asyncio.timeout(40):
            async with httpx.AsyncClient(
                base_url=SCHOOL_ORIGIN,
                timeout=15,
                follow_redirects=False,
                transport=transport,
            ) as client:
                response = await client.get("/")
                if response.status_code != 200:
                    raise _unavailable()
                path = _login_path(
                    await _json_request(client, "GET", "/auth/loginEntrance?context=")
                )
                key = await _json_request(client, "GET", "/auth/flow/login/getSm2PubK")
                if key.get("httpStatus") != "OK":
                    raise _unavailable()
                encrypted = _encrypt_password(password, key.get("data"))
                result = await _json_request(
                    client,
                    "POST",
                    path,
                    json={
                        "userName": username,
                        "password": encrypted,
                        "verifyCode": "",
                    },
                )
                data = result.get("data")
                if not isinstance(data, dict):
                    raise _unavailable()
                if data.get("needVerifyCode"):
                    raise _challenge()
                if not isinstance(data.get("url"), str) or not data["url"]:
                    if data.get("message"):
                        raise SchoolAuthError(
                            "SCHOOL_CREDENTIALS_REJECTED", "学校未接受此账号或密码。", 401
                        )
                    raise _unavailable()
                # A redirect URL alone proves no identity. Read and match the
                # current user on this isolated school session before accepting.
                return _identity(
                    await _json_request(client, "GET", "/auth/getUser"), username
                )
    except (httpx.HTTPError, httpx.CookieConflict, TimeoutError):
        # Never expose upstream response bodies, URLs, tokens or exception text.
        raise _unavailable() from None
