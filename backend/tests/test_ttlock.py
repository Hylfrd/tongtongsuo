import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.ttlock import TTLockService, create_ttlock_router


def service(tmp_path, handler):
    instance = TTLockService(tmp_path / 'devices.sqlite3', transport=httpx.MockTransport(handler))
    instance.save_settings({'client_id': 'application', 'client_secret': 'private-secret'})
    return instance


def token():
    return {'access_token': 'private-access', 'refresh_token': 'private-refresh', 'expires_in': 3600}


def cloud(request):
    if request.url.path == '/oauth2/token':
        return httpx.Response(200, json=token())
    if request.url.path == '/v3/lock/list':
        return httpx.Response(200, json={'list': [{'lockId': 11, 'lockAlias': '云名称', 'lockData': 'NEVER_STORE', 'featureValue': '500'}], 'pages': 1})
    if request.url.path == '/v3/gateway/list':
        return httpx.Response(200, json={'list': [{'gatewayId': 21, 'gatewayName': 'G2'}, {'gatewayId': 22, 'gatewayName': 'G2备份'}], 'pages': 1})
    if request.url.path == '/v3/gateway/listLock':
        return httpx.Response(200, json={'list': [{'lockId': 11}]})
    raise AssertionError(request.url.path)


def test_secrets_remain_encrypted_and_routes_require_admin(tmp_path):
    calls = []

    import hashlib
    def checked(request):
        assert request.url.host == 'cnapi.ttlock.com'
        values = parse_qs(request.content.decode())
        assert values['password'] == [hashlib.md5(b'local-test-password').hexdigest()]
        calls.append(request)
        return httpx.Response(200, json=token())
    instance = service(tmp_path, checked)
    instance.transport = httpx.MockTransport(checked)
    public = instance.connect('+8613800000000', 'local-test-password')
    assert public['connected'] is True
    with instance.database.transaction() as connection:
        row = dict(connection.execute('SELECT * FROM ttlock_settings').fetchone())
    text = json.dumps(row) + json.dumps(public)
    for secret in ('private-secret', 'private-access', 'private-refresh', 'local-test-password'):
        assert secret not in text
    assert instance.key_path.exists()

    def denied():
        raise HTTPException(401, '请登录')
    app = FastAPI()
    app.include_router(create_ttlock_router(denied, instance))
    client = TestClient(app)
    for method, path in [('GET', 'settings'), ('PUT', 'settings'), ('POST', 'connect'), ('POST', 'sync'), ('GET', 'locks/1/settings'), ('PATCH', 'locks/1/settings')]:
        assert client.request(method, '/api/admin/ttlock/' + path, json={}).status_code == 401
    instance.save_settings({'client_id': 'another-application'})
    assert instance.public()['connected'] is False
    assert instance.public()['client_secret_configured'] is False


def test_refresh_and_full_sync_preserve_local_management_and_cloud_links(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == '/v3/lock/list':
            page = int(request.url.params['pageNo'])
            return httpx.Response(200, json={'list': [{'lockId': page + 10, 'lockAlias': '云名称', 'lockData': 'NEVER_STORE', 'featureValue': '500'}], 'pages': 2})
        return cloud(request)
    instance = service(tmp_path, handler)
    instance.connect('cloud-account', 'password')
    with instance.database.transaction(write=True) as connection:
        connection.execute("INSERT INTO locks(code,name,provider_lock_id,cabinet_id,notes) VALUES('A-L1','自定义名','11',1,'本地备注')")
        connection.execute('UPDATE ttlock_settings SET expires_at=1')
    result = instance.sync()
    assert result == {'locks_added': 1, 'locks_updated': 1, 'gateways_added': 2, 'gateways_updated': 0}
    refresh = [request for request in calls if b'grant_type=refresh_token' in request.content]
    assert len(refresh) == 1
    with instance.database.transaction() as connection:
        lock = dict(connection.execute("SELECT * FROM locks WHERE provider_lock_id='11'").fetchone())
        details = connection.execute('SELECT details FROM ttlock_devices').fetchall()
    assert (lock['name'], lock['cabinet_id'], lock['notes'], lock['gateway_id']) == ('自定义名', 1, '本地备注', None)
    assert 'NEVER_STORE' not in str([tuple(row) for row in details])
    snapshot = instance.device_snapshot('locks', lock['id'])
    assert snapshot['status'] == 'unknown'
    assert len(snapshot['cloud_gateways']) == 2
    # A failed later pagination/association request cannot erase the saved inventory or associations.
    instance.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'errcode': -3002}))
    with pytest.raises(HTTPException):
        instance.sync()
    assert instance.device_snapshot('locks', lock['id']) == snapshot


