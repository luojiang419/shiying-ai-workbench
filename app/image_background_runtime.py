"""透明背景的固定魔塔资源与隔离 Python 依赖；不改动 H3 环境。"""
from __future__ import annotations

import json
import platform
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .clone_depth_runtime import download_verified, matches, safe_extract
from .h3_dependencies import _copy_verified, _paths, _valid_tree
from .process_lock import InterProcessLock

MODEL_FILES = {'config.json', 'BiRefNet_config.py', 'birefnet.py', 'model.safetensors'}
DEPENDENCY_MANIFEST = 'scripts/qwen21-background-dependencies.json'


def _version(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', value)


def _asset(spec, version=None):
    if not isinstance(spec.get('size'), int) or spec['size'] <= 0 or not re.fullmatch(r'[0-9a-f]{64}', spec.get('sha256', '')):
        raise ValueError('透明背景资源缺少有效大小或 SHA-256')
    if not spec.get('urls'):
        raise ValueError('透明背景资源缺少魔塔地址')
    for url in spec['urls']:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'modelscope.cn' or parsed.query or parsed.fragment or not parsed.path.startswith('/models/'):
            raise ValueError('透明背景资源必须从固定魔塔地址补齐')
        if not version and not re.fullmatch(r'/models/[^/]+/[^/]+/resolve/[0-9a-f]{40}/.+', parsed.path):
            raise ValueError('透明背景模型必须固定魔塔 revision')
        if version and f'/resolve/master/h3-deps/{version}/' not in parsed.path:
            raise ValueError('透明背景依赖必须使用固定版本目录')


def load_model_manifest(root: Path) -> dict:
    manifest = json.loads((root / 'scripts/qwen21-background-model.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or not _version(manifest.get('version')) or set(manifest.get('files', {})) != MODEL_FILES:
        raise ValueError('透明背景模型清单不完整或版本非法')
    for spec in manifest['files'].values():
        _asset(spec)
    return manifest


def load_dependency_manifest(root: Path) -> dict:
    manifest = json.loads((root / DEPENDENCY_MANIFEST).read_text(encoding='utf-8'))
    if (manifest.get('schema') != 1 or not _version(manifest.get('version'))
            or manifest.get('feature') != 'qwen-background' or manifest.get('python') != '3.12'
            or manifest.get('platform') != 'win_amd64'):
        raise ValueError('透明背景依赖清单版本或平台不受支持')
    if not {'timm', 'transformers', 'einops', 'safetensors', 'tokenizers', 'numpy', 'pillow', 'kornia', 'kornia-rs'} <= manifest.get('packages', {}).keys():
        raise ValueError('透明背景依赖清单不是完整快照')
    if manifest.get('base_packages') != ['torch', 'torchvision']:
        raise ValueError('透明背景必须复用工作台配套 PyTorch / torchvision')
    spec = manifest['archive']
    _asset(spec, manifest['version'])
    files = spec.get('files', {})
    required = {'python/timm/__init__.py', 'python/transformers/__init__.py', 'python/einops/__init__.py',
                'python/safetensors/__init__.py', 'python/tokenizers/__init__.py', 'python/numpy/__init__.py',
                'python/PIL/__init__.py', 'python/kornia/__init__.py', 'python/kornia_rs/__init__.py'}
    if not required <= files.keys():
        raise ValueError('透明背景依赖包遗漏必要文件')
    for name, digest in files.items():
        if (not name.startswith('python/') or ':' in name or '\\' in name or '..' in name.split('/')
                or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or name.startswith(('python/torch/', 'python/torchvision/'))):
            raise ValueError('透明背景依赖包含非法路径或试图替换 PyTorch')
    return manifest


def prepare_model(root: Path, emit=lambda *_: None) -> Path:
    manifest = load_model_manifest(root)
    target = root / 'models/image-background/BiRefNet'
    with InterProcessLock(root / 'outputs/runtime/image-background-model.lock', 1800):
        # 复用本项目其他功能已校验的同一份权重，不依赖旧 Qwen 工作台或用户 HF 缓存。
        shared = root / 'models/clone/person-mask/quality-birefnet'
        for name, spec in manifest['files'].items():
            destination = target / name
            if matches(destination, spec):
                continue
            if matches(shared / name, spec):
                emit(0, f'正在复用透明背景模型：{name}')
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + '.installing')
                shutil.copyfile(shared / name, temporary)
                temporary.replace(destination)
            else:
                emit(0, f'正在自动补齐透明背景模型：{name}')
                download_verified(spec['urls'], destination, spec, emit)
    return target


def prepare_dependencies(root: Path, emit=lambda *_: None) -> Path:
    if sys.platform == 'linux':
        from .linux_native import background_dependencies
        return background_dependencies(root, emit)
    manifest = load_dependency_manifest(root)
    if (sys.platform != 'win32' or sys.version_info[:2] != (3, 12)
            or platform.machine().lower() not in {'amd64', 'x86_64'}):
        raise RuntimeError('透明背景依赖需要工作台配套的 Windows x64 Python 3.12')
    spec = manifest['archive']
    runtime = root / 'outputs/runtime'
    target = runtime / 'image-background-python' / manifest['version']
    with InterProcessLock(runtime / 'image-background-dependencies.lock', 1200):
        if _valid_tree(target, spec, 'python/'):
            return target
        cache = runtime / 'image-background-downloads' / manifest['version'] / 'dependencies.zip'
        emit(0, '正在自动补齐透明背景 Python 依赖')
        archive = download_verified(spec['urls'], cache, spec, emit)
        with tempfile.TemporaryDirectory(prefix='image-background-install-', dir=runtime) as directory:
            staging = Path(directory)
            safe_extract(archive, staging, emit, lambda: False)
            # 所有文件验证成功后才安装；损坏/中断的目录下次仍会逐项复核。
            _copy_verified(staging / 'python', target, list(_paths(spec, 'python/')))
        emit(100, '透明背景 Python 依赖已就绪')
    return target
