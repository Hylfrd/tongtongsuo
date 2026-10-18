import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.cabinets import create_cabinet_router
from app.database import Database
from app.main import create_app


@pytest.fixture
def environment(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    database = Database(path)
    identity = {"name": "Student", "school_code": "001"}
    application = FastAPI()
    application.include_router(create_cabinet_router(lambda: identity, path))
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM tools")
        ids = {row["code"]: row["id"] for row in connection.execute("SELECT id,code FROM cabinets")}
        connection.execute("UPDATE cabinets SET notes='PRIVATE' WHERE code='A'")
        connection.execute("INSERT INTO cabinets (code,name) VALUES ('C-03','Third cabinet')")
        for index, code in enumerate(("A", "B", "A"), 1):
            connection.execute(
                "INSERT INTO tools (id,cabinet_id,name,quantity,notes,unit,name_en,unit_en,archived) VALUES (?,?,?,?,?,?,?,?,?)",
                (index, ids[code], f"Tool {index}", 8, "PRIVATE", "把", "Screwdriver", "pcs", int(index == 3)),
            )
        for school_code, code, tool_id, returned in (("001", "A", 1, None), ("002", "A", 1, None), ("001", "B", 2, None), ("001", "A", 1, "2026-10-19T12:00:00Z")):
            connection.execute(
                "INSERT INTO loans (user_name,school_code,cabinet_id,cabinet_name,cabinet_code,tool_id,tool_name,quantity,borrowed_at,returned_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("PRIVATE NAME", school_code, ids[code], code, code, tool_id, "Saved tool name", 2, "2026-10-18T12:00:00Z", returned),
            )
    return TestClient(application), database, identity


def test_cabinet_projection_and_dynamic_codes(environment):
    client, _, _ = environment
    response = client.get("/api/cabinets/A")
    assert response.status_code == 200
    assert set(response.json()) == {"id", "code", "name", "group_name", "location"}
    assert response.json()["name"] == "A区"
    assert client.get("/api/cabinets/B").json()["name"] == "B区"
    assert client.get("/api/cabinets/C-03").json()["name"] == "Third cabinet"
    assert client.get("/api/cabinets/missing").status_code == 404
    assert client.get("/api/cabinets/' OR 1=1 --").status_code == 404


def test_tools_are_scoped_and_do_not_disclose_borrowers(environment):
    client, _, _ = environment
    response = client.get("/api/cabinets/A/tools")
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == 1
    assert items[0]["available_quantity"] == 4
    assert items[0]["unit"] == "把"
    assert items[0]["name_en"] == "Screwdriver"
    assert set(items[0]) == {"id", "name", "name_en", "description", "description_en", "unit", "unit_en", "icon", "icon_data", "available_quantity"}
    assert "PRIVATE" not in response.text
    assert client.get("/api/cabinets/B/tools").json()["items"][0]["id"] == 2
    assert client.get("/api/cabinets/C-03/tools").json() == {"items": []}


def test_own_loans_are_filtered_by_identity_cabinet_and_return_status(environment):
    client, _, identity = environment
    response = client.get("/api/cabinets/A/loans/me?school_code=002")
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == 1
    assert items[0]["tool_name"] == "Saved tool name"
    assert items[0]["quantity"] == 2
    assert items[0]["unit"] == "把"
    assert "PRIVATE" not in response.text
    assert "school_code" not in items[0]
    assert client.get("/api/cabinets/B/loans/me").json()["items"][0]["tool_id"] == 2
    identity["school_code"] = ""
    assert client.get("/api/cabinets/A/loans/me").json() == {"items": []}


def test_unlock_never_creates_loans_or_mutates_inventory(environment):
    client, database, _ = environment
    for payload in (None, {"selection": [{"id": 1, "quantity": 2}], "authenticated": True}):
        response = client.post("/api/cabinets/A/unlock", json=payload)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "HARDWARE_NOT_CONFIGURED"
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM loans").fetchone()[0] == 4
        assert connection.execute("SELECT quantity FROM tools WHERE id=1").fetchone()[0] == 8


@pytest.mark.parametrize("route,method", [("/tools", "get"), ("/loans/me", "get"), ("/unlock", "post")])
def test_protected_routes_and_unknown_cabinets(environment, tmp_path, route, method):
    client, _, _ = environment
    assert getattr(client, method)("/api/cabinets/missing" + route).status_code == 404

    def denied():
        raise HTTPException(401, "No session")

    application = FastAPI()
    application.include_router(create_cabinet_router(denied, tmp_path / "denied.sqlite3"))
    anonymous = TestClient(application)
    assert getattr(anonymous, method)("/api/cabinets/A" + route).status_code == 401
    assert anonymous.get("/api/cabinets/A").status_code == 200


def test_app_registers_cabinet_routes_with_real_auth_and_no_store(tmp_path):
    client = TestClient(create_app(db_path=tmp_path / "app.sqlite3"))
    response = client.get("/api/cabinets/A")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    for suffix in ("/tools", "/loans/me"):
        assert client.get("/api/cabinets/A" + suffix).status_code == 401
    assert client.post("/api/cabinets/A/unlock").status_code == 401

def cloud_cabinet_client(tmp_path, *, fail_lock=None):
    import httpx
    from urllib.parse import parse_qs
    from app.ttlock import TTLockService
    writes = []
    def handler(request):
        path = request.url.path
        if path == '/oauth2/token':
            return httpx.Response(200, json={'access_token': 'mock-token', 'refresh_token': 'mock-refresh', 'expires_in': 3600})
        if path == '/v3/lock/list':
            return httpx.Response(200, json={'list': [{'lockId': id, 'lockAlias': f'Lock {id}', 'featureValue': '500'} for id in (11, 12, 13)], 'pages': 1})
        if path == '/v3/gateway/list':
            return httpx.Response(200, json={'list': [], 'pages': 1})
        if path == '/v3/lock/unlock':
            provider_id = parse_qs(request.content.decode())['lockId'][0]
            writes.append(provider_id)
            if provider_id == fail_lock:
                raise httpx.ReadTimeout('Mock timeout', request=request)
            return httpx.Response(200, json={'errcode': 0})
        raise AssertionError(path)
    path = tmp_path / 'cloud.sqlite3'
    service = TTLockService(path, transport=httpx.MockTransport(handler))
    service.save_settings({'client_id': 'fixture', 'client_secret': 'fixture-secret'})
    service.connect('fixture-account', 'fixture-password')
    service.sync()
    with service.database.transaction(write=True) as connection:
        ids = {row['code']: row['id'] for row in connection.execute('SELECT id,code FROM cabinets')}
        connection.execute("UPDATE locks SET cabinet_id=? WHERE provider_lock_id IN ('11','12')", (ids['A'],))
        connection.execute("UPDATE locks SET cabinet_id=? WHERE provider_lock_id='13'", (ids['B'],))
    identity = {'name': 'Student', 'school_code': '001'}
    app = FastAPI()
    app.include_router(create_cabinet_router(lambda: identity, path, service))
    return TestClient(app), service, writes, identity


def test_assigned_cabinet_unlocks_only_server_selected_locks_without_loans(tmp_path):
    client, service, writes, identity = cloud_cabinet_client(tmp_path)
    response = client.post('/api/cabinets/A/unlock', json={'provider_lock_id': '13', 'cabinet_id': 2})
    assert response.status_code == 200
    assert response.json()['status'] == 'unlocked'
    assert response.json()['completed_locks'] == 2
    assert writes == ['11', '12']
    with service.database.transaction() as connection:
        assert connection.execute('SELECT COUNT(*) FROM loans').fetchone()[0] == 0
        assert connection.execute('SELECT actor FROM ttlock_commands ORDER BY id LIMIT 1').fetchone()[0] == 'Student (001)'
    identity.update(name='管理员', role='admin', school_code=None)
    assert client.post('/api/cabinets/B/unlock').status_code == 200
    assert writes == ['11', '12', '13']


def test_cabinet_unlock_preflights_all_locks_and_never_replays_partial_timeout(tmp_path):
    client, service, writes, _ = cloud_cabinet_client(tmp_path, fail_lock='12')
    response = client.post('/api/cabinets/A/unlock')
    assert response.status_code == 504
    detail = response.json()['detail']
    assert detail['code'] == 'CABINET_UNLOCK_UNKNOWN'
    assert detail['completed_locks'] == 1
    assert detail['total_locks'] == 2
    assert writes == ['11', '12']
    with service.database.transaction(write=True) as connection:
        # Let the first lock's cooldown elapse without sleeping. The second
        # uncertain command must still block the entire cabinet before a write.
        connection.execute("UPDATE ttlock_commands SET started_at=0,finished_at='2000-01-01T00:00:00+00:00'")
    assert client.post('/api/cabinets/A/unlock').status_code == 409
    assert writes == ['11', '12']
    with service.database.transaction(write=True) as connection:
        connection.execute("UPDATE ttlock_devices SET details='{}',available=0 WHERE kind='locks' AND provider_id='13'")
    assert client.post('/api/cabinets/B/unlock').status_code == 409
    assert writes == ['11', '12']
    with service.database.transaction(write=True) as connection:
        connection.execute("UPDATE locks SET cabinet_id=NULL WHERE cabinet_id=(SELECT id FROM cabinets WHERE code='B')")
    assert client.post('/api/cabinets/B/unlock').json()['detail']['code'] == 'CABINET_HAS_NO_LOCKS'