def test_unknown_command_is_not_retried_and_requires_explicit_status(tmp_path):
    commands = []
    def handler(request):
        if request.url.path == '/v3/lock/unlock':
            commands.append(request)
            raise httpx.ReadTimeout('secret server response', request=request)
        if request.url.path == '/v3/lock/queryOpenState':
            return httpx.Response(200, json={'state': 1})
        return cloud(request)
    instance = service(tmp_path, handler)
    instance.connect('cloud-account', 'password')
    instance.sync()
    with instance.database.transaction() as connection:
        lock_id = connection.execute('SELECT id FROM locks').fetchone()[0]
    with pytest.raises(HTTPException) as failure:
        instance.lock_action(lock_id, 'unlock', {'name': 'Admin'})
    assert failure.value.status_code == 504
    assert failure.value.detail['code'] == 'COMMAND_RESULT_UNKNOWN'
    assert 'secret' not in str(failure.value.detail)
    with pytest.raises(HTTPException) as duplicate:
        instance.lock_action(lock_id, 'unlock', 'Admin')
    assert duplicate.value.status_code == 409
    assert len(commands) == 1
    assert instance.device_snapshot('locks', lock_id)['status'] == 'unknown'
    assert instance.lock_action(lock_id, 'status', 'Admin')['status'] == 'unlocked'
    with instance.database.transaction() as connection:
        audit = dict(connection.execute('SELECT * FROM ttlock_commands').fetchone())
    assert audit['action'] == 'unlock'
    assert audit['outcome'] == 'state_checked'
    assert audit['error_code'] == 'COMMAND_RESULT_UNKNOWN'
    # A syntactically valid JSON response is not evidence that a command succeeded.
    for index, malformed in enumerate(({}, {'errcode': None}, {'errcode': False})):
        def invalid_response(request):
            if request.url.path == '/v3/lock/unlock':
                return httpx.Response(200, json=malformed)
            return cloud(request)
        other = service(tmp_path / str(index), invalid_response)
        other.connect('cloud-account', 'password')
        other.sync()
        with pytest.raises(HTTPException) as invalid:
            other.lock_action(1, 'unlock', 'Admin')
        assert invalid.value.detail['code'] == 'COMMAND_RESULT_UNKNOWN'
        assert other.device_snapshot('locks', 1)['status'] == 'unknown'


def test_provider_errors_and_validation_never_echo_credentials(tmp_path):
    instance = service(tmp_path, lambda request: httpx.Response(200, json={'errcode': 10007, 'errmsg': 'private-secret private-access'}))
    app = FastAPI()
    app.include_router(create_ttlock_router(lambda: None, instance))
    client = TestClient(app)
    response = client.post('/api/admin/ttlock/connect', json={'username': 'cloud', 'password': 'private-secret'})
    assert response.status_code == 502
    assert 'private-secret' not in response.text
    response = client.put('/api/admin/ttlock/settings', json={'client_secret': {'private-secret': 'value'}})
    assert response.status_code == 422
    assert 'private-secret' not in response.text
    assert instance.public()['connected'] is False

