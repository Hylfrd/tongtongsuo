"""Login mode and separately scoped imported/SSO member directories."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import unicodedata
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.member_excel import parse_members, template_workbook


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def normalize_member(name: str, school_code: str, room_number: str) -> dict:
    values = {"name": normalize(name), "school_code": normalize(school_code).upper(),
              "room_number": normalize(room_number).upper()}
    for field, value in values.items():
        limit = 128 if field == "name" else 64
        if not value or len(value) > limit or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("姓名、学号和宿舍号均须填写，且不能包含控制字符或过长内容。")
    return values


class MembersStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS login_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL CHECK(mode IN ('sso','roster'))
                );
                INSERT OR IGNORE INTO login_settings VALUES (1,'sso');
                CREATE TABLE IF NOT EXISTS members (
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL CHECK(source IN ('sso','roster')),
                    name TEXT NOT NULL, school_code TEXT NOT NULL, room_number TEXT NOT NULL DEFAULT '',
                    first_login_at TEXT, last_login_at TEXT, UNIQUE(source, school_code)
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def mode(self):
        with closing(self.connect()) as db:
            return db.execute("SELECT mode FROM login_settings WHERE id=1").fetchone()[0]

    def set_mode(self, mode):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE login_settings SET mode=? WHERE id=1", (mode,))

    def verify_roster(self, values):
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT * FROM members WHERE source='roster' AND school_code=?", (values["school_code"],)).fetchone()
            if row is None or row["name"].casefold() != values["name"].casefold() or row["room_number"] != values["room_number"]:
                return None
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            db.execute("UPDATE members SET first_login_at=COALESCE(first_login_at,?),last_login_at=? WHERE id=?", (now, now, row["id"]))
            return {"name": row["name"], "school_code": row["school_code"]}

    def record_sso(self, identity):
        # Keep the verified school identifier verbatim, so member loan links match sessions.
        if not identity.get("school_code"):
            return
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with closing(self.connect()) as db, db:
            db.execute("""INSERT INTO members(source,name,school_code,first_login_at,last_login_at)
                VALUES ('sso',?,?,?,?) ON CONFLICT(source,school_code) DO UPDATE SET
                name=excluded.name,last_login_at=excluded.last_login_at""",
                (identity["name"], identity["school_code"], now, now))

    def import_rows(self, rows):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = {r[0] for r in db.execute("SELECT school_code FROM members WHERE source='roster'")}
            added = sum(row["school_code"] not in existing for row in rows)
            db.executemany("""INSERT INTO members(source,name,school_code,room_number) VALUES ('roster',:name,:school_code,:room_number)
                ON CONFLICT(source,school_code) DO UPDATE SET name=excluded.name,room_number=excluded.room_number""", rows)
            total = db.execute("SELECT COUNT(*) FROM members WHERE source='roster'").fetchone()[0]
            return {"added": added, "updated": len(rows) - added, "total": total}


class LoginSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["sso", "roster"]


def create_members_router(require_admin, store: MembersStore):
    router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

    @router.get("/login-settings")
    def login_settings():
        return {"mode": store.mode()}

    @router.put("/login-settings")
    def update_login_settings(payload: LoginSettings):
        store.set_mode(payload.mode)
        return {"mode": payload.mode}

    @router.get("/members")
    def members(source: Literal["sso", "roster"], q: str = Query("", max_length=200),
                page: int = Query(1, ge=1, le=1_000_000), page_size: int = Query(50, ge=1, le=100)):
        query = normalize(q).casefold()
        with closing(store.connect()) as db:
            # Unicode casefold works for names and student identifiers consistently with login.
            db.create_function("fold", 1, lambda value: value.casefold())
            where = "source=? AND (instr(fold(name),?)>0 OR instr(fold(school_code),?)>0 OR instr(fold(room_number),?)>0)"
            args = (source, query, query, query)
            total = db.execute("SELECT COUNT(*) FROM members WHERE " + where, args).fetchone()[0]
            items = [dict(row) for row in db.execute("SELECT id,name,school_code,room_number,first_login_at,last_login_at FROM members WHERE " + where + " ORDER BY school_code,id LIMIT ? OFFSET ?", (*args, page_size, (page - 1) * page_size))]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @router.get("/members/template.xlsx")
    def template():
        return Response(template_workbook(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": "attachment; filename=members.xlsx; filename*=UTF-8''" + quote("正雅苑成员导入模板.xlsx")})

    @router.post("/members/import")
    async def import_members(request: Request):
        if request.headers.get("content-type", "").split(";")[0] not in {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"}:
            raise HTTPException(415, detail={"code": "INVALID_WORKBOOK", "message": "请上传 .xlsx 格式的 Excel 文件。"})
        try:
            raw_rows = parse_members(await request.body())
            rows, seen = [], set()
            for number, values in raw_rows:
                try:
                    row = normalize_member(*values)
                except ValueError as exc:
                    raise ValueError(f"第 {number} 行：{exc}") from None
                if row["school_code"] in seen:
                    raise ValueError(f"第 {number} 行：学号重复，请先整理重复成员。")
                seen.add(row["school_code"])
                rows.append(row)
            return store.import_rows(rows)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "INVALID_WORKBOOK", "message": str(exc)}) from None

    return router
