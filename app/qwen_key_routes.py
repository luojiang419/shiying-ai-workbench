"""本机 Key 管理路由及旧 Qwen 端口上的原路径分发。"""
from __future__ import annotations

from pathlib import Path
import socket

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from .config import ROOT_DIR
from .qwen_key_migration import migrate_legacy
from .qwen_keys import LEGACY_CONTROL_PORT, LEGACY_PUBLIC_PORT, check_management_request


class NewKey(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class ImportKeys(BaseModel):
    path: str = ''


def client_addresses():
    try:
        addresses = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith(('127.', '169.254.'))]
    except OSError:
        addresses = []
    try:
        # UDP connect 仅查询默认路由，不发送数据，避免复制到 VMware/WSL 的虚拟网卡地址。
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))
            primary = probe.getsockname()[0]
        addresses = list(dict.fromkeys([primary, *addresses]))
    except OSError:
        pass
    return addresses


def install_key_routes(app, compat, studio):
    def management(request: Request, response: Response):
        check_management_request(request)
        response.headers['Cache-Control'] = 'no-store'

    router = APIRouter(dependencies=[Depends(management)])

    @router.get('')
    def keys():
        return {'keys': studio.keys.list()}

    @router.post('', status_code=201)
    def create(payload: NewKey):
        return studio.keys.create(payload.name)

    @router.get('/{key_id}/secret')
    def reveal(key_id: str):
        return {'api_key': studio.keys.reveal(key_id)}

    @router.post('/{key_id}/rotate')
    def rotate(key_id: str):
        return studio.keys.rotate(key_id)

    @router.post('/{key_id}/disable')
    def disable(key_id: str):
        return studio.keys.disable(key_id)

    @router.delete('/{key_id}', status_code=204)
    def delete(key_id: str):
        studio.keys.delete(key_id)
        return Response(status_code=204)

    app.include_router(router, prefix='/api/qwen/keys')
    compat.include_router(router, prefix='/api/keys')

    @app.get('/api/qwen/access', dependencies=[Depends(check_management_request)])
    def access(request: Request):
        addresses = client_addresses()
        public = next((item for item in studio.keys.listeners if item['port'] == LEGACY_PUBLIC_PORT and item['listening']), None)
        client_url = f'http://{addresses[0] if addresses else "127.0.0.1"}:{LEGACY_PUBLIC_PORT}' if public else str(request.base_url).rstrip('/') + '/qwen'
        return {'authentication_enabled': studio.keys.setting('authentication_enabled', False),
                'legacy_ports_enabled': studio.keys.setting('legacy_ports_enabled', False),
                'listeners': studio.keys.listeners, 'migration': studio.keys.setting('migration'),
                'migration_error': getattr(studio.keys, 'migration_error', None),
                'client_url': client_url, 'lan_addresses': addresses,
                'legacy_control_url': f'http://127.0.0.1:{LEGACY_CONTROL_PORT}',
                'legacy_public_port': LEGACY_PUBLIC_PORT}

    @app.post('/api/qwen/import', dependencies=[Depends(check_management_request)])
    def import_keys(payload: ImportKeys):
        path = Path(payload.path) if payload.path.strip() else ROOT_DIR.parent / 'qwen2.1/data/gallery.sqlite3'
        if path.is_dir():
            path = path / 'data/gallery.sqlite3' if (path / 'data/gallery.sqlite3').is_file() else path / 'gallery.sqlite3'
        try:
            return migrate_legacy(studio, path)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    app.add_middleware(QwenLegacyPortMiddleware, compat=compat)


class QwenLegacyPortMiddleware:
    def __init__(self, app, compat):
        self.app, self.compat = app, compat

    async def __call__(self, scope, receive, send):
        port = (scope.get('server') or (None, None))[1]
        path = scope.get('path', '')
        legacy = port in {LEGACY_CONTROL_PORT, LEGACY_PUBLIC_PORT}
        if scope['type'] == 'http' and port == LEGACY_PUBLIC_PORT:
            relative = path.removeprefix('/qwen')
            if relative in {'/docs', '/redoc', '/openapi.json'} or relative.startswith(('/api/keys', '/api/console', '/api/accounts')):
                await JSONResponse({'detail': 'Not Found'}, status_code=404)(scope, receive, send)
                return
        if scope['type'] == 'http' and legacy and not path.startswith('/qwen/'):
            forwarded = dict(scope)
            forwarded['qwen_legacy'] = True
            await self.compat(forwarded, receive, send)
            return
        await self.app(scope, receive, send)