def configured_lock(tmp_path, *, fail_setting=None):
    bits = (4, 15, 22, 23, 28, 29, 30, 36, 43, 46, 51)
    state = {'lockId': 11, 'lockName': 'factory-name', 'lockAlias': 'A 柜锁', 'lockMac': 'AA:BB',
             'featureValue': format(sum(1 << bit for bit in bits), 'x'), 'autoLockTime': 5,
             'lockSound': 1, 'soundVolume': 3, 'openDirection': 1, 'privacyLock': 2,
             'tamperAlert': 2, 'resetButton': 1, 'modelNum': 'MODEL', 'electricQuantity': 80,
             'noKeyPwd': 'DO-NOT-EXPOSE', 'lockData': 'DO-NOT-EXPOSE'}
    passage = {'passageMode': 2, 'autoUnlock': 2, 'cyclicConfig': []}
    writes = []
    def handler(request):
        path = request.url.path
        if path == '/v3/lock/detail':
            return httpx.Response(200, json=state)
        if path == '/v3/lock/getPassageModeConfiguration':
            return httpx.Response(200, json=passage)
        if path in {'/v3/lock/setAutoLockTime', '/v3/lock/updateSetting', '/v3/lock/configurePassageMode'}:
            form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
            writes.append((path, form))
            if path == '/v3/lock/updateSetting':
                assert form['changeType'] == '2'
                setting_type = int(form['type'])
                if setting_type == fail_setting:
                    raise httpx.ReadTimeout('private upstream error', request=request)
                state[{2: 'privacyLock', 3: 'tamperAlert', 4: 'resetButton', 6: 'lockSound', 7: 'openDirection', 8: 'soundVolume'}[setting_type]] = int(form['value'])
            elif path == '/v3/lock/setAutoLockTime':
                assert form['type'] == '2'
                state['autoLockTime'] = int(form['seconds'])
            else:
                assert form['type'] == '2'
                passage.update(passageMode=int(form['passageMode']), autoUnlock=int(form['autoUnlock']), cyclicConfig=json.loads(form['cyclicConfig']))
            return httpx.Response(200, json={'errcode': 0})
        return cloud(request)
    instance = service(tmp_path, handler)
    instance.connect('cloud-account', 'password')
    instance.sync()
    return instance, writes


def test_lock_settings_readback_capabilities_and_minimal_hardware_patch(tmp_path):
    instance, writes = configured_lock(tmp_path)
    with instance.database.transaction(write=True) as connection:
        connection.execute("UPDATE gateways SET name='本地备注名' WHERE provider_gateway_id='21'")
    current = instance.get_lock_settings(1)
    assert 'DO-NOT-EXPOSE' not in json.dumps(current)
    assert current['readonly']['gateways'][0]['name'] == '本地备注名'
    assert current['readonly']['gateways'][0]['cloud_name'] == 'G2'
    assert current['supported']['door_alarm_seconds'] is True
    assert current['capabilities']['door_alarm_seconds'] is False
    assert current['settings']['door_alarm_seconds'] is None
    with pytest.raises(HTTPException) as unsupported:
        instance.update_lock_settings(1, {'door_alarm_seconds': 20}, 'Admin')
    assert unsupported.value.status_code == 409
    assert writes == []
    with pytest.raises(HTTPException) as invalid:
        instance.update_lock_settings(1, {'auto_lock_seconds': True}, 'Admin')
    assert invalid.value.status_code == 422
    assert writes == []
    result = instance.update_lock_settings(1, {'auto_lock_seconds': 10, 'sound_volume': 3,
              'passage_mode': True, 'passage_schedules': [{'weekdays': [2, 1], 'all_day': False, 'start_minute': 480, 'end_minute': 1080}]}, 'Admin')
    assert [path for path, _ in writes] == ['/v3/lock/setAutoLockTime', '/v3/lock/configurePassageMode']
    assert result['settings']['auto_lock_seconds'] == 10
    assert result['settings']['passage_schedules'][0]['weekdays'] == [1, 2]
    assert result['message'] == '锁设置已更新。'
    with instance.database.transaction() as connection:
        rows = connection.execute('SELECT outcome FROM ttlock_setting_changes').fetchall()
    assert [row[0] for row in rows] == ['confirmed', 'confirmed']


def test_lock_setting_partial_timeout_stops_writes_and_returns_safe_readback(tmp_path):
    instance, writes = configured_lock(tmp_path, fail_setting=2)
    with pytest.raises(HTTPException) as failure:
        instance.update_lock_settings(1, {'auto_lock_seconds': 10, 'privacy_lock': True, 'tamper_alert': True}, 'Admin')
    detail = failure.value.detail
    assert failure.value.status_code == 504
    assert detail['code'] == 'TTLOCK_SETTINGS_PARTIAL'
    assert detail['acknowledged'] == ['auto_lock_seconds']
    assert detail['failed'] == ['privacy_lock']
    assert detail['current']['settings']['auto_lock_seconds'] == 10
    assert detail['current']['settings']['privacy_lock'] is False
    assert 'DO-NOT-EXPOSE' not in json.dumps(detail)
    assert 'private upstream' not in json.dumps(detail)
    assert len(writes) == 2
    with instance.database.transaction() as connection:
        rows = connection.execute('SELECT setting,outcome FROM ttlock_setting_changes ORDER BY id').fetchall()
    assert [tuple(row) for row in rows] == [('auto_lock_seconds', 'confirmed'), ('privacy_lock', 'unknown')]
