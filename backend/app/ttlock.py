"""Administrator-only TTLock cloud access; hardware commands are never retried."""

import hashlib
import json
import os
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.database import Database

BASE_URL = 'https://cnapi.ttlock.com'
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def fail(status, code, message):
    raise HTTPException(status, detail={'code': code, 'message': message})


def now():
    return datetime.now(timezone.utc).isoformat()


def cloud_id(value):
    value = str(value)
    if not value.isascii() or not value.isdigit() or int(value) <= 0 or len(value) > 20:
        fail(502, 'TTLOCK_INVALID_DEVICE', '设备的平台 ID 无效，请重新同步设备。')
    return str(int(value))


class TTLockService:
    def __init__(self, db_path: Path, *, transport=None):
        self.database = Database(db_path)
        self.key_path = Path(db_path).with_suffix('.ttlock-key')
        self.transport = transport
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(Path(db_path).resolve()), threading.RLock())
        with self.database.transaction(write=True) as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS ttlock_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), client_id TEXT NOT NULL DEFAULT '',
                    secret TEXT NOT NULL DEFAULT '', token TEXT NOT NULL DEFAULT '',
                    refresh TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '',
                    expires_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS ttlock_devices (
                    kind TEXT NOT NULL, provider_id TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1,
                    details TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'unknown',
                    observed_at TEXT, PRIMARY KEY(kind,provider_id));
                CREATE TABLE IF NOT EXISTS ttlock_links (
                    lock_id TEXT NOT NULL, gateway_id TEXT NOT NULL,
                    PRIMARY KEY(lock_id,gateway_id));
                CREATE TABLE IF NOT EXISTS ttlock_commands (
                    id INTEGER PRIMARY KEY, lock_id TEXT NOT NULL, action TEXT NOT NULL,
                    actor TEXT NOT NULL, started_at REAL NOT NULL, finished_at TEXT,
                    outcome TEXT NOT NULL, error_code TEXT);
            ''')

    def _cipher(self, create=False):
        try:
            if create and not self.key_path.exists():
                try:
                    descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(Fernet.generate_key())
                        stream.flush()
                        os.fsync(stream.fileno())
            if self.key_path.is_symlink():
                raise ValueError()
            descriptor = os.open(self.key_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or (os.name != 'nt' and metadata.st_mode & 0o077):
                    raise ValueError()
                return Fernet(stream.read(100))
        except (OSError, ValueError):
            fail(503, 'TTLOCK_ENCRYPTION_UNAVAILABLE', '通通锁加密密钥不可用，请检查服务器密钥文件及权限。')

    def _encrypt(self, value):
        return self._cipher(create=True).encrypt(value.encode()).decode('ascii')

    def _decrypt(self, value):
        try:
            return self._cipher().decrypt(value.encode('ascii')).decode()
        except (InvalidToken, UnicodeError):
            fail(503, 'TTLOCK_DECRYPT_FAILED', '无法解密通通锁配置，请恢复服务器密钥文件。')

    def _settings(self):
        with self.database.transaction() as connection:
            row = connection.execute('SELECT * FROM ttlock_settings WHERE id=1').fetchone()
        return dict(row) if row else dict(client_id='', secret='', token='', refresh='', username='', expires_at=0)

    def public(self):
        with self._lock:
            data = self._settings()
            return {'client_id': data['client_id'], 'client_secret_configured': bool(data['secret']),
                    'connected': bool(data['token'] and data['refresh']), 'username': data['username'],
                    'expires_at': datetime.fromtimestamp(data['expires_at'], timezone.utc).isoformat() if data['expires_at'] else None}

    def save_settings(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'client_id', 'client_secret'}:
            fail(422, 'TTLOCK_INVALID_SETTINGS', '通通锁配置格式无效。')
        for field, value in payload.items():
            if not isinstance(value, str) or len(value) > (128 if field == 'client_id' else 256) or any(ord(char) < 33 or ord(char) > 126 for char in value.strip()):
                fail(422, 'TTLOCK_INVALID_SETTINGS', '通通锁配置格式无效。')
        with self._lock:
            previous = self._settings()
            client_id = payload.get('client_id', previous['client_id']).strip()
            secret = payload.get('client_secret', '').strip()
            changed = client_id != previous['client_id'] or bool(secret)
            ciphertext = self._encrypt(secret) if secret else (previous['secret'] if client_id == previous['client_id'] else '')
            with self.database.transaction(write=True) as connection:
                connection.execute('''INSERT INTO ttlock_settings(id,client_id,secret) VALUES(1,?,?)
                    ON CONFLICT(id) DO UPDATE SET client_id=excluded.client_id,secret=excluded.secret''', (client_id, ciphertext))
                if changed:
                    connection.execute("UPDATE ttlock_settings SET token='',refresh='',username='',expires_at=0 WHERE id=1")
                    connection.execute('DELETE FROM ttlock_devices')
                    connection.execute('DELETE FROM ttlock_links')
            return self.public()

    def _configured(self):
        config = self._settings()
        if not config['client_id'] or not config['secret']:
            fail(503, 'HARDWARE_NOT_CONFIGURED', '请先配置通通锁应用并连接账号，未向设备发送任何请求。')
        return config

    def _request(self, method, path, data, *, command=False):
        try:
            with httpx.Client(base_url=BASE_URL, transport=self.transport, timeout=httpx.Timeout(40, connect=10), follow_redirects=False, trust_env=False) as client:
                response = client.request(method, path, **({'params': data} if method == 'GET' else {'data': data}))
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError()
                return body
        except (httpx.TimeoutException, httpx.NetworkError):
            if command:
                fail(504, 'COMMAND_RESULT_UNKNOWN', '开关锁结果未知，请现场核实或请求锁状态；系统不会自动重发指令。')
            fail(502, 'TTLOCK_UNAVAILABLE', '通通锁服务连接超时或网络不可用。')
        except (httpx.HTTPError, ValueError):
            if command:
                fail(502, 'COMMAND_RESULT_UNKNOWN', '未收到有效的开关锁结果，请现场核实或请求锁状态。')
            fail(502, 'TTLOCK_INVALID_RESPONSE', '通通锁服务未返回有效结果。')

    @staticmethod
    def _check(body):
        code = body.get('errcode', 0)
        if code not in (0, '0', None):
            messages = {'10000': '通通锁应用 ID 无效。', '10001': '通通锁应用 ID 或密钥无效。',
                        '30006': '通通锁接口调用过于频繁，请稍后再试。', '30007': '本月通通锁接口额度已用完。',
                        '-3036': '锁离线，请检查电量及与网关的距离。',
                        '10007': '通通锁账号或密码错误。', '10003': '通通锁授权无效，请重新连接账号。',
                        '10004': '通通锁授权已失效，请重新连接账号。', '10011': '通通锁授权已失效，请重新连接账号。',
                        '-4043': '请先在通通锁 App 中开启该锁的远程开锁功能。',
                        '-2012': '该锁暂无可用网关，请检查 App 中的设备连接。',
                        '-3002': '网关离线，请检查电源与网络。', '-3003': '网关忙，请稍后请求状态。',
                        '-3037': '锁正在执行其他操作，请稍后请求状态。', '-3046': '该账号没有远程开锁权限。',
                        '30008': '账号不属于中国区，请使用国内通通锁账号。', '80000': '服务器时间与通通锁平台时间不同步。'}
            safe_code = str(code) if isinstance(code, (str, int)) and str(code).lstrip('-').isdigit() and len(str(code)) <= 12 else 'UNKNOWN'
            fail(502, 'TTLOCK_' + safe_code, messages.get(str(code), '通通锁拒绝了该请求，请检查设备及账号权限。'))
        return body

    def _persist_tokens(self, config, body, username):
        self._check(body)
        if not all(isinstance(body.get(key), str) and body[key] for key in ('access_token', 'refresh_token')):
            fail(502, 'TTLOCK_INVALID_RESPONSE', '通通锁授权返回不完整。')
        lifetime = body.get('expires_in')
        if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or not 0 < lifetime <= 10 * 366 * 86400:
            fail(502, 'TTLOCK_INVALID_RESPONSE', '通通锁授权有效期无效。')
        with self.database.transaction(write=True) as connection:
            connection.execute('UPDATE ttlock_settings SET token=?,refresh=?,username=?,expires_at=? WHERE id=1',
                               (self._encrypt(body['access_token']), self._encrypt(body['refresh_token']), username, time.time() + lifetime))
        return self._settings()

    def connect(self, username, password):
        if not isinstance(username, str) or not username.strip() or len(username) > 128 or not isinstance(password, str) or not password or len(password) > 256:
            fail(422, 'TTLOCK_INVALID_LOGIN', '请输入有效的通通锁账号和密码。')
        with self._lock:
            config = self._configured()
            body = self._request('POST', '/oauth2/token', {'clientId': config['client_id'], 'clientSecret': self._decrypt(config['secret']),
                                'username': username.strip(), 'password': hashlib.md5(password.encode(), usedforsecurity=False).hexdigest()})
            self._persist_tokens(config, body, username.strip())
            with self.database.transaction(write=True) as connection:
                connection.execute('DELETE FROM ttlock_devices')
                connection.execute('DELETE FROM ttlock_links')
            return self.public()

    def _refresh(self, config):
        body = self._request('POST', '/oauth2/token', {'clientId': config['client_id'], 'clientSecret': self._decrypt(config['secret']),
                             'grant_type': 'refresh_token', 'refresh_token': self._decrypt(config['refresh'])})
        try:
            return self._persist_tokens(config, body, config['username'])
        except HTTPException as exc:
            if exc.detail.get('code') in {'TTLOCK_10003', 'TTLOCK_10004', 'TTLOCK_10011'}:
                with self.database.transaction(write=True) as connection:
                    connection.execute("UPDATE ttlock_settings SET token='',refresh='',expires_at=0 WHERE id=1")
            raise

    def _api(self, path, values=None, *, command=False):
        config = self._configured()
        if not config['token'] or not config['refresh']:
            fail(503, 'HARDWARE_NOT_CONFIGURED', '请先连接通通锁账号，未向设备发送任何请求。')
        if config['expires_at'] <= time.time() + 60:
            config = self._refresh(config)
        for attempt in range(2):
            data = {'clientId': config['client_id'], 'accessToken': self._decrypt(config['token']), 'date': int(time.time() * 1000), **(values or {})}
            body = self._request('POST' if command else 'GET', path, data, command=command)
            if not command and attempt == 0 and str(body.get('errcode')) in {'10003', '10004'}:
                config = self._refresh(config)
                continue
            if command:
                code = body.get('errcode')
                if type(code) not in (int, str) or (isinstance(code, str) and not code.lstrip('-').isdigit()):
                    fail(502, 'COMMAND_RESULT_UNKNOWN', '未收到明确的开关锁结果，请现场核实或请求锁状态。')
            return self._check(body)

    def _pages(self, path, extra=None):
        records, seen = [], set()
        for page in range(1, 1001):
            body = self._api(path, {'pageNo': page, 'pageSize': 100, **(extra or {})})
            items = body.get('list')
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                fail(502, 'TTLOCK_INVALID_RESPONSE', '设备列表返回格式无效。')
            fingerprint = json.dumps(items, sort_keys=True)
            if items and fingerprint in seen:
                fail(502, 'TTLOCK_INVALID_RESPONSE', '设备列表分页重复，同步未保存。')
            seen.add(fingerprint)
            records.extend(items)
            pages = body.get('pages')
            if not items or (isinstance(pages, int) and page >= pages) or (pages is None and len(items) < 100):
                return records
        fail(502, 'TTLOCK_INVALID_RESPONSE', '设备列表过大，同步未保存。')

    @staticmethod
    def _upsert(connection, kind, provider_id, item):
        column = 'provider_lock_id' if kind == 'locks' else 'provider_gateway_id'
        existing = connection.execute(f'SELECT id FROM {kind} WHERE {column}=?', (provider_id,)).fetchone()
        if existing:
            return False
        prefix = 'TT-L-' if kind == 'locks' else 'TT-G-'
        code, suffix = prefix + provider_id, 1
        while connection.execute(f'SELECT 1 FROM {kind} WHERE code=?', (code,)).fetchone():
            suffix += 1
            code = f'{prefix}{provider_id}-{suffix}'
        name = item.get('lockAlias') or item.get('lockName') if kind == 'locks' else item.get('gatewayName')
        connection.execute(f'INSERT INTO {kind}(code,name,{column}) VALUES(?,?,?)', (code, str(name or code)[:120], provider_id))
        return True

    def sync(self):
        with self._lock:
            locks = self._pages('/v3/lock/list')
            gateways = self._pages('/v3/gateway/list', {'orderBy': 0})
            links = set()
            lock_ids = {cloud_id(item.get('lockId')) for item in locks}
            for gateway in gateways:
                gateway_id = cloud_id(gateway.get('gatewayId'))
                items = self._api('/v3/gateway/listLock', {'gatewayId': gateway_id}).get('list')
                if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                    fail(502, 'TTLOCK_INVALID_RESPONSE', '网关关联列表无效，同步未保存。')
                for item in items:
                    lock_id = cloud_id(item.get('lockId'))
                    if lock_id in lock_ids:
                        links.add((lock_id, gateway_id))
            counts = {'locks_added': 0, 'locks_updated': 0, 'gateways_added': 0, 'gateways_updated': 0}
            with self.database.transaction(write=True) as connection:
                connection.execute('UPDATE ttlock_devices SET available=0')
                connection.execute('DELETE FROM ttlock_links')
                for kind, items in (('locks', locks), ('gateways', gateways)):
                    for item in items:
                        provider_id = cloud_id(item.get('lockId' if kind == 'locks' else 'gatewayId'))
                        added = self._upsert(connection, kind, provider_id, item)
                        counts[kind + ('_added' if added else '_updated')] += 1
                        fields = ('lockAlias', 'lockName', 'lockMac', 'electricQuantity', 'featureValue') if kind == 'locks' else ('gatewayName', 'gatewayMac', 'gatewayVersion', 'isOnline')
                        details = {field: item[field] for field in fields if field in item and isinstance(item[field], (str, int, float, bool))}
                        connection.execute('''INSERT INTO ttlock_devices(kind,provider_id,available,details) VALUES(?,?,1,?)
                            ON CONFLICT(kind,provider_id) DO UPDATE SET available=1,details=excluded.details''', (kind, provider_id, json.dumps(details)))
                        if kind == 'gateways':
                            status = {0: 'offline', 1: 'online'}.get(item.get('isOnline'), 'unknown')
                            connection.execute('UPDATE ttlock_devices SET status=?,observed_at=? WHERE kind=? AND provider_id=?', (status, now(), kind, provider_id))
                connection.executemany('INSERT INTO ttlock_links(lock_id,gateway_id) VALUES(?,?)', sorted(links))
            return counts

    def device_snapshot(self, kind, record_id):
        if kind not in {'locks', 'gateways'}:
            raise ValueError('Unsupported device type')
        column = 'provider_lock_id' if kind == 'locks' else 'provider_gateway_id'
        with self.database.transaction() as connection:
            record = connection.execute(f'SELECT {column} AS provider_id FROM {kind} WHERE id=?', (record_id,)).fetchone()
            device = connection.execute('SELECT * FROM ttlock_devices WHERE kind=? AND provider_id=?', (kind, record['provider_id'])).fetchone() if record else None
            if not device:
                return {'status': 'unknown', 'observed_at': None, 'cloud_available': False, 'cloud_gateways': []}
            gateways = [dict(row) for row in connection.execute('''SELECT g.id,g.name,g.provider_gateway_id FROM ttlock_links link
                JOIN gateways g ON g.provider_gateway_id=link.gateway_id WHERE link.lock_id=?''', (record['provider_id'],))] if kind == 'locks' else []
        return {**json.loads(device['details']), 'status': device['status'] if device['available'] else 'unknown',
                'observed_at': device['observed_at'], 'cloud_available': bool(device['available']), 'cloud_gateways': gateways}

    def _device(self, kind, record_id):
        self._configured()
        config = self._settings()
        if not config['token']:
            fail(503, 'HARDWARE_NOT_CONFIGURED', '请先连接通通锁账号。')
        column = 'provider_lock_id' if kind == 'locks' else 'provider_gateway_id'
        with self.database.transaction() as connection:
            row = connection.execute(f'SELECT {column} AS provider_id FROM {kind} WHERE id=?', (record_id,)).fetchone()
            if not row:
                fail(404, 'DEVICE_NOT_FOUND', '未找到设备。')
            provider_id = cloud_id(row['provider_id'])
            device = connection.execute('SELECT available FROM ttlock_devices WHERE kind=? AND provider_id=?', (kind, provider_id)).fetchone()
        if not device or not device['available']:
            fail(409, 'TTLOCK_SYNC_REQUIRED', '请先同步通通锁，确认该设备属于当前连接账号。')
        return provider_id

    def _observe(self, kind, provider_id, status):
        observed = now()
        with self.database.transaction(write=True) as connection:
            connection.execute('UPDATE ttlock_devices SET status=?,observed_at=? WHERE kind=? AND provider_id=?', (status, observed, kind, provider_id))
        return observed

    def _check_capability(self, provider_id, action):
        with self.database.transaction() as connection:
            row = connection.execute("SELECT details FROM ttlock_devices WHERE kind='locks' AND provider_id=?", (provider_id,)).fetchone()
        feature = json.loads(row['details']).get('featureValue') if row else None
        if feature is None:
            detail = self._api('/v3/lock/detail', {'lockId': provider_id})
            feature = detail.get('featureValue')
        # Some older locks do not report capabilities. The cloud still verifies
        # permission/support; absence is not presented as confirmed support.
        try:
            bits = int(str(feature), 16)
        except (ValueError, TypeError):
            return
        bit = 10 if action == 'unlock' else 8
        if not bits & (1 << bit):
            fail(409, 'TTLOCK_UNSUPPORTED_ACTION', '该锁未报告远程开锁能力。' if action == 'unlock' else '该锁不支持远程闭锁；请按设备方式关门自动落锁。')

    def lock_action(self, record_id, action, actor):
        if action not in {'unlock', 'lock', 'status'}:
            fail(422, 'INVALID_ACTION', '无效的锁操作。')
        with self._lock:
            provider_id = self._device('locks', record_id)
            if action == 'status':
                body = self._api('/v3/lock/queryOpenState', {'lockId': provider_id})
                state = body.get('state')
                status = {0: 'locked', 1: 'unlocked'}.get(state, 'unknown') if type(state) is int else 'unknown'
                observed = self._observe('locks', provider_id, status)
                if status != 'unknown':
                    with self.database.transaction(write=True) as connection:
                        connection.execute("UPDATE ttlock_commands SET outcome='state_checked',finished_at=? WHERE lock_id=? AND outcome IN ('pending','unknown')", (observed, provider_id))
                return {'status': status, 'observed_at': observed, 'message': {'locked': '锁舌已闭合。', 'unlocked': '锁舌已打开。', 'unknown': '当前无法判断锁舌状态。'}[status]}
            self._check_capability(provider_id, action)
            with self.database.transaction(write=True) as connection:
                previous = connection.execute('SELECT * FROM ttlock_commands WHERE lock_id=? ORDER BY id DESC LIMIT 1', (provider_id,)).fetchone()
                if previous and previous['outcome'] in {'pending', 'unknown'}:
                    fail(409, 'COMMAND_RESULT_UNKNOWN', '上一次开关锁结果未知，请先请求锁状态并现场核实。')
                finished = datetime.fromisoformat(previous['finished_at']).timestamp() if previous and previous['finished_at'] else 0
                if previous and time.time() - max(previous['started_at'], finished) < 5:
                    fail(409, 'COMMAND_COOLDOWN', '操作过于频繁，请稍后请求状态。')
                actor_name = str(actor.get('name') or actor.get('username') or actor.get('id') or 'admin') if isinstance(actor, dict) else str(actor or 'admin')
                command_id = connection.execute("INSERT INTO ttlock_commands(lock_id,action,actor,started_at,outcome) VALUES(?,?,?,?,'pending')", (provider_id, action, actor_name[:128], time.time())).lastrowid
            try:
                self._api('/v3/lock/' + action, {'lockId': provider_id}, command=True)
            except HTTPException as exc:
                code = exc.detail.get('code', 'TTLOCK_ERROR') if isinstance(exc.detail, dict) else 'TTLOCK_ERROR'
                outcome = 'unknown' if code == 'COMMAND_RESULT_UNKNOWN' else 'failed'
                with self.database.transaction(write=True) as connection:
                    connection.execute('UPDATE ttlock_commands SET outcome=?,finished_at=?,error_code=? WHERE id=?', (outcome, now(), code, command_id))
                self._observe('locks', provider_id, 'unknown')
                raise
            status = 'unlocked' if action == 'unlock' else 'locked'
            observed = self._observe('locks', provider_id, status)
            with self.database.transaction(write=True) as connection:
                connection.execute("UPDATE ttlock_commands SET outcome='succeeded',finished_at=? WHERE id=?", (observed, command_id))
            return {'status': status, 'observed_at': observed, 'message': '开锁指令已执行，请现场确认柜门。' if action == 'unlock' else '闭锁指令已执行，请现场确认柜门。'}

    def gateway_status(self, record_id):
        with self._lock:
            provider_id = self._device('gateways', record_id)
            item = self._api('/v3/gateway/detail', {'gatewayId': provider_id})
            status = {0: 'offline', 1: 'online'}.get(item.get('isOnline'), 'unknown')
            observed = self._observe('gateways', provider_id, status)
            return {'status': status, 'observed_at': observed, 'message': {'online': '平台显示网关在线；状态可能有延迟。', 'offline': '平台显示网关离线；状态可能有延迟。', 'unknown': '平台暂未返回该网关的在线状态。'}[status]}


def create_ttlock_router(require_admin, service: TTLockService):
    router = APIRouter(prefix='/api/admin/ttlock', dependencies=[Depends(require_admin)])

    @router.get('/settings')
    def settings():
        return service.public()

    @router.put('/settings')
    async def save_settings(request: Request):
        try:
            payload = await request.json()
        except ValueError:
            fail(422, 'TTLOCK_INVALID_SETTINGS', '通通锁配置格式无效。')
        return await run_in_threadpool(service.save_settings, payload)

    @router.post('/connect')
    async def connect(request: Request):
        try:
            payload = await request.json()
        except ValueError:
            fail(422, 'TTLOCK_INVALID_LOGIN', '通通锁登录信息格式无效。')
        if not isinstance(payload, dict) or set(payload) != {'username', 'password'}:
            fail(422, 'TTLOCK_INVALID_LOGIN', '通通锁登录信息格式无效。')
        return await run_in_threadpool(service.connect, payload['username'], payload['password'])

    @router.post('/sync')
    def sync():
        return service.sync()

    return router
