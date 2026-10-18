"""浏览器主机设置：白名单命令、原子持久化与不回传密钥的助手管理。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import config
from .assistant_secrets import open_node_key, seal_node_key
from .prompt_optimizer import (
    _decrypt_assistant_key,
    _normalize_provider_base_url,
    _provider_completion,
)
from .upscale_settings import inspect_upscale_settings, save_upscale_settings

LOCK = threading.RLock()
SETTING_COMMANDS = {
    'set_acceleration_mode': ('acceleration_mode', 'mode', config.ACCELERATION_MODES),
    'set_default_steps': ('default_steps', 'steps', (8, 12, 16)),
    'set_workbench_reference_model': ('workbench_reference_model', 'model', ('fl2va', 'ref2va')),
    'set_cloud_clone_reference_type': ('cloud_clone_reference_type', 'referenceType', ('source', 'depth')),
    'set_vram_reserve': ('vram_reserve_gb', 'reserveGb', (0, 1, 2, 3, 4)),
    'set_nsfw_profile': ('nsfw_profile', 'profile', config.NSFW_PROFILES),
    'set_nsfw_enabled': ('nsfw_enabled', 'enabled', bool),
    'set_free_gpu_cache_after_task': ('free_gpu_cache_after_task', 'enabled', bool),
    'set_preflight_enabled': ('preflight_enabled', 'enabled', bool),
}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.stem, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def public_providers() -> list[dict]:
    return [{**{key: item.get(key) for key in ('id', 'name', 'baseUrl', 'apiType', 'models',
                                              'defaultModel', 'preset', 'autoModels', 'enabled')},
             'models': item.get('models') or [item.get('defaultModel', '')],
             'enabled': item.get('enabled', True), 'autoModels': item.get('autoModels', False),
             'hasKey': bool(item.get('encryptedKey')), 'serverManaged': True}
            for item in read_json(config.ASSISTANT_PROVIDERS_FILE, [])]


def encrypt_key(key: str, root: Path) -> str:
    if os.name == 'nt':
        # 保持与同机桌面程序的 DPAPI 格式兼容。
        from .qwen_keys import protect_key
        return protect_key(key.encode()).hex()
    return seal_node_key(key, root)


def save_providers(providers, root: Path) -> list[dict]:
    if not isinstance(providers, list) or len(providers) > 20:
        raise ValueError('最多保存 20 个助手配置')
    with LOCK:
        existing = {item['id']: item for item in read_json(config.ASSISTANT_PROVIDERS_FILE, [])}
        saved, ids = [], set()
        for item in providers:
            if not isinstance(item, dict):
                raise TypeError('助手配置必须是对象')
            identifier = item.get('id')
            if not isinstance(identifier, str) or not identifier or len(identifier) > 100 or identifier in ids:
                raise ValueError('助手配置 ID 无效或重复')
            ids.add(identifier)
            models = item.get('models')
            if (not isinstance(models, list) or not models or len(models) > 1000
                    or any(not isinstance(model, str) or not model.strip() or len(model) > 300 for model in models)
                    or item.get('defaultModel') not in models):
                raise ValueError('请选择有效的默认模型')
            api_type = item.get('apiType', 'OpenAI Chat Completions API')
            if api_type not in ('OpenAI Chat Completions API', 'OpenAI Responses API'):
                raise ValueError('不支持的助手 API 类型')
            if type(item.get('enabled', True)) is not bool or type(item.get('autoModels', True)) is not bool:
                raise ValueError('助手开关必须是布尔值')
            key = item.get('apiKey', '')
            if not isinstance(key, str) or len(key) > 8192:
                raise TypeError('API Key 无效')
            if not isinstance(item.get('baseUrl'), str):
                raise TypeError('服务地址必须是文本')
            base_url = _normalize_provider_base_url(item['baseUrl'])
            old = existing.get(identifier, {})
            if not key.strip() and old.get('encryptedKey') and base_url != _normalize_provider_base_url(old['baseUrl']):
                raise ValueError('修改服务地址后请填写新 API Key')
            encrypted = (encrypt_key(key.strip(), root) if key.strip()
                         else existing.get(identifier, {}).get('encryptedKey', ''))
            if not encrypted and urlsplit(base_url).hostname not in ('localhost', '127.0.0.1', '::1'):
                raise ValueError('请输入 API Key')
            saved.append({'id': identifier, 'name': str(item.get('name') or 'AI 接口')[:100],
                          'baseUrl': base_url,
                          'apiType': api_type, 'models': models, 'defaultModel': item['defaultModel'],
                          'enabled': item.get('enabled', True), 'autoModels': item.get('autoModels', True),
                          'preset': str(item.get('preset') or '0')[:10], 'encryptedKey': encrypted})
        write_json(config.ASSISTANT_PROVIDERS_FILE, saved)
        return public_providers()


def assistant_request(command: str, args: dict, root: Path):
    request = args.get('request', {})
    if not isinstance(request, dict):
        raise TypeError('助手请求必须是对象')
    if not isinstance(request.get('baseUrl'), str):
        raise TypeError('服务地址必须是文本')
    base_url = _normalize_provider_base_url(request['baseUrl'])
    key = request.get('apiKey', '')
    if not isinstance(key, str):
        raise TypeError('API Key 无效')
    if not key and request.get('providerId'):
        saved = next((item for item in read_json(config.ASSISTANT_PROVIDERS_FILE, [])
                      if item['id'] == request['providerId']), None)
        if saved is None:
            raise ValueError('助手配置已删除，请刷新页面')
        # 已保存密钥只能发送到对应配置原地址，避免修改测试地址后泄露旧 Key。
        if base_url != _normalize_provider_base_url(saved['baseUrl']):
            raise ValueError('修改服务地址后请填写新 API Key')
        encrypted = saved.get('encryptedKey', '')
        key = open_node_key(encrypted, root) if encrypted.startswith('node:v1:') else _decrypt_assistant_key(encrypted)
    try:
        if command == 'fetch_assistant_models':
            headers = {'Accept': 'application/json'}
            if key:
                headers['Authorization'] = 'Bearer ' + key
            with urllib.request.urlopen(urllib.request.Request(base_url + '/models', headers=headers), timeout=30) as response:
                data = json.load(response)
            models = [item['id'] for item in data.get('data', []) if isinstance(item, dict) and isinstance(item.get('id'), str)]
            return {'normalizedBaseUrl': base_url, 'models': models, 'message': '接口未返回模型'}
        model = request.get('model')
        messages = request.get('messages')
        if not isinstance(model, str) or not model or not isinstance(messages, list) or not messages:
            raise ValueError('请选择模型并填写测试消息')
        if len(messages) > 100 or len(json.dumps(messages)) > 100000:
            raise ValueError('测试对话过长，请重新打开测试窗口')
        if any(not isinstance(message, dict) or message.get('role') not in ('user', 'assistant', 'system', 'developer')
               or not isinstance(message.get('content'), str) for message in messages):
            raise ValueError('测试消息格式无效')
        if request.get('apiType') == 'OpenAI Responses API':
            # 优化器的单轮转换只提取首条用户消息；对话测试需要保留完整历史。
            headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
            if key:
                headers['Authorization'] = 'Bearer ' + key
            body = json.dumps({'model': model, 'input': messages}).encode()
            with urllib.request.urlopen(urllib.request.Request(base_url + '/responses', data=body,
                                         headers=headers, method='POST'), timeout=180) as response:
                data = json.load(response)
            text = '\n'.join(content.get('text', '') for output in data.get('output', [])
                             for content in output.get('content', []) if content.get('type') == 'output_text')
            if not text.strip():
                raise RuntimeError('模型未返回文本')
            return text.strip()
        provider = {'baseUrl': base_url, 'defaultModel': model, 'apiType': request.get('apiType')}
        return _provider_completion(provider, key)('test', {'messages': messages}, False)
    except (urllib.error.URLError, OSError, RuntimeError, json.JSONDecodeError) as error:
        # 不转发供应商可能含敏感请求字段的错误正文。
        raise ValueError('助手连接或响应失败，请检查地址、Key、模型和 API 类型') from error


def execute(command: str, args: dict, root: Path):
    if command in SETTING_COMMANDS:
        field, argument, choices = SETTING_COMMANDS[command]
        value = args.get(argument)
        valid = type(value) is bool if choices is bool else (
            not isinstance(value, bool) and value in choices)
        if not valid:
            raise ValueError('设置值无效')
        with LOCK:
            data = read_json(config.CONTROL_PANEL_SETTINGS_FILE, {})
            data[field] = value
            if field == 'acceleration_mode':
                data['sage_attention_enabled'] = value in ('sage_attention', 'turbo_8step_sage')
            write_json(config.CONTROL_PANEL_SETTINGS_FILE, data)
        return True
    if command == 'save_assistant_providers':
        return save_providers(args.get('providers'), root)
    if command in ('fetch_assistant_models', 'chat_assistant_api'):
        return assistant_request(command, args, root)
    if command == 'get_upscale_settings':
        return inspect_upscale_settings(args.get('path'), root)
    if command == 'save_upscale_settings':
        with LOCK:
            save_upscale_settings(args.get('settings'), root)
        return inspect_upscale_settings(root=root)
    if command == 'storage_paths':
        return {'output': str(config.WEB_OUTPUT_DIR), 'database': str(config.JOB_DATABASE_FILE)}
    raise KeyError(command)
