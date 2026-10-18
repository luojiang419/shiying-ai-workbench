"""透明 PNG 的独立 BiRefNet CPU 后处理，不依赖旧 Qwen 项目。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from .config import ROOT_DIR
from .image_background_runtime import prepare_dependencies
from .image_background_runtime import prepare_model as _prepare_model


def model_directory() -> Path:
    return ROOT_DIR / 'models/image-background/BiRefNet'


def prepare_model(emit=lambda *_: None) -> Path:
    # 在生图前完成下载，失败时不消耗一次 GPU 生成。
    dependencies = prepare_dependencies(ROOT_DIR, emit)
    _check_runtime(str(dependencies))
    return _prepare_model(ROOT_DIR, emit)


def _environment(dependencies: Path) -> dict:
    return {**os.environ, 'PYTHONPATH': str(dependencies), 'PYTHONNOUSERSITE': '1',
            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'CUDA_VISIBLE_DEVICES': '',
            'HF_MODULES_CACHE': str(ROOT_DIR / 'outputs/runtime/image-background-modules')}


@lru_cache(maxsize=2)
def _check_runtime(dependencies: str) -> None:
    result = subprocess.run(
        [sys.executable, '-s', '-c',
         'import torch, torchvision, timm, einops, kornia; from transformers import AutoModelForImageSegmentation'],
        env=_environment(Path(dependencies)), capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=120, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    if result.returncode:
        raise RuntimeError('透明背景依赖加载失败：' + result.stderr[-1500:])


def make_transparent(raw: bytes) -> bytes:
    dependencies = prepare_dependencies(ROOT_DIR)
    model = _prepare_model(ROOT_DIR)
    runtime = ROOT_DIR / 'outputs/runtime'
    with tempfile.TemporaryDirectory(prefix='image-background-', dir=runtime) as directory:
        source, target = Path(directory) / 'input.png', Path(directory) / 'output.png'
        source.write_bytes(raw)
        result = subprocess.run(
            [sys.executable, '-s', str(ROOT_DIR / 'tools/image_background/worker.py'),
             '--model', str(model), '--input', str(source), '--output', str(target)],
            env=_environment(dependencies), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        if result.returncode or not target.is_file():
            raise RuntimeError('透明背景处理失败：' + result.stderr[-1500:])
        return target.read_bytes()
