from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import AuthManager
from app.admin_password import create_password_hash
from app.member_excel import NS, parse_members, template_workbook
from app.members import MembersStore, create_members_router
from app.request_limits import AdminBodyLimitMiddleware
from app.school_sso import SchoolIdentity

ORIGIN = "https://bnbu.hmcl-helper.cn"
HEADERS = {"Origin": ORIGIN}


def roster_file(rows, formula=False):
    original = ZipFile(BytesIO(template_workbook()))
    sheet = ET.fromstring(original.read("xl/worksheets/sheet1.xml"))
    data = sheet.find("{" + NS + "}sheetData")
    data.remove(data[1])
    for i, values in enumerate(rows, 2):
        row = ET.SubElement(data, "{" + NS + "}row", r=str(i))
        for j, value in enumerate(values):
            cell = ET.SubElement(row, "{" + NS + "}c", r=f"{chr(65+j)}{i}", t="inlineStr")
            ET.SubElement(ET.SubElement(cell, "{" + NS + "}is"), "{" + NS + "}t").text = value
            if formula:
                ET.SubElement(cell, "{" + NS + "}f").text = "1+1"
    target = BytesIO()
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name in original.namelist():
            archive.writestr(name, ET.tostring(sheet) if name == "xl/worksheets/sheet1.xml" else original.read(name))
    return target.getvalue()


def test_login_modes_member_isolation_and_import_atomicity(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", create_password_hash("admin"))
    calls = []

    async def school(username, password):
        calls.append(username)
        return SchoolIdentity("SSO User", "00123")

    path = tmp_path / "members.sqlite3"
    auth = AuthManager(school, path)
    app = FastAPI()
    app.add_middleware(AdminBodyLimitMiddleware)
    app.include_router(auth.router)
    app.include_router(create_members_router(auth.require_admin, auth.members))
    admin = TestClient(app, base_url=ORIGIN)
    pupil = TestClient(app, base_url=ORIGIN)
    assert pupil.get("/api/auth/mode").json() == {"mode": "sso"}
    for endpoint in ("/members?source=roster", "/login-settings", "/members/template.xlsx"):
        assert pupil.get("/api/admin" + endpoint).status_code == 401
    assert pupil.post("/api/auth/login", headers=HEADERS, json={"username": "00123", "password": "school"}).status_code == 200
    assert pupil.get("/api/admin/members?source=sso").status_code == 403
    assert admin.post("/api/auth/login", headers=HEADERS, json={"username": "admin", "password": "admin"}).status_code == 200
    assert admin.get("/api/admin/members?source=sso").json()["total"] == 1
    assert admin.get("/api/admin/members?source=roster").json()["total"] == 0

    def upload(rows, **kwargs):
        return admin.post("/api/admin/members/import", headers={**HEADERS, "Content-Type": "application/octet-stream"}, content=roster_file(rows, **kwargs))

    assert upload([("JANE ANN DOE", "00123", "0102")]).json() == {"added": 1, "updated": 0, "total": 1}
    assert upload([("Changed", "00123", "1111"), ("New", "00001", "")]).status_code == 422
    assert upload([("X", "00001", "0101"), ("Y", "００００１", "0102")]).status_code == 422
    assert upload([("X", "00002", "0101")], formula=True).status_code == 422
    listing = admin.get("/api/admin/members?source=roster&q=jane").json()
    assert listing["total"] == 1 and listing["items"][0]["name"] == "JANE ANN DOE"
    assert listing["items"][0]["first_login_at"] is None
    assert admin.put("/api/admin/login-settings", json={"mode": "roster"}).status_code == 403
    assert admin.put("/api/admin/login-settings", headers=HEADERS, json={"mode": "roster"}).status_code == 200
    assert MembersStore(path).mode() == "roster"
    assert pupil.post("/api/auth/login", headers=HEADERS, json={"username": "00123", "password": "school"}).status_code == 409
    assert calls == ["00123"]
    credentials = {"name": "jane ann doe", "school_code": "00123", "room_number": "0102"}
    assert pupil.post("/api/auth/login", headers=HEADERS, json={**credentials, "name": "JANEANNDOE"}).status_code == 401
    assert pupil.post("/api/auth/login", headers=HEADERS, json={**credentials, "room_number": "wrong"}).status_code == 401
    assert pupil.post("/api/auth/login", headers=HEADERS, json=credentials).json() == {"user": {"name": "JANE ANN DOE", "school_code": "00123"}}
    assert admin.get("/api/admin/members?source=roster").json()["items"][0]["last_login_at"] is not None
    assert admin.post("/api/auth/login", headers=HEADERS, json={"username": "admin", "password": "admin"}).status_code == 200
    # Roster failure uses the same cooldown regardless of which field was wrong.
    pupil.post("/api/auth/logout", headers=HEADERS)
    for _ in range(3):
        response = pupil.post("/api/auth/login", headers=HEADERS, json={**credentials, "name": "wrong"})
        assert response.status_code == 401
    assert response.headers["retry-after"] == "30"
    assert pupil.post("/api/auth/login", headers=HEADERS, json=credentials).status_code == 429


def test_excel_numeric_text_ids_and_hostile_xml():
    raw = roster_file([("测试成员", "00123", "0102")])
    assert parse_members(raw)[0][1] == ("测试成员", "00123", "0102")
    archive = ZipFile(BytesIO(raw))
    sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    cell = sheet.find(f".//{{{NS}}}c[@r='B2']")
    cell.clear()
    cell.attrib.update(r="B2", t="n", s="12")
    ET.SubElement(cell, "{" + NS + "}v").text = "123"
    styles = ET.fromstring(archive.read("xl/styles.xml"))
    ET.SubElement(styles.find("{" + NS + "}numFmts"), "{" + NS + "}numFmt", numFmtId="165", formatCode="00000")
    ET.SubElement(styles.find("{" + NS + "}cellXfs"), "{" + NS + "}xf", numFmtId="165")
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as result:
        for name in archive.namelist():
            result.writestr(name, ET.tostring(sheet) if name.endswith("sheet1.xml") else ET.tostring(styles) if name.endswith("styles.xml") else archive.read(name))
    assert parse_members(output.getvalue())[0][1][1] == "00123"
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as result:
        result.writestr("xl/workbook.xml", b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///secret">]><x>&x;</x>')
    import pytest
    with pytest.raises(ValueError, match="XML"):
        parse_members(output.getvalue())
