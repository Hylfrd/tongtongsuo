"""Campus toolbox entry point with administrator-managed lock integration."""

import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import AuthManager
from app.admin import create_admin_router
from app.cabinets import create_cabinet_router
from app.request_limits import AdminBodyLimitMiddleware
from app.ai_settings import SettingsStore, create_ai_settings_router
from app.ai_generation import create_ai_generation_router
from app.members import create_members_router
from app.ttlock import TTLockService, create_ttlock_router


# Vite's default content fingerprint is eight URL-safe base64 characters.
# Unversioned public files (licences, etc.) must be revalidated after deployment.
FINGERPRINTED_ASSET = re.compile(r"^/assets/[^/]+-[A-Za-z0-9_-]{8}\.(?:js|css|png|jpe?g|webp|avif|svg|ico|woff2?|ttf)$")


def create_app(frontend_dist: Path | None = None, db_path: Path | None = None) -> FastAPI:
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
        # Process liveness only; upstream services are not called here.
        return {"status": "ok", "service": "tongtongsuo", "version": version}

    @application.get("/api/status")
    def status() -> dict[str, str]:
        return {"authentication": "school_account", "lock_control": "not_configured"}

    db_path = Path(db_path or os.environ.get("DATABASE_PATH", "runtime/tongtongsuo.sqlite3"))
    auth = AuthManager(db_path=db_path)
    application.add_middleware(AdminBodyLimitMiddleware)
    application.include_router(auth.router)
    application.include_router(create_members_router(auth.require_admin, auth.members))
    ttlock = TTLockService(db_path)
    application.include_router(create_ttlock_router(auth.require_admin, ttlock))
    application.include_router(create_admin_router(auth.require_admin, db_path, ttlock))
    application.include_router(create_cabinet_router(auth.require_user, db_path))
    ai_settings = SettingsStore(db_path)
    application.include_router(create_ai_settings_router(auth.require_admin, ai_settings))
    application.include_router(create_ai_generation_router(auth.require_admin, ai_settings, db_path))

    @application.middleware("http")
    async def private_api_responses(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/") or response.status_code >= 400:
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith("/assets/"):
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
                if response.status_code in (200, 206, 304) and FINGERPRINTED_ASSET.fullmatch(path)
                else "no-cache"
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.get("/api/auth/login")
    @application.get("/api/auth/callback")
    def authentication_unavailable() -> None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AUTH_NOT_CONFIGURED",
                "message": "请通过本项目登录表单验证学校账号；学校直接回调尚未接入。",
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
    @application.get("/m", include_in_schema=False)
    @application.get("/admin", include_in_schema=False)
    @application.get("/license", include_in_schema=False)
    def frontend_root() -> FileResponse:
        return frontend_index()

    @application.get("/scan/{cabinet_code}", include_in_schema=False)
    @application.get("/m/scan/{cabinet_code}", include_in_schema=False)
    def frontend_scan(cabinet_code: str) -> FileResponse:
        # A QR reference identifies an entry point; it never grants permission.
        return frontend_index()

    # Restrict SPA entry points so misspelled API and asset URLs retain real 404s.
    # Build assets before starting the production process.
    assets = dist / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    return application


app = create_app()
