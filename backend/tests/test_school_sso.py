import asyncio
import hashlib
import json
from dataclasses import asdict

import httpx
import pytest
from gmssl import sm2

from app.school_sso import (
    SchoolAuthError,
    SchoolIdentity,
    _encrypt_password,
    authenticate,
)


PUBLIC_KEY = sm2.default_ecc_table["g"]
LOGIN_PATH = "/auth/pwd/ldap-test"


def school_transport(*, entrance=None, login=None, user=None, status=200):
    requests = []

    def handle(request: httpx.Request):
        requests.append(request)
        assert request.url.host == "sso.bnbu.edu.cn"
        if request.url.path == "/":
            assert not request.headers.get("cookie")
            return httpx.Response(200, text="School entry")
        timestamp = request.headers["tencent-Request-T"]
        rt = "fresh-request-cookie" if request.url.path != "/auth/loginEntrance" else ""
        assert request.headers["tencent-Request-Key"] == hashlib.md5(
            (rt + timestamp).encode(), usedforsecurity=False
        ).hexdigest()
        if request.url.path == "/auth/loginEntrance":
            return httpx.Response(
                200,
                json={
                    "httpStatus": "OK",
                    "data": entrance
                    if entrance is not None
                    else [{"authType": "LDAP", "enable": True, "baseUrl": "/auth/pwd", "url": "/ldap-test"}],
                },
                headers={"set-cookie": "rt=fresh-request-cookie; Path=/; Secure"},
            )
        if request.url.path == "/auth/flow/login/getSm2PubK":
            return httpx.Response(200, json={"httpStatus": "OK", "data": PUBLIC_KEY})
        if request.url.path == LOGIN_PATH:
            assert request.method == "POST"
            body = json.loads(request.content)
            assert body["userName"] == "v-test"
            assert body["verifyCode"] == ""
            cipher = sm2.CryptSM2(private_key="1", public_key=PUBLIC_KEY, mode=1)
            assert body["password"].startswith("04")
            assert cipher.decrypt(bytes.fromhex(body["password"][2:])) == b"test-password"
            return httpx.Response(
                status,
                json=login
                if login is not None
                else {"httpStatus": "OK", "data": {"url": "https://portal.bnbu.edu.cn/?secret=never-follow"}},
            )
        if request.url.path == "/auth/getUser":
            return httpx.Response(
                200,
                json={
                    "httpStatus": "OK",
                    "data": user
                    if user is not None
                    else {
                        "individualId": "internal-identity",
                        "individualUserName": "V-TEST",
                        "individualDisplayName": "测试用户",
                        "subjectCode": "personnel-123",
                        "sessionId": "never-export",
                        "password": "never-export",
                    },
                },
            )
        pytest.fail("Unexpected upstream request")

    return httpx.MockTransport(handle), requests


def test_returns_only_matching_identity_and_discards_upstream_session():
    transport, requests = school_transport()
    for _ in range(2):
        identity = asyncio.run(authenticate("v-test", "test-password", transport=transport))
        assert identity == SchoolIdentity("测试用户", "personnel-123")
        assert asdict(identity) == {"name": "测试用户", "school_code": "personnel-123"}
    assert len([r for r in requests if r.method == "POST"]) == 2
    assert len(requests) == 10


@pytest.mark.parametrize(
    "overrides",
    [
        {"baseUrl": "https://other.example/auth/pwd"},
        {"url": "//other.example/ldap-test"},
        {"url": "/ldap-../evil"},
        {"fullUrl": "https://other.example/auth/pwd/ldap-test"},
        {"authType": "PWD"},
    ],
)
def test_does_not_send_credentials_to_unapproved_entrance(overrides):
    entry = {"authType": "LDAP", "enable": True, "baseUrl": "/auth/pwd", "url": "/ldap-test"}
    entry.update(overrides)
    transport, requests = school_transport(entrance=[entry])
    with pytest.raises(SchoolAuthError) as caught:
        asyncio.run(authenticate("v-test", "test-password", transport=transport))
    assert caught.value.status_code == 503
    assert all(request.method == "GET" for request in requests)


def test_mismatched_account_is_not_accepted():
    transport, _ = school_transport(user={
        "individualId": "someone-else", "individualUserName": "different-user",
        "individualDisplayName": "Other Person", "subjectCode": "123",
    })
    with pytest.raises(SchoolAuthError) as caught:
        asyncio.run(authenticate("v-test", "test-password", transport=transport))
    assert caught.value.status_code == 503


@pytest.mark.parametrize("login,status,code", [
    ({"data": {"needVerifyCode": True, "message": "sensitive-error"}}, 200, "SCHOOL_CHALLENGE_REQUIRED"),
    ({"data": {"message": "sensitive-error"}}, 200, "SCHOOL_CREDENTIALS_REJECTED"),
    ({"data": {"message": "sensitive-error"}}, 500, "SCHOOL_AUTH_UNAVAILABLE"),
    ({"data": "unexpected-sensitive-error"}, 200, "SCHOOL_AUTH_UNAVAILABLE"),
])
def test_challenges_and_errors_are_redacted_without_retries(login, status, code):
    transport, requests = school_transport(login=login, status=status)
    with pytest.raises(SchoolAuthError) as caught:
        asyncio.run(authenticate("v-test", "test-password", transport=transport))
    assert caught.value.code == code
    assert "sensitive" not in str(caught.value)
    assert caught.value.status_code == (401 if code == "SCHOOL_CREDENTIALS_REJECTED" else 503)
    assert len([r for r in requests if r.method == "POST"]) == 1
    assert not any(r.url.path == "/auth/getUser" for r in requests)


def test_network_failure_does_not_expose_upstream_exception():
    def fail(request):
        raise httpx.ReadTimeout("sensitive-url-and-cookie", request=request)

    with pytest.raises(SchoolAuthError) as caught:
        asyncio.run(authenticate("v-test", "test-password", transport=httpx.MockTransport(fail)))
    assert caught.value.code == "SCHOOL_AUTH_UNAVAILABLE"
    assert "sensitive" not in str(caught.value)


def test_sm2_uncompressed_c1c3c2_roundtrip_and_randomness():
    encrypted = _encrypt_password("密码123", "04" + PUBLIC_KEY)
    other = _encrypt_password("密码123", PUBLIC_KEY)
    cipher = sm2.CryptSM2(private_key="1", public_key=PUBLIC_KEY, mode=1)
    assert encrypted.startswith("04")
    assert len(bytes.fromhex(encrypted)) == 1 + 64 + 32 + len("密码123".encode())
    assert cipher.decrypt(bytes.fromhex(encrypted[2:])) == "密码123".encode()
    assert encrypted != other
