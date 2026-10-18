from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_VERSION", "test-release")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "toolbox.sqlite3"))
    return TestClient(create_app(tmp_path / "missing-dist"))


def test_health_is_liveness_not_integration_readiness(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "tongtongsuo",
        "version": "test-release",
    }
    assert client.get("/api/status").json() == {
        "authentication": "school_account",
        "lock_control": "not_configured",
    }


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/callback?ticket=fake"])
def test_authentication_fails_closed(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AUTH_NOT_CONFIGURED"


def test_unlock_does_not_trust_client_identity(client: TestClient) -> None:
    response = client.post(
        "/api/locks/test-lock/unlock",
        json={"student_id": "untrusted", "authenticated": True},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "LOCK_CONTROL_NOT_CONFIGURED"


def test_missing_build_does_not_break_api(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "FRONTEND_NOT_BUILT"
    assert client.get("/scan/test-lock").status_code == 503
    assert client.get("/api/health").status_code == 200
    assert client.get("/assets/missing.js").status_code == 404


def test_built_frontend_only_serves_known_entry_points(tmp_path: Path) -> None:
    page = "<!doctype html><html><body>Frontend shell</body></html>"
    (tmp_path / "index.html").write_text(page, encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export {};", encoding="utf-8")
    client = TestClient(create_app(tmp_path, db_path=tmp_path / "inventory.sqlite3"))

    for path in ("/", "/m", "/admin", "/license", "/license?from=%2Fm%2Fscan%2FA", "/scan/A", "/scan/B", "/scan/new-cabinet", "/m/scan/A", "/m/scan/new-cabinet"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.text == page
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"

    assert client.get("/assets/app.js").text == "export {};"
    for path in ("/api/unknown", "/assets/missing.js", "/unknown", "/scan/a/b"):
        assert client.get(path).status_code == 404


def test_frontend_dist_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "index.html").write_text("Configured frontend", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))
    monkeypatch.delenv("APP_VERSION", raising=False)
    client = TestClient(create_app(db_path=tmp_path / "inventory.sqlite3"))
    assert client.get("/").text == "Configured frontend"
    assert client.get("/api/health").json()["version"] == "development"


def test_asset_caching_keeps_html_api_and_unversioned_files_fresh(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("Frontend", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("index-BwRNJl_I.js", "fonts-A1b_2C-d.woff2", "app.js", "jsqr-LICENSE.txt", "OFL.txt"):
        (assets / name).write_bytes(b"fixture asset content")
    client = TestClient(create_app(tmp_path, db_path=tmp_path / "inventory.sqlite3"))
    immutable = "public, max-age=31536000, immutable"
    response = client.get("/assets/index-BwRNJl_I.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == immutable
    assert client.get("/assets/fonts-A1b_2C-d.woff2").headers["cache-control"] == immutable
    conditional = client.get("/assets/index-BwRNJl_I.js", headers={"If-None-Match": response.headers["etag"]})
    assert conditional.status_code == 304
    assert conditional.headers["cache-control"] == immutable
    partial = client.get("/assets/index-BwRNJl_I.js", headers={"Range": "bytes=0-6"})
    assert partial.status_code == 206
    assert partial.headers["cache-control"] == immutable
    for path in ("/", "/scan/A", "/admin", "/license", "/assets/app.js", "/assets/jsqr-LICENSE.txt", "/assets/OFL.txt"):
        assert client.get(path).headers["cache-control"] == "no-cache"
    for path in ("/assets/missing-AbCd1234.js", "/assets/unknown.js", "/unknown"):
        missing = client.get(path)
        assert missing.status_code == 404
        assert missing.headers["cache-control"] == "no-store"
    for path in ("/api/health", "/api/auth/me", "/api/cabinets/A", "/api/unknown"):
        assert client.get(path).headers["cache-control"] == "no-store"
