import base64
from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.admin import create_admin_router
from app.database import Database


@pytest.fixture
def environment(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    app = FastAPI()
    app.include_router(create_admin_router(lambda: None, path))
    database = Database(path)
    # Keep CRUD fixtures independent of the separately tested initial catalogue.
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM tools")
    return TestClient(app), database


def create(client, kind, **values):
    response = client.post(f"/api/admin/{kind}", json=values)
    assert response.status_code == 201, response.text
    return response.json()


def cabinet(client, code="A"):
    existing = next((row for row in client.get("/api/admin/cabinets").json()["items"] if row["code"] == code), None)
    if existing:
        response = client.patch(f'/api/admin/cabinets/{existing["id"]}', json={"name": "一号工具柜", "group_name": "正雅苑", "location": "一楼"})
        assert response.status_code == 200
        return response.json()
    return create(client, "cabinets", code=code, name="一号工具柜", group_name="正雅苑", location="一楼")


def loan(database, tool, box, *, name="Alice", returned=False, days=3):
    now = datetime.now(timezone.utc)
    with database.transaction(write=True) as connection:
        result = connection.execute(
            "INSERT INTO loans (user_name,school_code,cabinet_id,cabinet_name,cabinet_code,tool_id,tool_name,quantity,borrowed_at,returned_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, "00123", box["id"], box["name"], box["code"], tool["id"], tool["name"], 2, (now - timedelta(days=days)).isoformat(), now.isoformat() if returned else None),
        )
        return result.lastrowid


def test_empty_database_and_durability(environment):
    client, database = environment
    assert client.get("/api/admin/overview").json() == {"cabinet_count": 2, "lock_count": 0, "gateway_count": 0, "tool_count": 0, "active_loan_count": 0, "user_count": 0, "average_borrow_ratio": 0, "borrow_visit_count": 0, "borrow_visits_per_day": 0, "today_borrow_count": 0, "today_return_count": 0, "hardware_configured": False}
    assert [row["code"] for row in client.get("/api/admin/cabinets").json()["items"]] == ["A", "B"]
    for kind in ["locks", "gateways", "tools", "loans"]:
        assert client.get(f"/api/admin/{kind}").json() == {"items": []}
    cabinet(client)
    other = FastAPI()
    other.include_router(create_admin_router(lambda: None, database.path))
    assert TestClient(other).get("/api/admin/cabinets").json()["items"][0]["code"] == "A"


@pytest.mark.parametrize("status", [401, 403])
def test_all_endpoints_require_admin(tmp_path, status):
    def denied():
        raise HTTPException(status, "Denied")

    app = FastAPI()
    app.include_router(create_admin_router(denied, tmp_path / "denied.sqlite3"))
    client = TestClient(app)
    for method, path in [("get", "/overview"), ("get", "/cabinets"), ("post", "/cabinets"), ("patch", "/cabinets/1"), ("delete", "/cabinets/1"), ("get", "/tools"), ("post", "/locks/1/actions"), ("post", "/gateways/1/status"), ("get", "/loans"), ("get", "/loans/export.xlsx")]:
        assert getattr(client, method)("/api/admin" + path).status_code == status


def test_relationships_unique_fields_and_multiple_locks(environment):
    client, _ = environment
    box = cabinet(client)
    gateway = create(client, "gateways", code="GW1", name="G2", provider_gateway_id="991")
    locks = [create(client, "locks", code=f"L{i}", name="柜门锁", cabinet_id=box["id"], gateway_id=gateway["id"], provider_lock_id=str(i)) for i in range(2)]
    assert all(item["status"] == "unknown" for item in locks)
    assert client.get("/api/admin/cabinets").json()["items"][0]["lock_count"] == 2
    assert client.delete(f'/api/admin/cabinets/{box["id"]}').status_code == 409
    assert client.delete(f'/api/admin/gateways/{gateway["id"]}').status_code == 409
    assert client.post("/api/admin/locks", json={"code": "BAD", "name": "Bad", "cabinet_id": 999}).status_code == 409
    assert client.post("/api/admin/cabinets", json={"code": "A", "name": "Duplicate"}).status_code == 409
    assert client.post("/api/admin/locks", json={"code": "OTHER", "name": "Duplicate external", "provider_lock_id": "0"}).status_code == 409
    for item in locks:
        assert client.patch(f'/api/admin/locks/{item["id"]}', json={"gateway_id": None, "cabinet_id": None}).status_code == 200
    assert client.delete(f'/api/admin/gateways/{gateway["id"]}').status_code == 204
    assert client.delete(f'/api/admin/cabinets/{box["id"]}').status_code == 204
    assert client.delete("/api/admin/locks/999").status_code == 404


def test_partial_updates_validate_final_values(environment):
    client, _ = environment
    box = cabinet(client)
    assert client.patch(f'/api/admin/cabinets/{box["id"]}', json={"name": "  改名  "}).json()["name"] == "改名"
    for payload in [{"name": " "}, {"code": None}, {"id": 88}, {"arbitrary": 1}, {"notes": "a" * 2001}]:
        assert client.patch(f'/api/admin/cabinets/{box["id"]}', json=payload).status_code == 422
    assert client.patch("/api/admin/cabinets/999", json={"name": "Missing"}).status_code == 404
    assert client.get("/api/admin/cabinets").json()["items"][0]["name"] == "改名"


def test_delete_unused_tool_allows_empty_cabinet_removal(environment):
    client, database = environment
    box = cabinet(client)
    tool = create(client, "tools", cabinet_id=box["id"], name="Unused", quantity=0)
    assert client.delete(f'/api/admin/tools/{tool["id"]}').status_code == 204
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tools").fetchone()[0] == 0
    assert client.delete(f'/api/admin/cabinets/{box["id"]}').status_code == 204


def test_hardware_actions_fail_closed_even_with_platform_ids(environment):
    client, _ = environment
    item = create(client, "locks", code="L", name="Lock", provider_lock_id="123")
    gateway = create(client, "gateways", code="G", name="Gateway", provider_gateway_id="321")
    for action in ["unlock", "lock", "status"]:
        response = client.post(f'/api/admin/locks/{item["id"]}/actions', json={"action": action})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "HARDWARE_NOT_CONFIGURED"
    assert client.post(f'/api/admin/gateways/{gateway["id"]}/status').status_code == 503
    assert client.post("/api/admin/locks/999/actions", json={"action": "unlock"}).status_code == 404
    assert client.post("/api/admin/gateways/999/status").status_code == 404
    assert client.post(f'/api/admin/locks/{item["id"]}/actions', json={"action": "reset"}).status_code == 422


def test_inventory_and_preserved_loan_history(environment):
    client, database = environment
    box, second = cabinet(client), cabinet(client, "B")
    tool = create(client, "tools", cabinet_id=box["id"], name="螺丝刀", quantity=5, icon="screwdriver")
    loan_id = loan(database, tool, box)
    loan(database, tool, box, name="Returned", returned=True)
    item = client.get("/api/admin/tools", params={"cabinet_id": box["id"]}).json()["items"][0]
    assert (item["borrowed_quantity"], item["available_quantity"]) == (2, 3)
    assert len(item["borrowers"]) == 1
    assert item["borrowers"][0]["name"] == "Alice"
    assert client.get("/api/admin/tools", params={"cabinet_id": second["id"]}).json()["items"] == []
    for payload in [{"quantity": 1}, {"cabinet_id": second["id"]}]:
        assert client.patch(f'/api/admin/tools/{tool["id"]}', json=payload).status_code == 409
    assert client.patch(f'/api/admin/tools/{tool["id"]}', json={"quantity": 2}).status_code == 200
    assert client.delete(f'/api/admin/tools/{tool["id"]}').status_code == 409
    with database.transaction(write=True) as connection:
        connection.execute("UPDATE loans SET returned_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), loan_id))
    assert client.delete(f'/api/admin/tools/{tool["id"]}').status_code == 204
    assert client.get("/api/admin/tools").json()["items"] == []
    assert client.get("/api/admin/overview").json()["tool_count"] == 0
    assert len(client.get("/api/admin/loans").json()["items"]) == 2
    assert client.delete(f'/api/admin/cabinets/{box["id"]}').status_code == 409
    assert client.patch(f'/api/admin/tools/{tool["id"]}', json={"name": "No"}).status_code == 404


def test_loans_filter_sort_export_literal_formula(environment):
    client, database = environment
    box = cabinet(client)
    tool = create(client, "tools", cabinet_id=box["id"], name="Hammer", quantity=8)
    loan(database, tool, box, name="=HYPERLINK(\"https://example.invalid\")", days=5)
    loan(database, tool, box, name="Zelda", returned=True, days=2)
    loan(database, tool, box, name="Alice", days=1)
    response = client.get("/api/admin/loans", params={"sort": "days", "direction": "asc"})
    assert [row["days"] for row in response.json()["items"]] == [1, 2, 5]
    assert all(row["borrowed_at"].endswith("Z") for row in response.json()["items"])
    assert len(client.get("/api/admin/loans", params={"status": "active"}).json()["items"]) == 2
    assert len(client.get("/api/admin/loans", params={"status": "returned"}).json()["items"]) == 1
    assert client.get("/api/admin/loans", params={"q": "Alice"}).json()["items"][0]["user_name"] == "Alice"
    assert client.get("/api/admin/loans", params={"q": "%"}).json()["items"] == []
    assert client.get("/api/admin/loans", params={"sort": "user_name;DROP TABLE tools"}).status_code == 422
    assert client.get("/api/admin/loans", params={"cabinet_id": 999}).json()["items"] == []
    export = client.get("/api/admin/loans/export.xlsx", params={"status": "active", "sort": "days", "direction": "desc"})
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "filename*=UTF-8''" in export.headers["content-disposition"]
    with ZipFile(BytesIO(export.content)) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        assert sheet.findall(".//s:f", namespace) == []
        rows = sheet.findall("s:sheetData/s:row", namespace)
        assert len(rows) == 3
        assert rows[1][0].attrib["t"] == "inlineStr"
        assert rows[1][0].find("s:is/s:t", namespace).text.startswith("=HYPERLINK")
        assert rows[1][1].find("s:is/s:t", namespace).text == "00123"


def test_icon_validation_and_quantity(environment):
    client, _ = environment
    box = cabinet(client)
    payload = {"cabinet_id": box["id"], "name": "Icon", "quantity": 2}
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"image").decode()
    assert client.post("/api/admin/tools", json={**payload, "icon_data": png}).status_code == 201
    for bad in ["data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,!!!!", "data:image/png;base64," + base64.b64encode(b"Not PNG").decode(), "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * (512 * 1024)).decode()]:
        response = client.post("/api/admin/tools", json={**payload, "icon_data": bad})
        assert response.status_code == 422
        assert bad not in response.text
    for bad in [-1, 1.5, "3", True]:
        assert client.post("/api/admin/tools", json={**payload, "quantity": bad}).status_code == 422
    assert client.post("/api/admin/tools", json={**payload, "icon": "unknown"}).status_code == 422


def test_tool_units_and_translations(environment):
    client, _ = environment
    box = cabinet(client)
    tool = create(client, "tools", cabinet_id=box["id"], name="卷尺")
    assert tool["unit"] == "件"
    fields = {"unit": "把", "unit_en": "pcs", "name_en": "Tape measure", "description_en": "Five metres"}
    response = client.patch(f'/api/admin/tools/{tool["id"]}', json=fields)
    assert response.status_code == 200
    assert all(response.json()[key] == value for key, value in fields.items())
    assert all(client.get("/api/admin/tools").json()["items"][0][key] == value for key, value in fields.items())
    for invalid in ("", "   ", "x" * 17, None):
        assert client.patch(f'/api/admin/tools/{tool["id"]}', json={"unit": invalid}).status_code == 422
        assert client.post("/api/admin/tools", json={"cabinet_id": box["id"], "name": "Bad unit", "unit": invalid}).status_code == 422


def test_cabinet_codes_remain_single_scannable_path_segments(environment):
    client, _ = environment
    box = cabinet(client)
    for code in ("A/1", "A\\1", "A\x00B", "A\nB", "A\x7fB", "A\u200bB"):
        assert client.post("/api/admin/cabinets", json={"code": code, "name": "Invalid"}).status_code == 422
        assert client.patch(f'/api/admin/cabinets/{box["id"]}', json={"code": code}).status_code == 422
    assert create(client, "cabinets", code="正雅-C_01", name="New cabinet")["code"] == "正雅-C_01"
    # Other device identifiers are not URLs and retain their existing contract.
    assert create(client, "gateways", code="G/1", name="Gateway")["code"] == "G/1"
