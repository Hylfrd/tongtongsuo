"""Student cabinet views expose only inventory and the current user's loans."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.database import Database


def create_cabinet_router(require_user, db_path: Path | None = None, ttlock=None):
    database = Database(db_path)
    router = APIRouter(prefix="/api/cabinets")

    def cabinet(connection, code):
        row = connection.execute(
            "SELECT id,code,name,group_name,location FROM cabinets WHERE code=?", (code,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "CABINET_NOT_FOUND", "message": "未找到该柜子，请重新扫码。"})
        return dict(row)

    @router.get("/{code}")
    def get_cabinet(code: str):
        with database.transaction() as connection:
            return cabinet(connection, code)

    @router.get("/{code}/tools")
    def get_tools(code: str, user=Depends(require_user)):
        with database.transaction() as connection:
            selected = cabinet(connection, code)
            rows = connection.execute(
                """SELECT t.id,t.name,t.name_en,t.description,t.description_en,
                    t.unit,t.unit_en,t.icon,t.icon_data,
                    MAX(0,t.quantity-COALESCE((SELECT SUM(l.quantity) FROM loans l
                        WHERE l.tool_id=t.id AND l.returned_at IS NULL),0)) AS available_quantity
                    FROM tools t WHERE t.cabinet_id=? AND t.archived=0 ORDER BY t.id""",
                (selected["id"],),
            )
            return {"items": [dict(row) for row in rows]}

    @router.get("/{code}/loans/me")
    def get_own_loans(code: str, user=Depends(require_user)):
        with database.transaction() as connection:
            selected = cabinet(connection, code)
            school_code = user.get("school_code")
            if not school_code:
                return {"items": []}
            rows = connection.execute(
                """SELECT l.id,l.tool_id,l.tool_name,t.name_en AS tool_name_en,
                    l.quantity,l.borrowed_at,t.unit,t.unit_en,t.icon,t.icon_data
                    FROM loans l JOIN tools t ON t.id=l.tool_id
                    WHERE l.school_code=? AND l.cabinet_id=? AND l.returned_at IS NULL
                    ORDER BY l.borrowed_at,l.id""",
                (school_code, selected["id"]),
            )
            return {"items": [dict(row) for row in rows]}

    @router.post("/{code}/unlock")
    def unlock(code: str, user=Depends(require_user)):
        with database.transaction() as connection:
            selected = cabinet(connection, code)
        if ttlock is None:
            raise HTTPException(503, detail={"code": "HARDWARE_NOT_CONFIGURED", "message": "通通锁接口尚未配置，请联系管理员。"})
        # Only the server-owned cabinet mapping selects devices. Client selection
        # data is not persisted as a loan; lending confirmation remains separate.
        return ttlock.unlock_cabinet(selected["id"], user)

    return router
