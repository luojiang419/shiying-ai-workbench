"""公开镜像的实例设置会话；完整设置仅对持有实例管理码的所有者开放。"""
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

from fastapi import HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from . import browser_settings, config
from .browser_workbench import panel_settings
from .qwen_keys import check_management_request

COOKIE = 'shiying_settings'
SESSION_SECONDS = 8 * 60 * 60


def _owner_code(root: Path) -> str:
    from .public_release import owner_code
    return owner_code(root)


def _signature(root: Path, issued: str) -> str:
    return hmac.new(_owner_code(root).encode(), ('settings:' + issued).encode(), hashlib.sha256).hexdigest()


def is_owner(request: Request, root: Path) -> bool:
    value = request.cookies.get(COOKIE, '')
    try:
        issued, signature = value.split('.', 1)
        age = time.time() - int(issued)
    except (ValueError, TypeError):
        return False
    return 0 <= age <= SESSION_SECONDS and hmac.compare_digest(signature.encode(), _signature(root, issued).encode())


def install_routes(app, *, root: Path = config.ROOT_DIR) -> None:
    def require_owner(request: Request):
        check_management_request(request)
        if not is_owner(request, root):
            raise HTTPException(401, '请先使用实例管理码解锁设置')

    @app.post('/api/public-settings/login')
    def login(payload: dict, request: Request, response: Response):
        check_management_request(request)
        code = payload.get('code')
        if not isinstance(code, str) or not hmac.compare_digest(code.strip().encode(), _owner_code(root).encode()):
            raise HTTPException(401, '实例管理码不正确')
        issued = str(int(time.time()))
        response.set_cookie(COOKIE, issued + '.' + _signature(root, issued), max_age=SESSION_SECONDS,
                            httponly=True, samesite='strict',
                            secure=request.url.scheme == 'https' or request.headers.get('origin', '').startswith('https://'))
        response.headers['Cache-Control'] = 'no-store'
        return {'authenticated': True}

    @app.post('/api/public-settings/logout')
    def logout(request: Request, response: Response):
        check_management_request(request)
        response.delete_cookie(COOKIE)
        response.headers['Cache-Control'] = 'no-store'
        return {'authenticated': False}

    @app.get('/api/public-settings/bootstrap')
    def bootstrap(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        check_management_request(request)
        if not is_owner(request, root):
            return {'canManage': False}
        return {'canManage': True, 'publicOwner': True, 'managementPath': '/api/public-settings',
                'settings': panel_settings(), 'providers': browser_settings.public_providers()}

    @app.post('/api/public-settings/command/{command}')
    def command_route(command: str, args: dict, request: Request):
        require_owner(request)
        try:
            result = browser_settings.execute(command, args, root)
            return panel_settings() if command in browser_settings.SETTING_COMMANDS else result
        except KeyError as error:
            raise HTTPException(404, '接口不存在') from error
        except (ValueError, TypeError, RuntimeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.get('/api/public-settings/log', response_class=PlainTextResponse)
    def log(request: Request):
        require_owner(request)
        paths = [path for path in config.LOG_DIR.glob('*.log') if path.is_file()]
        if not paths:
            return '暂无服务日志'
        path = max(paths, key=lambda item: item.stat().st_mtime)
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 128 * 1024))
            return stream.read().decode('utf-8', errors='replace')
