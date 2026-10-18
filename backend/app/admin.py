"""Cabinet-centered administration. Hardware actions deliberately fail closed."""

import base64
import binascii
import math
import sqlite3
import unicodedata
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.database import Database


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


def create_admin_router(require_admin, db_path: Path | None = None):
    database = Database(db_path)
    router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

    @router.get("/overview")
    def overview():
        with database.transaction() as connection:
            counts = {key: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for key, table in [("cabinet_count", "cabinets"), ("lock_count", "locks"), ("gateway_count", "gateways")]}
            counts["tool_count"] = connection.execute("SELECT COUNT(*) FROM tools WHERE archived=0").fetchone()[0]
            counts["active_loan_count"] = connection.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NULL").fetchone()[0]
            return {**counts, "hardware_configured": False}

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
                return {"items": [present(connection, table, row) for row in connection.execute(sql, args)]}

        def create_record(payload: dict):
            values = validate(model, payload)
            try:
                with database.transaction(write=True) as connection:
                    columns = ",".join(values)
                    placeholders = ",".join("?" for _ in values)
                    result = connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values()))
                    return present(connection, table, find(connection, table, result.lastrowid))
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
                    return present(connection, table, find(connection, table, record_id))
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
    def lock_action(record_id: int, payload: Action):
        with database.transaction() as connection:
            find(connection, "locks", record_id)
        hardware_unavailable()

    @router.post("/gateways/{record_id}/status")
    def gateway_status(record_id: int):
        with database.transaction() as connection:
            find(connection, "gateways", record_id)
        hardware_unavailable()

    def get_loans(
        sort: Literal["borrowed_at", "returned_at", "days", "user_name", "cabinet_name", "tool_name"] = "borrowed_at",
        direction: Literal["asc", "desc"] = "desc",
        status: Literal["all", "active", "returned"] = "all",
        q: Annotated[str, Query(max_length=200)] = "",
        cabinet_id: Annotated[int | None, Query(gt=0)] = None,
    ):
        conditions, args = [], []
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
    def export_loans(rows=Depends(get_loans)):
        return Response(content=workbook(rows), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=loans.xlsx; filename*=UTF-8''" + quote("工具借用记录.xlsx"), "Cache-Control": "no-store"})

    return router


def workbook(loans):
    """Produce XLSX with literal strings, including names starting with =,+,-,@."""
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = Element("worksheet", xmlns=namespace)
    columns = SubElement(sheet, "cols")
    for index, width in enumerate([22, 24, 28, 24, 10, 25, 25, 14, 12], 1):
        SubElement(columns, "col", min=str(index), max=str(index), width=str(width), customWidth="1")
    data = SubElement(sheet, "sheetData")
    rows = [["人员", "学校人员编号", "柜子", "物品", "数量", "借用时间 (UTC)", "归还时间 (UTC)", "借用天数", "状态"]]
    rows.extend([[r["user_name"], r["school_code"], f'{r["cabinet_code"]} · {r["cabinet_name"]}', r["tool_name"], r["quantity"], r["borrowed_at"], r["returned_at"] or "", r["days"], "已归还" if r["returned_at"] else "借用中"] for r in loans])
    for number, values in enumerate(rows, 1):
        row = SubElement(data, "row", r=str(number))
        for index, value in enumerate(values):
            cell = SubElement(row, "c", r=f"{chr(65 + index)}{number}", t="n" if isinstance(value, int) else "inlineStr")
            if isinstance(value, int):
                SubElement(cell, "v").text = str(value)
            else:
                text = SubElement(SubElement(cell, "is"), "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
                # XML 1.0 disallows control characters, including user-entered ones.
                text.text = "".join(c for c in str(value) if c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)
    SubElement(sheet, "autoFilter", ref=f"A1:I{len(rows)}")
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{namespace}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="借用记录" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", tostring(sheet, encoding="utf-8", xml_declaration=True))
    return output.getvalue()
