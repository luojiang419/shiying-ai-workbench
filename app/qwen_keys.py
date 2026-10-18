"""Qwen 命名 API Key：保留旧哈希与 Windows DPAPI 密文，密钥管理仅限本机。"""
from __future__ import annotations

import ctypes
import hashlib
import ipaddress
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

LEGACY_CONTROL_PORT = 8192
LEGACY_PUBLIC_PORT = 8193


@dataclass(frozen=True)
class QwenPrincipal:
    id: str | None = None
    name: str = '本机工作台'
    role: str = 'admin'


class DataBlob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect_key(value: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != 'nt':
        from .linux_native import protect_key as native_protect
        return native_protect(value, decrypt=decrypt)
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    original, result = DataBlob(len(value), source), DataBlob()
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if not function(ctypes.byref(original), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def local_request(request: Request) -> bool:
    try:
        address = ipaddress.ip_address(request.client.host if request.client else '')
        return address.is_loopback or bool(getattr(address, 'ipv4_mapped', None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def public_request(request: Request) -> bool:
    return (request.scope.get('server') or (None, None))[1] == LEGACY_PUBLIC_PORT


def check_management_request(request: Request) -> None:
    if public_request(request):
        raise HTTPException(404, '接口不存在')
    if not local_request(request):
        raise HTTPException(403, 'API Key 管理仅限本机工作台')
    origin = request.headers.get('origin', '')
    if origin and origin not in {'tauri://localhost', 'http://tauri.localhost', 'https://tauri.localhost'}:
        parsed = urlsplit(origin)
        if parsed.scheme not in {'http', 'https'} or parsed.netloc != request.headers.get('host'):
            raise HTTPException(403, '请求来源不匹配')


def assert_visible(item: dict, principal: QwenPrincipal) -> None:
    if principal.role == 'api' and item.get('key_id') != principal.id:
        raise HTTPException(404, '任务或图片不存在')


class QwenKeys:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.listeners: list[dict] = []
        with closing(self.db()) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    token_hash TEXT NOT NULL UNIQUE, token_prefix TEXT NOT NULL, token_value BLOB,
                    disabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
                    last_used_at INTEGER, calls_today INTEGER NOT NULL DEFAULT 0, calls_day INTEGER
                );
                CREATE TABLE IF NOT EXISTS qwen_api_settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            db.commit()

    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def setting(self, name, default=None):
        with closing(self.db()) as db:
            row = db.execute('SELECT value FROM qwen_api_settings WHERE name=?', (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def principal(self, request: Request) -> QwenPrincipal:
        cached = getattr(request.state, 'qwen_principal', None)
        if cached is not None:
            return cached
        header = request.headers.get('authorization', '')
        if header:
            token = header[7:].strip() if header.lower().startswith('bearer ') else ''
            digest = hashlib.sha256(token.encode()).hexdigest()
            now = int(time.time())
            with closing(self.db()) as db:
                row = db.execute('SELECT id,name FROM api_keys WHERE token_hash=? AND disabled=0', (digest,)).fetchone()
                if not token or row is None:
                    raise HTTPException(401, 'API Key 无效或已停用')
                db.execute('''UPDATE api_keys SET last_used_at=?, calls_today=CASE WHEN calls_day=? THEN calls_today+1 ELSE 1 END,
                              calls_day=? WHERE id=?''', (now, now // 86400, now // 86400, row['id']))
                db.commit()
            principal = QwenPrincipal(row['id'], row['name'], 'api')
        elif public_request(request) or (self.setting('authentication_enabled', False) and not local_request(request)):
            raise HTTPException(401, '请提供 Authorization: Bearer <API_KEY>')
        else:
            if local_request(request):
                check_management_request(request)
            principal = QwenPrincipal()
        request.state.qwen_principal = principal
        return principal

    def list(self):
        with closing(self.db()) as db:
            rows = db.execute('''SELECT id,name,token_prefix,token_value IS NOT NULL AS can_reveal,disabled,created_at,last_used_at,
                                CASE WHEN calls_day=? THEN calls_today ELSE 0 END AS calls_today,
                                (SELECT COUNT(*) FROM images WHERE json_extract(data,'$.key_id')=api_keys.id) AS image_count
                                FROM api_keys ORDER BY created_at DESC,id DESC''', (int(time.time()) // 86400,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def validate_name(name):
        name = name.strip()
        if not 1 <= len(name) <= 32 or any(ord(char) < 32 for char in name):
            raise HTTPException(422, 'API 名称需为 1～32 字符，不能包含控制字符')
        return name

    def create(self, name):
        name = self.validate_name(name)
        key_id, token, now = uuid.uuid4().hex, 'sk-shiying-' + secrets.token_urlsafe(32), int(time.time())
        encrypted = protect_key(token.encode())
        with closing(self.db()) as db:
            try:
                db.execute('INSERT INTO api_keys(id,name,token_hash,token_prefix,token_value,created_at) VALUES(?,?,?,?,?,?)',
                           (key_id, name, hashlib.sha256(token.encode()).hexdigest(), token[:19], encrypted, now))
                db.execute("INSERT OR REPLACE INTO qwen_api_settings VALUES('authentication_enabled','true')")
                db.commit()
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, 'API 名称已存在') from exc
        return {'id': key_id, 'name': name, 'api_key': token, 'created_at': now}

    def reveal(self, key_id):
        with closing(self.db()) as db:
            row = db.execute('SELECT token_value FROM api_keys WHERE id=?', (key_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '密钥不存在')
        if row[0] is None:
            raise HTTPException(409, '此旧密钥仅保存哈希，无法还原明文；原 Key 仍可使用，无需重置')
        try:
            return protect_key(row[0], decrypt=True).decode()
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise HTTPException(409, '无法解密此密钥，请使用创建它的 Windows 账号') from exc

    def rotate(self, key_id):
        token = 'sk-shiying-' + secrets.token_urlsafe(32)
        encrypted = protect_key(token.encode())
        with closing(self.db()) as db:
            result = db.execute('UPDATE api_keys SET token_hash=?,token_prefix=?,token_value=? WHERE id=? AND disabled=0',
                                (hashlib.sha256(token.encode()).hexdigest(), token[:19], encrypted, key_id))
            if not result.rowcount:
                raise HTTPException(404, '启用中的密钥不存在')
            db.commit()
        return {'api_key': token}

    def disable(self, key_id):
        with closing(self.db()) as db:
            result = db.execute('UPDATE api_keys SET disabled=1 WHERE id=?', (key_id,))
            if not result.rowcount:
                raise HTTPException(404, '密钥不存在')
            db.commit()
        return {'disabled': True}

    def delete(self, key_id):
        with closing(self.db()) as db:
            result = db.execute('DELETE FROM api_keys WHERE id=?', (key_id,))
            if not result.rowcount:
                raise HTTPException(404, '密钥不存在')
            db.commit()
