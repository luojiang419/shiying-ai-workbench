"""独立的节点登记与监控服务，不连接生成业务数据库。"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    node_id: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,100}$')
    boot_id: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,100}$')
    sequence: int = Field(ge=0, le=9223372036854775807)
    name: str = Field(max_length=80)
    instance_id: str = Field(max_length=150)
    hostname: str = Field(max_length=200)
    region: str = Field(default='', max_length=100)
    access_urls: list[str] = Field(default_factory=list, max_length=5)
    access_ips: list[str] = Field(default_factory=list, max_length=8)
    local_ips: list[str] = Field(default_factory=list, max_length=12)
    public_port: int = Field(ge=1, le=65535)
    public_scheme: str = Field(default='https', pattern=r'^https?$')
    api_ready: bool
    service_message: str = Field(default='', max_length=300)
    app_version: str = Field(default='', max_length=40)
    uptime_seconds: int = Field(ge=0, le=3153600000)

    @field_validator('access_urls')
    @classmethod
    def valid_urls(cls, values):
        for value in values:
            parsed = urlsplit(value)
            if (len(value) > 500 or parsed.scheme not in {'http', 'https'} or not parsed.hostname
                    or any(ord(character) <= 32 or ord(character) == 127 for character in value)
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in {'', '/'}):
                raise ValueError('节点访问地址须为无凭据的 HTTP(S) 主机地址')
            try:
                _ = parsed.port
            except ValueError as error:
                raise ValueError('访问端口无效') from error
        return list(dict.fromkeys(value.rstrip('/') for value in values))

    @field_validator('access_ips', 'local_ips')
    @classmethod
    def valid_ips(cls, values):
        return list(dict.fromkeys(str(ipaddress.ip_address(value)) for value in values))


def matches(actual: str, expected: str) -> bool:
    return hmac.compare_digest(hashlib.sha256(actual.encode()).digest(), hashlib.sha256(expected.encode()).digest())


class Registry:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL,
                    first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                    source_ip TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    node_id TEXT NOT NULL, boot_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    PRIMARY KEY (node_id, boot_id)
                );
            ''')

    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def receive(self, report: Heartbeat, source_ip: str, now: float) -> bool:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            node = db.execute('SELECT * FROM nodes WHERE node_id=?', (report.node_id,)).fetchone()
            session = db.execute('SELECT sequence FROM sessions WHERE node_id=? AND boot_id=?',
                                 (report.node_id, report.boot_id)).fetchone()
            # 重试不能刷新过期心跳；已被新启动会话替代的旧请求不能回写地址。
            if session and ((node and node['boot_id'] != report.boot_id) or report.sequence <= session['sequence']):
                return False
            db.execute('INSERT INTO sessions VALUES (?,?,?) ON CONFLICT(node_id,boot_id) '
                       'DO UPDATE SET sequence=excluded.sequence', (report.node_id, report.boot_id, report.sequence))
            db.execute('INSERT INTO nodes VALUES (?,?,?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET '
                       'boot_id=excluded.boot_id,last_seen=excluded.last_seen,source_ip=excluded.source_ip,payload=excluded.payload',
                       (report.node_id, report.boot_id, node['first_seen'] if node else now, now,
                        source_ip, report.model_dump_json()))
        return True

    def snapshot(self, now: float, offline_after: int):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM nodes ORDER BY last_seen DESC LIMIT 500').fetchall()
        nodes = []
        for row in rows:
            report = json.loads(row['payload'])
            age = max(0, now - row['last_seen'])
            peer = row['source_ip']
            authority = f'[{peer}]' if ':' in peer else peer
            fallback = f"{report['public_scheme']}://{authority}:{report['public_port']}"
            ip = report['access_ips'][0] if report['access_ips'] else peer
            ip_authority = f'[{ip}]' if ':' in ip else ip
            nodes.append({**report, 'first_seen': row['first_seen'], 'last_seen': row['last_seen'],
                          'source_ip': peer, 'age_seconds': round(age, 1), 'online': age <= offline_after,
                          'primary_url': report['access_urls'][0] if report['access_urls'] else fallback,
                          'address_source': 'platform' if report['access_urls'] else 'observed_ip',
                          'ip_address': ip,
                          'ip_url': f"{report['public_scheme']}://{ip_authority}:{report['public_port']}",
                          'ip_address_source': 'domain_dns' if report['access_ips'] else 'heartbeat_source'})
        nodes.sort(key=lambda row: (row['online'], row['last_seen']), reverse=True)
        return {'server_time': now, 'offline_after': offline_after, 'nodes': nodes,
                'online_count': sum(row['online'] for row in nodes), 'total_count': len(nodes)}


def create_app(config: dict, clock=time.time):
    for key in ['agent_key', 'view_key']:
        if not isinstance(config.get(key), str) or len(config[key]) < 32:
            raise ValueError(f'{key} 须为至少 32 位的随机访问密钥')
    if matches(config['agent_key'], config['view_key']):
        raise ValueError('上报密钥和监控查看密钥必须不同')
    registry = Registry(Path(config['database']))
    offline_after = int(config.get('offline_after', 60))
    if offline_after < 10:
        raise ValueError('离线阈值不能小于 10 秒')
    app = FastAPI(title='MiniMax 算力节点监控', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.registry = registry

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY'})
        return response

    @app.get('/health')
    def health():
        return {'status': 'ok', 'service': 'minimax-node-monitor', 'schema': 1}

    @app.get('/')
    def public_dashboard():
        if not config.get('public_dashboard', False):
            raise HTTPException(404, '页面不存在')
        return FileResponse(Path(__file__).parent / 'web/index.html', media_type='text/html')

    @app.get('/api/nodes')
    def public_nodes():
        if not config.get('public_dashboard', False):
            raise HTTPException(404, '接口不存在')
        data = registry.snapshot(clock(), offline_after)
        for node in data['nodes']:
            for key in ['boot_id', 'sequence', 'instance_id', 'local_ips']:
                node.pop(key, None)
        return JSONResponse(data)

    @app.post('/api/heartbeat')
    async def heartbeat(request: Request):
        authorization = request.headers.get('authorization', '')
        if not authorization.startswith('Bearer ') or not matches(authorization[7:], config['agent_key']):
            raise HTTPException(401, '上报凭据无效')
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            raise HTTPException(400, '请求长度无效') from None
        if length > 65536:
            raise HTTPException(413, '上报内容过大')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, '上报内容过大')
        try:
            report = Heartbeat.model_validate_json(bytes(body))
        except ValidationError:
            raise HTTPException(422, '上报字段或节点地址无效') from None
        source_ip = request.client.host if request.client else ''
        try:
            source_ip = str(ipaddress.ip_address(source_ip))
        except ValueError:
            source_ip = '127.0.0.1'
        now = clock()
        accepted = registry.receive(report, source_ip, now)
        return {'accepted': accepted, 'server_time': now, 'observed_ip': source_ip,
                'offline_after': offline_after}

    def check_view(key):
        if not matches(key, config['view_key']):
            raise HTTPException(404, '页面不存在')

    @app.get('/m/{key}/')
    def dashboard(key: str):
        check_view(key)
        return FileResponse(Path(__file__).parent / 'web/index.html', media_type='text/html')

    @app.get('/m/{key}/api/nodes')
    def nodes(key: str):
        check_view(key)
        return JSONResponse(registry.snapshot(clock(), offline_after))

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=18443)
    parser.add_argument('--certfile', required=True)
    parser.add_argument('--keyfile', required=True)
    args = parser.parse_args()
    import uvicorn
    config = json.loads(args.config.read_text(encoding='utf-8'))
    uvicorn.run(create_app(config), host=args.host, port=args.port, access_log=False,
                proxy_headers=False, ssl_certfile=args.certfile, ssl_keyfile=args.keyfile)


if __name__ == '__main__':
    main()
