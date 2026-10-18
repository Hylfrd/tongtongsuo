"""Cabinet-centered administration and authenticated hardware control."""

import base64
import binascii
import math
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.database import Database
from app.loan_export import workbook


Name = Annotated[str, Field(min_length=1, max_length=120)]
Code = Annotated[str, Field(min_length=1, max_length=64)]
Note = Annotated[str, Field(max_length=2000)]
ExternalId = Annotated[str, Field(min_length=1, max_length=100)]
Icon = Literal["screwdriver", "tape", "hammer", "pliers", "toolbox"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Cabinet(Input):
    code: Code
    name: Name
    group_name: Annotated[str, Field(max_length=120)] = ""
    location: Annotated[str, Field(max_length=250)] = ""
    notes: Note = ""

    @field_validator("code", mode="before")
    @classmethod
    def validate_scan_code(cls, value):
        if isinstance(value, str) and any(
            char in "/\\" or unicodedata.category(char) in {"Cc", "Cf", "Cs"}
            for char in value
        ):
            raise ValueError("柜子编号不能包含斜杠或控制字符，请使用可扫码的单段编号")
        return value


class Gateway(Input):
    code: Code
    name: Name
    provider_gateway_id: ExternalId | None = None
    notes: Note = ""


class Lock(Input):
    code: Code
    name: Name
    provider_lock_id: ExternalId | None = None
    cabinet_id: Annotated[int, Field(gt=0, strict=True)] | None = None
    gateway_id: Annotated[int, Field(gt=0, strict=True)] | None = None
    notes: Note = ""


class Tool(Input):
    cabinet_id: Annotated[int, Field(gt=0, strict=True)]
    name: Name
    name_en: Annotated[str, Field(max_length=120)] = ""
    description: Note = ""
    description_en: Note = ""
    unit: Annotated[str, Field(min_length=1, max_length=16)] = "件"
    unit_en: Annotated[str, Field(max_length=32)] = ""
    notes: Note = ""
    quantity: Annotated[int, Field(ge=0, le=1000000, strict=True)] = 1
    icon: Icon = "toolbox"
    icon_data: Annotated[str, Field(max_length=700000)] | None = None

    @field_validator("icon_data")
    @classmethod
    def validate_image(cls, value):
        if value is None:
            return value
        try:
            prefix, encoded = value.split(",", 1)
            if prefix not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
                raise ValueError()
            data = base64.b64decode(encoded, validate=True)
            if not data or len(data) > 512 * 1024:
                raise ValueError()
            valid = (
                prefix == "data:image/png;base64" and data.startswith(b"\x89PNG\r\n\x1a\n")
                or prefix == "data:image/jpeg;base64" and data.startswith(b"\xff\xd8\xff")
                or prefix == "data:image/webp;base64" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
            )
            if not valid:
                raise ValueError()
        except (ValueError, binascii.Error):
            raise ValueError("图标必须为不超过 512 KiB 的 PNG、JPEG 或 WebP 图片") from None
        return value


class Action(Input):
    action: Literal["unlock", "lock", "status"]


def not_found():
    raise HTTPException(404, "未找到该记录")


def conflict(message="编号或平台 ID 已存在，或关联记录不存在；请检查后重试"):
    raise HTTPException(409, message)


def hardware_unavailable():
    raise HTTPException(503, detail={"code": "HARDWARE_NOT_CONFIGURED", "message": "通通锁云接口尚未配置，未向设备发送任何请求。"})


def validate(model, payload):
    try:
        return model.model_validate(payload).model_dump()
    except ValidationError as exc:
        # Do not echo uploaded image data into errors.
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        raise HTTPException(422, detail={"message": "输入内容无效", "errors": errors}) from None


def borrowed(connection, tool_id):
    return connection.execute("SELECT COALESCE(SUM(quantity),0) FROM loans WHERE tool_id=? AND returned_at IS NULL", (tool_id,)).fetchone()[0]


def find(connection, table, record_id):
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    if row is None or table == "tools" and row["archived"]:
        not_found()
    return dict(row)


def present(connection, table, record):
    record = dict(record)
    if table in {"locks", "gateways"}:
        record["status"] = "unknown"
    if table == "cabinets":
        record["lock_count"] = connection.execute("SELECT COUNT(*) FROM locks WHERE cabinet_id=?", (record["id"],)).fetchone()[0]
        record["tool_count"] = connection.execute("SELECT COUNT(*) FROM tools WHERE cabinet_id=? AND archived=0", (record["id"],)).fetchone()[0]
    if table == "tools":
        record.pop("archived", None)
        record["borrowed_quantity"] = borrowed(connection, record["id"])
        record["available_quantity"] = record["quantity"] - record["borrowed_quantity"]
        record["borrowers"] = [dict(row) for row in connection.execute("SELECT user_name AS name,school_code,quantity,borrowed_at FROM loans WHERE tool_id=? AND returned_at IS NULL ORDER BY borrowed_at", (record["id"],))]
    return record


def usage_metrics(connection):
    local_timezone = timezone(timedelta(hours=8))
    today = datetime.now(local_timezone).date()
    users, borrow_visits, return_visits = set(), set(), set()

    def timestamp(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(local_timezone)

    # Loans are item rows, without operation IDs. Group the items from the same
    # person, cabinet and instant rather than counting every tool as a visit.
    for row in connection.execute("SELECT id,school_code,cabinet_id,borrowed_at,returned_at FROM loans"):
        school_code = (row["school_code"] or "").strip()
        if school_code:
            users.add(school_code)
        # There is no user_id fallback in this schema. Do not merge unidentified
        # people by their names or count an empty school code as a known user.
        identity = ("school", school_code) if school_code else ("loan", row["id"])
        borrow_visits.add((identity, row["cabinet_id"], timestamp(row["borrowed_at"])))
        if row["returned_at"]:
            return_visits.add((identity, row["cabinet_id"], timestamp(row["returned_at"])))

    average_ratio = connection.execute(
        """WITH outstanding AS (
            SELECT tool_id,SUM(quantity) AS quantity FROM loans
            WHERE returned_at IS NULL GROUP BY tool_id
        ), cabinet_totals AS (
            SELECT t.cabinet_id,SUM(t.quantity) AS total,
                SUM(COALESCE(o.quantity,0)) AS borrowed
            FROM tools t LEFT JOIN outstanding o ON o.tool_id=t.id
            WHERE t.archived=0 GROUP BY t.cabinet_id
        )
        SELECT COALESCE(AVG(1.0*borrowed/total),0)
        FROM cabinet_totals WHERE total>0"""
    ).fetchone()[0]
    first_day = min((visit[2].date() for visit in borrow_visits), default=today)
    elapsed_days = max(1, (today - first_day).days + 1)
    return {
        "user_count": len(users),
        "average_borrow_ratio": average_ratio,
        "borrow_visit_count": len(borrow_visits),
        "borrow_visits_per_day": len(borrow_visits) / elapsed_days,
        "today_borrow_count": sum(visit[2].date() == today for visit in borrow_visits),
        "today_return_count": sum(visit[2].date() == today for visit in return_visits),
    }


def create_admin_router(require_admin, db_path: Path | None = None, ttlock=None):
    database = Database(db_path)
    router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

    def hardware_state(table, record):
        if ttlock is not None and table in {"locks", "gateways"}:
            record.update(ttlock.device_snapshot(table, record["id"]))
        return record

    @router.get("/overview")
    def overview():
        configured = bool(ttlock and ttlock.public()["connected"])
        with database.transaction() as connection:
            counts = {key: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for key, table in [("cabinet_count", "cabinets"), ("lock_count", "locks"), ("gateway_count", "gateways")]}
            counts["tool_count"] = connection.execute("SELECT COUNT(*) FROM tools WHERE archived=0").fetchone()[0]
            counts["active_loan_count"] = connection.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NULL").fetchone()[0]
            return {**counts, **usage_metrics(connection), "hardware_configured": configured}

    def register(table, model):
        # table and model come only from the fixed declarations below.
        def list_records(cabinet_id: Annotated[int | None, Query(gt=0)] = None):
            with database.transaction() as connection:
                where = ["archived=0"] if table == "tools" else []
                args = []
                if cabinet_id is not None and table in {"tools", "locks"}:
                    where.append("cabinet_id=?")
                    args.append(cabinet_id)
                sql = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
                rows = [present(connection, table, row) for row in connection.execute(sql, args)]
            return {"items": [hardware_state(table, row) for row in rows]}

        def create_record(payload: dict):
            values = validate(model, payload)
            try:
                with database.transaction(write=True) as connection:
                    columns = ",".join(values)
                    placeholders = ",".join("?" for _ in values)
                    result = connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values()))
                    record = present(connection, table, find(connection, table, result.lastrowid))
                return hardware_state(table, record)
            except sqlite3.IntegrityError:
                conflict()

        def update_record(record_id: int, payload: dict):
            try:
                with database.transaction(write=True) as connection:
                    current = find(connection, table, record_id)
                    candidate = {key: current[key] for key in model.model_fields}
                    candidate.update(payload)
                    values = validate(model, candidate)
                    if table == "tools":
                        quantity = borrowed(connection, record_id)
                        if values["quantity"] < quantity:
                            conflict("总数量不能少于当前借出数量")
                        if quantity and values["cabinet_id"] != current["cabinet_id"]:
                            conflict("该工具尚有借用未归还，暂不能移动到其他柜子")
                    assignments = ",".join(f"{key}=?" for key in values)
                    connection.execute(f"UPDATE {table} SET {assignments} WHERE id=?", [*values.values(), record_id])
                    record = present(connection, table, find(connection, table, record_id))
                return hardware_state(table, record)
            except sqlite3.IntegrityError:
                conflict()

        def delete_record(record_id: int):
            try:
                with database.transaction(write=True) as connection:
                    find(connection, table, record_id)
                    if table == "tools":
                        if borrowed(connection, record_id):
                            conflict("该工具尚有借用未归还，不能删除")
                        has_history = connection.execute("SELECT 1 FROM loans WHERE tool_id=? LIMIT 1", (record_id,)).fetchone()
                        if has_history:
                            connection.execute("UPDATE tools SET archived=1 WHERE id=?", (record_id,))
                        else:
                            connection.execute("DELETE FROM tools WHERE id=?", (record_id,))
                    else:
                        connection.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
            except sqlite3.IntegrityError:
                conflict("该记录仍有关联设备、工具或借用历史，不能删除")
            return Response(status_code=204)

        router.add_api_route(f"/{table}", list_records, methods=["GET"])
        router.add_api_route(f"/{table}", create_record, methods=["POST"], status_code=201)
        router.add_api_route(f"/{table}/{{record_id}}", update_record, methods=["PATCH"])
        router.add_api_route(f"/{table}/{{record_id}}", delete_record, methods=["DELETE"])

    register("cabinets", Cabinet)
    register("gateways", Gateway)
    register("locks", Lock)
    register("tools", Tool)

    @router.post("/locks/{record_id}/actions")
    def lock_action(record_id: int, payload: Action, user=Depends(require_admin)):
        with database.transaction() as connection:
            find(connection, "locks", record_id)
        if ttlock is None:
            hardware_unavailable()
        return ttlock.lock_action(record_id, payload.action, user)

    @router.post("/gateways/{record_id}/status")
    def gateway_status(record_id: int):
        with database.transaction() as connection:
            find(connection, "gateways", record_id)
        if ttlock is None:
            hardware_unavailable()
        return ttlock.gateway_status(record_id)

    def get_loans(
        sort: Literal["borrowed_at", "returned_at", "days", "user_name", "cabinet_name", "tool_name"] = "borrowed_at",
        direction: Literal["asc", "desc"] = "desc",
        status: Literal["all", "active", "returned"] = "all",
        q: Annotated[str, Query(max_length=200)] = "",
        school_code: Annotated[str, Query(max_length=128)] = "",
        cabinet_id: Annotated[int | None, Query(gt=0)] = None,
    ):
        conditions, args = [], []
        if school_code:
            conditions.append("school_code=?")
            args.append(school_code)
        if status != "all":
            conditions.append("returned_at IS " + ("NOT NULL" if status == "returned" else "NULL"))
        if cabinet_id is not None:
            conditions.append("cabinet_id=?")
            args.append(cabinet_id)
        if q:
            conditions.append("(instr(user_name,?)>0 OR instr(school_code,?)>0 OR instr(tool_name,?)>0 OR instr(cabinet_name,?)>0 OR instr(cabinet_code,?)>0)")
            args.extend([q.strip()] * 5)
        query = "SELECT * FROM loans" + (" WHERE " + " AND ".join(conditions) if conditions else "")
        now = datetime.now(timezone.utc)
        with database.transaction() as connection:
            rows = [dict(row) for row in connection.execute(query, args)]
        for row in rows:
            start = datetime.fromisoformat(row["borrowed_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(row["returned_at"].replace("Z", "+00:00")) if row["returned_at"] else now
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            row["days"] = max(0, math.floor((end - start).total_seconds() / 86400))
            row["borrowed_at"] = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if row["returned_at"]:
                row["returned_at"] = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        # Null return dates always appear after concrete return dates.
        populated = [row for row in rows if row[sort] is not None]
        populated.sort(key=lambda row: (row[sort], row["id"]), reverse=direction == "desc")
        return populated + [row for row in rows if row[sort] is None]

    @router.get("/loans")
    def list_loans(rows=Depends(get_loans)):
        return {"items": rows}

    @router.get("/loans/export.xlsx")
    def export_loans(request: Request, rows=Depends(get_loans)):
        # get_loans validates and applies these same query parameters first.
        filters = dict(request.query_params)
        cabinet_label = "全部柜子"
        if filters.get("cabinet_id"):
            with database.transaction() as connection:
                cabinet = connection.execute("SELECT code,name FROM cabinets WHERE id=?", (int(filters["cabinet_id"]),)).fetchone()
            cabinet_label = f'{cabinet["name"]}（编号 {cabinet["code"]}）' if cabinet else f'柜子 ID {filters["cabinet_id"]}'
        return Response(content=workbook(rows, filters=filters, cabinet_label=cabinet_label), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=loans.xlsx; filename*=UTF-8''" + quote("工具借用记录.xlsx"), "Cache-Control": "no-store"})

    return router
