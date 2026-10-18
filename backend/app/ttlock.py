"""Administrator-only TTLock cloud access; hardware commands are never retried."""

import hashlib
import json
import os
import stat
import threading
import time
import uuid
from contextlib import contextmanager
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
                CREATE TABLE IF NOT EXISTS ttlock_setting_changes (
                    id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, lock_id TEXT NOT NULL,
                    setting TEXT NOT NULL, requested TEXT NOT NULL, actor TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT NOT NULL, error_code TEXT);
                CREATE TABLE IF NOT EXISTS ttlock_gateway_changes (
                    id INTEGER PRIMARY KEY, gateway_id TEXT NOT NULL, requested_name TEXT NOT NULL,
                    actor TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
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

    def _api(self, path, values=None, *, command=False, send_by=None):
        config = self._configured()
        if not config['token'] or not config['refresh']:
            fail(503, 'HARDWARE_NOT_CONFIGURED', '请先连接通通锁账号，未向设备发送任何请求。')
        if config['expires_at'] <= time.time() + 60:
            config = self._refresh(config)
        for attempt in range(2):
            if send_by is not None and time.monotonic() > send_by:
                fail(409, 'COMMAND_EXPIRED', '设备准备耗时较长，未发送开关锁指令，请重新确认后操作。')
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
            gateways = []
            if kind == 'locks':
                for row in connection.execute('''SELECT g.id,g.name,g.provider_gateway_id,d.details FROM ttlock_links link
                    JOIN gateways g ON g.provider_gateway_id=link.gateway_id
                    LEFT JOIN ttlock_devices d ON d.kind='gateways' AND d.provider_id=link.gateway_id
                    WHERE link.lock_id=?''', (record['provider_id'],)):
                    cloud_name = json.loads(row['details'] or '{}').get('gatewayName')
                    gateways.append({'id': row['id'], 'name': row['name'], 'provider_gateway_id': row['provider_gateway_id'],
                                     'cloud_name': cloud_name if isinstance(cloud_name, str) else None})
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

    @staticmethod
    def _command_ready(connection, provider_id):
        previous = connection.execute('SELECT * FROM ttlock_commands WHERE lock_id=? ORDER BY id DESC LIMIT 1', (provider_id,)).fetchone()
        if previous and previous['outcome'] in {'pending', 'unknown'}:
            fail(409, 'COMMAND_RESULT_UNKNOWN', '上一次开关锁结果未知，请先请求锁状态并现场核实。')
        finished = datetime.fromisoformat(previous['finished_at']).timestamp() if previous and previous['finished_at'] else 0
        if previous and time.time() - max(previous['started_at'], finished) < 5:
            fail(409, 'COMMAND_COOLDOWN', '操作过于频繁，请稍后请求状态。')

    @contextmanager
    def _command_slot(self):
        if not self._lock.acquire(blocking=False):
            fail(409, 'DEVICE_BUSY', '设备正在处理其他请求，未发送开关锁指令，请稍后再试。')
        try:
            yield
        finally:
            self._lock.release()

    def unlock_cabinet(self, cabinet_id, actor):
        # Hold the same lock across the complete cabinet operation, including
        # preflight, so another request cannot interleave its physical commands.
        send_by = time.monotonic() + 15
        with self._command_slot():
            with self.database.transaction() as connection:
                records = connection.execute('SELECT id FROM locks WHERE cabinet_id=? ORDER BY id', (cabinet_id,)).fetchall()
            if not records:
                fail(409, 'CABINET_HAS_NO_LOCKS', '该柜子尚未分配柜锁，请联系管理员。')
            for record in records:
                provider_id = self._device('locks', record['id'])
                self._check_capability(provider_id, 'unlock')
                with self.database.transaction() as connection:
                    self._command_ready(connection, provider_id)
            if time.monotonic() > send_by:
                fail(409, 'COMMAND_EXPIRED', '设备准备耗时较长，未发送开锁指令，请重新确认后操作。')
            actor_name = str(actor.get('name') or '使用人') if isinstance(actor, dict) else str(actor)
            if isinstance(actor, dict) and actor.get('school_code'):
                actor_name += ' (' + str(actor['school_code']) + ')'
            completed = 0
            last_observed = None
            for record in records:
                try:
                    result = self.lock_action(record['id'], 'unlock', actor_name, _capability_checked=True)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {}
                    unknown = detail.get('code') == 'COMMAND_RESULT_UNKNOWN'
                    if not completed:
                        raise
                    raise HTTPException(exc.status_code, detail={
                        'code': 'CABINET_UNLOCK_UNKNOWN' if unknown else 'CABINET_UNLOCK_PARTIAL',
                        'message': f'已有 {completed}/{len(records)} 把锁收到成功响应，其余柜锁未能确认打开。请联系管理员核实，系统未自动重发。',
                        'status': 'unknown' if unknown else 'partial', 'completed_locks': completed,
                        'total_locks': len(records), 'upstream_code': detail.get('code', 'TTLOCK_ERROR'),
                    }) from None
                completed += 1
                last_observed = result.get('observed_at')
            return {'status': 'unlocked', 'message': '柜门开锁指令已执行，请现场确认。',
                    'completed_locks': completed, 'total_locks': len(records), 'observed_at': last_observed}

    def lock_action(self, record_id, action, actor, *, _capability_checked=False):
        if action not in {'unlock', 'lock', 'status'}:
            fail(422, 'INVALID_ACTION', '无效的锁操作。')
        send_by = time.monotonic() + 15
        with (self._lock if action == 'status' else self._command_slot()):
            provider_id = self._device('locks', record_id)
            if action == 'status':
                body = self._api('/v3/lock/queryOpenState', {'lockId': provider_id})
                state = body.get('state')
                status = {0: 'locked', 1: 'unlocked'}.get(state, 'unknown') if type(state) is int else 'unknown'
                observed = self._observe('locks', provider_id, status)
                if status != 'unknown':
                    with self.database.transaction(write=True) as connection:
                        connection.execute("UPDATE ttlock_commands SET outcome='state_checked',finished_at=? WHERE lock_id=? AND outcome IN ('pending','unknown')", (observed, provider_id))
                return {'status': status, 'observed_at': observed, 'message': '状态请求完成。'}
            if not _capability_checked:
                self._check_capability(provider_id, action)
            with self.database.transaction(write=True) as connection:
                self._command_ready(connection, provider_id)
                actor_name = str(actor.get('name') or actor.get('username') or actor.get('id') or 'admin') if isinstance(actor, dict) else str(actor or 'admin')
                command_id = connection.execute("INSERT INTO ttlock_commands(lock_id,action,actor,started_at,outcome) VALUES(?,?,?,?,'pending')", (provider_id, action, actor_name[:128], time.time())).lastrowid
            try:
                self._api('/v3/lock/' + action, {'lockId': provider_id}, command=True, send_by=send_by)
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

    @staticmethod
    def _passage_schedules(value, *, incoming=False):
        code = 'TTLOCK_INVALID_SETTINGS' if incoming else 'TTLOCK_INVALID_RESPONSE'
        status = 422 if incoming else 502
        if not isinstance(value, list) or len(value) > 5:
            fail(status, code, '常开模式最多支持五个时间段。')
        schedules = []
        for item in value:
            required = {'weekdays', 'all_day', 'start_minute', 'end_minute'}
            if not isinstance(item, dict) or (incoming and set(item) != required):
                fail(status, code, '常开模式时间段格式无效。')
            days = item.get('weekdays' if incoming else 'weekDays')
            all_day = item.get('all_day') if incoming else {1: True, 2: False}.get(item.get('isAllDay'))
            start = item.get('start_minute' if incoming else 'startTime')
            end = item.get('end_minute' if incoming else 'endTime')
            if not isinstance(days, list) or not days or len(days) > 7 or any(type(day) is not int or not 1 <= day <= 7 for day in days) or len(set(days)) != len(days) or type(all_day) is not bool:
                fail(status, code, '请选择有效且不重复的星期。')
            if all_day:
                start, end = 0, 1440
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= 1440:
                fail(status, code, '常开时间应在同一天内，结束时间必须晚于开始时间。')
            schedules.append({'weekdays': sorted(days), 'all_day': all_day, 'start_minute': start, 'end_minute': end})
        # The upstream sorts schedules when returning them. Compare canonical values.
        return sorted(schedules, key=lambda item: (item['weekdays'], item['start_minute'], item['end_minute']))

    def _read_lock_settings(self, provider_id):
        detail = self._api('/v3/lock/detail', {'lockId': provider_id})
        if 'lockId' in detail and cloud_id(detail['lockId']) != provider_id:
            fail(502, 'TTLOCK_INVALID_RESPONSE', '通通锁返回了不匹配的设备信息。')
        try:
            feature = int(str(detail.get('featureValue')), 16)
            if feature < 0:
                feature = 0
        except (TypeError, ValueError):
            feature = 0
        bits = {'auto_lock_seconds': 4, 'passage_mode': 22, 'passage_schedules': 22,
                'passage_auto_unlock': 46, 'sound_enabled': 15, 'sound_volume': 43,
                'open_direction': 36, 'privacy_lock': 30, 'tamper_alert': 28,
                'reset_button': 29, 'door_alarm_seconds': 51, 'auto_lock_disable': 23,
                'ic_card': 1, 'fingerprint': 2, 'remote_unlock': 10}
        supported = {key: bool(feature & (1 << bit)) for key, bit in bits.items()}
        supported['remote_unlock_setting'] = False  # Cloud API does not configure this switch.

        def integer(key, minimum, maximum):
            value = detail.get(key)
            return value if type(value) is int and minimum <= value <= maximum else None

        def switch(key):
            value = detail.get(key)
            return {1: True, 2: False}.get(value) if type(value) is int else None

        seconds = integer('autoLockTime', -1, 32767)
        settings = {'auto_lock_seconds': seconds if seconds != 0 else None,
                    'passage_mode': None, 'passage_auto_unlock': None, 'passage_schedules': None,
                    'sound_enabled': switch('lockSound'), 'sound_volume': integer('soundVolume', 1, 5),
                    'open_direction': {1: 'left', 2: 'right'}.get(integer('openDirection', 1, 2)),
                    'privacy_lock': switch('privacyLock'), 'tamper_alert': switch('tamperAlert'),
                    'reset_button': switch('resetButton'), 'door_alarm_seconds': None}
        # The documented detail response has no door-not-closed alarm duration.
        # Report its hardware capability, but do not invent a value or offer an
        # edit that cannot be read back. It remains managed in the vendor App.
        if supported['passage_mode']:
            passage = self._api('/v3/lock/getPassageModeConfiguration', {'lockId': provider_id})
            enabled = passage.get('passageMode')
            if type(enabled) is not int or enabled not in (1, 2):
                fail(502, 'TTLOCK_INVALID_RESPONSE', '通通锁未返回有效的常开模式状态。')
            settings['passage_mode'] = enabled == 1
            settings['passage_schedules'] = self._passage_schedules(passage.get('cyclicConfig'))
            auto_unlock = passage.get('autoUnlock')
            settings['passage_auto_unlock'] = {1: True, 2: False}.get(auto_unlock) if type(auto_unlock) is int else None
        capabilities = {key: supported[key] and value is not None for key, value in settings.items()}
        capabilities.update({key: supported[key] for key in ('auto_lock_disable', 'ic_card', 'fingerprint', 'remote_unlock', 'remote_unlock_setting')})
        with self.database.transaction() as connection:
            gateways = []
            for row in connection.execute('''SELECT g.provider_gateway_id,g.name,d.details FROM ttlock_links link
                    JOIN gateways g ON g.provider_gateway_id=link.gateway_id
                    LEFT JOIN ttlock_devices d ON d.kind='gateways' AND d.provider_id=link.gateway_id
                    WHERE link.lock_id=? ORDER BY g.id''', (provider_id,)):
                cloud_name = json.loads(row['details'] or '{}').get('gatewayName')
                gateways.append({'provider_gateway_id': row['provider_gateway_id'], 'name': row['name'],
                                 'cloud_name': cloud_name if isinstance(cloud_name, str) else None})
        def text(key):
            value = detail.get(key)
            return value[:250] if isinstance(value, str) else None
        readonly = {'provider_lock_id': provider_id, 'lock_name': text('lockName'), 'lock_alias': text('lockAlias'),
                    'lock_mac': text('lockMac'), 'battery': integer('electricQuantity', 0, 100),
                    'model': text('modelNum'), 'hardware': text('hardwareRevision'), 'firmware': text('firmwareRevision'),
                    'timezone_offset': integer('timezoneRawOffset', -86400000, 86400000),
                    'gateways': gateways, 'gateway_names_source': 'last_sync'}
        return {'settings': settings, 'capabilities': capabilities, 'supported': supported,
                'readonly': readonly, 'observed_at': now()}

    def get_lock_settings(self, record_id):
        with self._lock:
            provider_id = self._device('locks', record_id)
            return self._read_lock_settings(provider_id)

    @staticmethod
    def _validate_lock_patch(payload, current):
        values = current['settings']
        if not isinstance(payload, dict) or set(payload) - values.keys():
            fail(422, 'TTLOCK_INVALID_SETTINGS', '包含未知或只读的锁设置项。')
        changes = {}
        booleans = {'sound_enabled', 'privacy_lock', 'tamper_alert', 'reset_button', 'passage_mode', 'passage_auto_unlock'}
        for key, value in payload.items():
            if not current['capabilities'].get(key):
                fail(409, 'TTLOCK_UNSUPPORTED_SETTING', '该锁不支持此设置项，或平台未提供可核实的当前值。')
            if key in booleans and type(value) is not bool:
                fail(422, 'TTLOCK_INVALID_SETTINGS', '锁开关选项必须为开启或关闭。')
            if key == 'auto_lock_seconds':
                if type(value) is not int or not (1 <= value <= 32767 or value == -1):
                    fail(422, 'TTLOCK_INVALID_SETTINGS', '自动闭锁时间需为 1–32767 秒，或关闭。')
                if value == -1 and not current['capabilities']['auto_lock_disable']:
                    fail(409, 'TTLOCK_UNSUPPORTED_SETTING', '该锁不支持关闭自动闭锁。')
            if key == 'sound_volume' and (type(value) is not int or not 1 <= value <= 5):
                fail(422, 'TTLOCK_INVALID_SETTINGS', '音量需为 1–5。')
            if key == 'open_direction' and (not isinstance(value, str) or value not in {'left', 'right'}):
                fail(422, 'TTLOCK_INVALID_SETTINGS', '请选择左开或右开。')
            if key == 'passage_schedules':
                value = TTLockService._passage_schedules(value, incoming=True)
            if value != values[key]:
                changes[key] = value
        merged = {**values, **changes}
        if any(key.startswith('passage_') for key in changes) and merged['passage_mode'] and not merged['passage_schedules']:
            fail(422, 'TTLOCK_INVALID_SETTINGS', '开启常开模式前，请至少设置一个时间段。')
        return changes, merged

    def update_lock_settings(self, record_id, payload, actor):
        with self._lock:
            provider_id = self._device('locks', record_id)
            current = self._read_lock_settings(provider_id)
            changes, merged = self._validate_lock_patch(payload, current)
            if not changes:
                return {**current, 'message': '设置未发生变化。'}
            operations = []
            if 'auto_lock_seconds' in changes:
                operations.append(('auto_lock_seconds', '/v3/lock/setAutoLockTime', {'seconds': changes['auto_lock_seconds'], 'type': 2}, {'auto_lock_seconds'}))
            setting_types = {'privacy_lock': 2, 'tamper_alert': 3, 'reset_button': 4, 'sound_enabled': 6, 'open_direction': 7, 'sound_volume': 8}
            for key, setting_type in setting_types.items():
                if key not in changes:
                    continue
                value = changes[key]
                if type(value) is bool:
                    value = 1 if value else 2
                elif key == 'open_direction':
                    value = {'left': 1, 'right': 2}[value]
                operations.append((key, '/v3/lock/updateSetting', {'type': setting_type, 'value': value, 'changeType': 2}, {key}))
            passage_changes = {key for key in changes if key.startswith('passage_')}
            if passage_changes:
                config = [{'weekDays': item['weekdays'], 'isAllDay': 1 if item['all_day'] else 2,
                           'startTime': item['start_minute'], 'endTime': item['end_minute']} for item in merged['passage_schedules']]
                values = {'type': 2, 'passageMode': 1 if merged['passage_mode'] else 2,
                          'cyclicConfig': json.dumps(config, separators=(',', ':'))}
                if current['capabilities']['passage_auto_unlock']:
                    values['autoUnlock'] = 1 if merged['passage_auto_unlock'] else 2
                operations.append(('passage', '/v3/lock/configurePassageMode', values, passage_changes))
            actor_name = str(actor.get('name') or actor.get('username') or actor.get('id') or 'admin') if isinstance(actor, dict) else str(actor or 'admin')
            batch_id = uuid.uuid4().hex
            audit_ids, acknowledged, failed = [], [], None
            for key, path, data, fields in operations:
                with self.database.transaction(write=True) as connection:
                    audit_id = connection.execute('''INSERT INTO ttlock_setting_changes
                        (batch_id,lock_id,setting,requested,actor,started_at,outcome) VALUES(?,?,?,?,?,?,'pending')''',
                        (batch_id, provider_id, key, json.dumps({field: changes[field] for field in sorted(fields)}), actor_name[:128], now())).lastrowid
                audit_ids.append((audit_id, fields))
                try:
                    self._api(path, {'lockId': provider_id, **data}, command=True)
                except HTTPException as exc:
                    code = exc.detail.get('code', 'TTLOCK_ERROR') if isinstance(exc.detail, dict) else 'TTLOCK_ERROR'
                    uncertain = code == 'COMMAND_RESULT_UNKNOWN'
                    with self.database.transaction(write=True) as connection:
                        connection.execute('UPDATE ttlock_setting_changes SET outcome=?,error_code=?,finished_at=? WHERE id=?',
                                           ('unknown' if uncertain else 'failed', code, now(), audit_id))
                    reason = exc.detail.get('message', '') if isinstance(exc.detail, dict) else ''
                    failed = (exc.status_code, code, fields, reason)
                    break  # Never retry a write, or continue the batch after a failure.
                acknowledged.extend(sorted(fields))
                with self.database.transaction(write=True) as connection:
                    connection.execute("UPDATE ttlock_setting_changes SET outcome='acknowledged',finished_at=? WHERE id=?", (now(), audit_id))
            refreshed = None
            try:
                refreshed = self._read_lock_settings(provider_id)
            except HTTPException:
                pass
            if refreshed:
                with self.database.transaction(write=True) as connection:
                    for audit_id, fields in audit_ids:
                        matches = all(refreshed['settings'].get(field) == changes[field] for field in fields)
                        connection.execute("UPDATE ttlock_setting_changes SET outcome=? WHERE id=? AND outcome='acknowledged'",
                                           ('confirmed' if matches else 'unconfirmed', audit_id))
            if failed:
                status, code, fields, reason = failed
                message = '部分设置已发送，但后续设置失败。请核对读回值后再操作。' if acknowledged else '设置未能完成，请核对读回值后再操作。'
                if code == 'COMMAND_RESULT_UNKNOWN':
                    message = '部分设置已发送，后续设置结果未知，系统未自动重发。请核对设备配置。' if acknowledged else '设置结果未知，系统未自动重发。请重新读取并核对设备配置。'
                elif reason:
                    message += ' ' + reason
                raise HTTPException(status, detail={'code': 'TTLOCK_SETTINGS_PARTIAL' if acknowledged else 'TTLOCK_SETTINGS_FAILED',
                                    'message': message, 'upstream_code': code, 'acknowledged': acknowledged,
                                    'failed': sorted(fields), 'current': refreshed})
            if refreshed is None or any(refreshed['settings'].get(key) != value for key, value in changes.items()):
                raise HTTPException(502, detail={'code': 'TTLOCK_SETTINGS_UNCONFIRMED',
                                    'message': '设置指令已发送，但读回尚未确认全部变更，请重新读取设备配置。',
                                    'acknowledged': acknowledged, 'current': refreshed})
            return {**refreshed, 'message': '锁设置已更新。'}

    def _read_gateway_settings(self, provider_id):
        detail = self._api('/v3/gateway/detail', {'gatewayId': provider_id})
        if 'gatewayId' in detail and cloud_id(detail['gatewayId']) != provider_id:
            fail(502, 'TTLOCK_INVALID_RESPONSE', '通通锁返回了不匹配的网关信息。')
        connected = self._api('/v3/gateway/listLock', {'gatewayId': provider_id}).get('list')
        if not isinstance(connected, list) or not all(isinstance(item, dict) for item in connected):
            fail(502, 'TTLOCK_INVALID_RESPONSE', '通通锁未返回有效的关联网关设备列表。')
        def text(item, key):
            value = item.get(key)
            return value[:250] if isinstance(value, str) else None
        def integer(item, key, minimum, maximum):
            value = item.get(key)
            return value if type(value) is int and minimum <= value <= maximum else None
        observed = now()
        online = integer(detail, 'isOnline', 0, 1)
        status = {0: 'offline', 1: 'online'}.get(online, 'unknown')
        version = integer(detail, 'gatewayVersion', 1, 4)
        cloud_name = text(detail, 'gatewayName')
        safe_details = {key: detail[key] for key in ('gatewayName', 'gatewayMac', 'gatewayVersion', 'networkName', 'networkMac', 'lockNum', 'isOnline')
                        if key in detail and type(detail[key]) in (str, int)}
        locks = []
        with self.database.transaction(write=True) as connection:
            local = connection.execute('SELECT name FROM gateways WHERE provider_gateway_id=?', (provider_id,)).fetchone()
            for item in connected:
                lock_id = cloud_id(item.get('lockId'))
                local_lock = connection.execute('SELECT name FROM locks WHERE provider_lock_id=?', (lock_id,)).fetchone()
                timestamp = integer(item, 'updateDate', 0, 253402300799000)
                updated_at = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat() if timestamp is not None else None
                locks.append({'provider_lock_id': lock_id, 'name': local_lock['name'] if local_lock else None,
                              'cloud_name': text(item, 'lockAlias') or text(item, 'lockName'), 'lock_mac': text(item, 'lockMac'),
                              'rssi': integer(item, 'rssi', -200, 0), 'updated_at': updated_at})
            # Refresh only cloud observations. Local aliases and cabinet assignments
            # are separate; a cloud rename must not overwrite either of them.
            connection.execute("UPDATE ttlock_devices SET details=?,status=?,observed_at=? WHERE kind='gateways' AND provider_id=?",
                               (json.dumps(safe_details), status, observed, provider_id))
            connection.execute('DELETE FROM ttlock_links WHERE gateway_id=?', (provider_id,))
            for item in locks:
                if connection.execute("SELECT 1 FROM ttlock_devices WHERE kind='locks' AND provider_id=? AND available=1", (item['provider_lock_id'],)).fetchone():
                    connection.execute('INSERT OR IGNORE INTO ttlock_links(lock_id,gateway_id) VALUES(?,?)', (item['provider_lock_id'], provider_id))
        return {'settings': {'cloud_name': cloud_name},
                'capabilities': {'cloud_name': True, 'cloud_initialize': False, 'wifi_setup': False, 'time_sync': False, 'timezone': False},
                'readonly': {'provider_gateway_id': provider_id, 'name': local['name'] if local else None,
                             'gateway_mac': text(detail, 'gatewayMac'), 'version': version,
                             'model': f'G{version}' if version else None, 'network_name': text(detail, 'networkName'),
                             'network_mac': text(detail, 'networkMac'), 'lock_count': integer(detail, 'lockNum', 0, 100000),
                             'status': status, 'locks': locks}, 'observed_at': observed}

    def get_gateway_settings(self, record_id):
        with self._lock:
            return self._read_gateway_settings(self._device('gateways', record_id))

    def update_gateway_settings(self, record_id, payload, actor):
        if not isinstance(payload, dict) or set(payload) != {'cloud_name'}:
            fail(422, 'TTLOCK_INVALID_SETTINGS', '网关仅支持修改平台名称。')
        name = payload['cloud_name']
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(ord(char) < 32 or ord(char) == 127 for char in name):
            fail(422, 'TTLOCK_INVALID_SETTINGS', '网关名称需为 1–120 个字符，不能包含控制字符。')
        name = name.strip()
        with self._lock:
            provider_id = self._device('gateways', record_id)
            current = self._read_gateway_settings(provider_id)
            if current['settings']['cloud_name'] == name:
                return {**current, 'message': '设置未发生变化。'}
            actor_name = str(actor.get('name') or actor.get('username') or actor.get('id') or 'admin') if isinstance(actor, dict) else str(actor or 'admin')
            with self.database.transaction(write=True) as connection:
                audit_id = connection.execute('''INSERT INTO ttlock_gateway_changes
                    (gateway_id,requested_name,actor,started_at,outcome) VALUES(?,?,?,?,'pending')''',
                    (provider_id, name, actor_name[:128], now())).lastrowid
            failure = None
            try:
                self._api('/v3/gateway/rename', {'gatewayId': provider_id, 'gatewayName': name}, command=True)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                failure = (exc.status_code, detail.get('code', 'TTLOCK_ERROR'))
            refreshed = None
            try:
                refreshed = self._read_gateway_settings(provider_id)
            except HTTPException:
                pass
            confirmed = bool(refreshed and refreshed['settings']['cloud_name'] == name)
            outcome = ('unknown' if failure[1] == 'COMMAND_RESULT_UNKNOWN' else 'failed') if failure else ('confirmed' if confirmed else 'unconfirmed')
            with self.database.transaction(write=True) as connection:
                connection.execute('UPDATE ttlock_gateway_changes SET outcome=?,finished_at=?,error_code=? WHERE id=?',
                                   (outcome, now(), failure[1] if failure else None, audit_id))
            if failure:
                raise HTTPException(failure[0], detail={'code': 'TTLOCK_GATEWAY_UPDATE_FAILED',
                    'message': '网关名称修改未能确认，系统未自动重发。请核对重新读取的名称。',
                    'upstream_code': failure[1], 'current': refreshed})
            if not confirmed:
                raise HTTPException(502, detail={'code': 'TTLOCK_GATEWAY_UPDATE_UNCONFIRMED',
                    'message': '网关改名指令已发送，但读回尚未确认，请重新读取网关信息。', 'current': refreshed})
            return {**refreshed, 'message': '网关名称已更新。'}

    def gateway_status(self, record_id):
        with self._lock:
            provider_id = self._device('gateways', record_id)
            item = self._api('/v3/gateway/detail', {'gatewayId': provider_id})
            status = {0: 'offline', 1: 'online'}.get(item.get('isOnline'), 'unknown')
            observed = self._observe('gateways', provider_id, status)
            return {'status': status, 'observed_at': observed, 'message': '状态请求完成。'}


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

    @router.get('/locks/{record_id}/settings')
    def lock_settings(record_id: int):
        return service.get_lock_settings(record_id)

    @router.patch('/locks/{record_id}/settings')
    async def update_lock_settings(record_id: int, request: Request, actor=Depends(require_admin)):
        try:
            payload = await request.json()
        except ValueError:
            fail(422, 'TTLOCK_INVALID_SETTINGS', '锁设置格式无效。')
        return await run_in_threadpool(service.update_lock_settings, record_id, payload, actor)

    @router.get('/gateways/{record_id}/settings')
    def gateway_settings(record_id: int):
        return service.get_gateway_settings(record_id)

    @router.patch('/gateways/{record_id}/settings')
    async def update_gateway_settings(record_id: int, request: Request, actor=Depends(require_admin)):
        try:
            payload = await request.json()
        except ValueError:
            fail(422, 'TTLOCK_INVALID_SETTINGS', '网关设置格式无效。')
        return await run_in_threadpool(service.update_gateway_settings, record_id, payload, actor)

    @router.post('/sync')
    def sync():
        return service.sync()

    return router
