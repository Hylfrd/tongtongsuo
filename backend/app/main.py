"""Deployable shell; authentication and physical lock control stay disabled."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def create_app(frontend_dist: Path | None = None) -> FastAPI:
    application = FastAPI(
        title="Tongtongsuo",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    version = os.environ.get("APP_VERSION", "development")
    dist = (
        frontend_dist
        if frontend_dist is not None
        else Path(
            os.environ.get(
                "FRONTEND_DIST",
                str(Path(__file__).resolve().parents[2] / "frontend" / "dist"),
            )
        )
    ).resolve()

    @application.get("/api/health")
    def health() -> dict[str, str]:
        # Process liveness only: neither SSO nor TTLock is connected yet.
        return {"status": "ok", "service": "tongtongsuo", "version": version}

    @application.get("/api/status")
    def status() -> dict[str, str]:
        return {"authentication": "not_configured", "lock_control": "not_configured"}

    @application.get("/api/auth/login")
    @application.get("/api/auth/callback")
    def authentication_unavailable() -> None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AUTH_NOT_CONFIGURED",
                "message": "学校身份认证尚未接入，暂时无法登录。",
            },
        )

    @application.post("/api/locks/{lock_id}/unlock")
    def unlock_unavailable(lock_id: str) -> None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LOCK_CONTROL_NOT_CONFIGURED",
                "message": "通通锁接口尚未接入，暂时无法开锁。",
            },
        )

    def frontend_index() -> FileResponse:
        index = dist / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "FRONTEND_NOT_BUILT",
                    "message": "网页尚未构建，请先完成前端构建后再访问。",
                },
            )
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    @application.get("/", include_in_schema=False)
    def frontend_root() -> FileResponse:
        return frontend_index()

    @application.get("/scan/{lock_reference}", include_in_schema=False)
    def frontend_scan(lock_reference: str) -> FileResponse:
        # A QR reference identifies an entry point; it never grants permission.
        return frontend_index()

    # Restrict SPA entry points so misspelled API and asset URLs retain real 404s.
    # Build assets before starting the production process.
    assets = dist / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    return application


app = create_app()
