"""诗影公开镜像：实例所有者首次配置 DeepSeek Flash。"""
from __future__ import annotations

import argparse
import hmac
import json
import secrets
import socket
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
BRAND = '诗影 AI 工作台'
MODEL = 'deepseek-flash'
BASE_URL = 'https://api.deepseek.com/v1'


def enabled(root: Path = ROOT) -> bool:
    try:
        return json.loads((root/'config/public-release.json').read_text(encoding='utf-8')).get('edition') == 'video-clone'
    except (OSError, ValueError, AttributeError):
        return False


def write_private(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.new')
    with temporary.open('w', encoding='utf-8') as stream:
        temporary.chmod(0o600)
        json.dump(value, stream, ensure_ascii=False)
    temporary.replace(path)


def instance_id() -> str:
    from node_monitor.agent import runtime_environment
    return runtime_environment().get('AutoDLContainerUUID') or socket.gethostname()


def owner_code(root: Path = ROOT, instance: str | None = None) -> str:
    instance = instance or instance_id()
    path = root/'config/public-owner.json'
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        stored = {}
    if stored.get('instance') != instance or not stored.get('code'):
        stored = {'instance':instance, 'code':secrets.token_urlsafe(32)}
        write_private(path, stored)
    return stored['code']


def description(root: Path = ROOT) -> dict:
    from .prompt_optimizer import load_assistant_providers
    return {'enabled':enabled(root), 'brand':BRAND, 'edition':'video-clone', 'imageStudio':False,
            'setupUrl':'/setup/', 'assistantConfigured':bool(load_assistant_providers(root/'config/assistant-providers.json'))}


def configure_flash(root: Path, key: str, verify=None) -> None:
    from .assistant_secrets import seal_node_key
    from .prompt_optimizer import _provider_completion
    if not isinstance(key, str) or not 10 <= len(key.strip()) <= 512 or any(c.isspace() for c in key.strip()):
        raise ValueError('请输入有效的 DeepSeek API Key')
    key = key.strip()
    provider = {'id':'deepseek-flash', 'name':'DeepSeek Flash', 'baseUrl':BASE_URL,
                'apiType':'OpenAI Responses API', 'defaultModel':MODEL, 'enabled':True}
    try:
        if verify:
            verify(provider, key)
        else:
            answer = _provider_completion(provider, key)('setup', {'messages':[
                {'role':'system','content':'Reply with OK only.'}, {'role':'user','content':'Connection check.'}]}, False)
            if answer.strip().rstrip('.') != 'OK':
                raise ValueError('Unexpected response')
    except (OSError, RuntimeError, ValueError):
        raise ValueError('Flash 验证失败，请检查 Key、余额或稍后重试') from None
    provider['encryptedKey'] = seal_node_key(key, root)
    write_private(root/'config/assistant-providers.json', [provider])


def install_routes(app, *, root: Path = ROOT, verify=None) -> None:
    if not enabled(root):
        return
    owner_code(root)

    @app.get('/setup/')
    def setup_page():
        web = root/'public-web' if (root/'public-web').is_dir() else root/'web'
        return FileResponse(web/'public-setup.html', headers={'Cache-Control':'no-store'})

    @app.get('/api/public-release')
    def release_status():
        return description(root)

    @app.get('/api/public-release/license')
    def model_license():
        return FileResponse(root/'model-notices/MiniMax-H3-LICENSE.txt',media_type='text/plain; charset=utf-8')

    @app.post('/api/public-release/assistant')
    def save_assistant(payload: dict, request: Request):
        from .qwen_keys import check_management_request
        check_management_request(request)
        code = request.headers.get('x-shiying-admin-code', '')
        if not hmac.compare_digest(code, owner_code(root)):
            raise HTTPException(401, '管理码不正确，请在当前实例终端获取')
        try:
            configure_flash(root, payload.get('apiKey'), verify)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {'configured':True, 'model':MODEL}


def main():
    parser = argparse.ArgumentParser(description='诗影 AI 工作台实例管理')
    parser.add_argument('command', choices=['admin-code'])
    parser.parse_args()
    if not enabled():
        raise SystemExit('当前未启用公开镜像版')
    print(owner_code())


if __name__ == '__main__':
    main()
