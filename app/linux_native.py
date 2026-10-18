"""Linux deployment helpers; keep platform-specific binaries out of the node."""
import os
import subprocess
import sys
from pathlib import Path


def background_dependencies(root, emit=lambda *_: None):
    """Validate the native environment actually used by the background worker."""
    result = subprocess.run([sys.executable, '-c',
        'import torch,torchvision,timm,einops,kornia,safetensors; '
        'from transformers import AutoModelForImageSegmentation'],
        capture_output=True, text=True, timeout=120)
    if result.returncode:
        emit(0, '正在补齐 Linux 图片处理依赖')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '-r',
            str(Path(root) / 'deployment/native-repair.txt')], check=True, timeout=1800,
            stdout=subprocess.DEVNULL)
        result = subprocess.run([sys.executable, '-c',
            'import torch,torchvision,timm,einops,kornia,safetensors; '
            'from transformers import AutoModelForImageSegmentation'], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError('Linux background dependencies need repair: ' + result.stderr[-1500:])
    target = Path(root) / 'outputs/runtime/linux-background'
    target.mkdir(parents=True, exist_ok=True)
    return target


def protect_key(value, decrypt=False):
    """Per-node authenticated encryption, with a mode-0600 local master key."""
    from cryptography.fernet import Fernet
    root = Path(__file__).resolve().parents[1]
    keyfile = root / 'config/node-key-encryption.key'
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    if not keyfile.exists():
        if decrypt:
            raise RuntimeError('Node encryption key is missing; restore its protected backup')
        try:
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
    if keyfile.stat().st_mode & 0o077:
        raise RuntimeError('Node encryption key permissions must be 0600')
    cipher = Fernet(keyfile.read_bytes())
    if decrypt:
        if not value.startswith(b'linux-fernet:'):
            raise RuntimeError('This key was encrypted on a different platform')
        return cipher.decrypt(value[len(b'linux-fernet:'):])
    return b'linux-fernet:' + cipher.encrypt(value)


def llama_binary(root):
    path = Path(os.environ.get('MINIMAX_LLAMA_SERVER', str(Path(root) / 'vendor/clone-llama/llama-server')))
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError('Linux llama-server is missing: ' + str(path))
    return path


def prepare_managed_qwen(root, emit):
    """Join the managed installer, then verify/reuse or repair image assets."""
    from .qwen_node_profile import MANAGED, prepare_shared_models
    if MANAGED:
        from .config import COMFY_DIR
        return prepare_shared_models(COMFY_DIR, emit)
    import time
    import psutil
    script = str(Path(root) / 'deployment/prepare_models.py')
    deadline = time.monotonic() + 6 * 3600
    while any(script in (p.info['cmdline'] or []) for p in psutil.process_iter(['cmdline'])):
        if time.monotonic() > deadline:
            raise TimeoutError('模型准备超过六小时，请检查 logs/model-preparation.log')
        emit(0, '算力节点正在校验或补齐模型，请稍候')
        time.sleep(5)
    emit(0, '校验节点图片模型')
    log = Path(root) / 'logs/qwen-model-repair.log'
    env = dict(os.environ, MINIMAX_COMFY_DIR='/root/ComfyUI')
    with log.open('a') as output:
        result = subprocess.run([sys.executable, script, '--qwen-only'], env=env,
            stdout=output, stderr=subprocess.STDOUT, timeout=6*3600)
    if result.returncode:
        raise RuntimeError('图片模型补齐失败，请检查 logs/qwen-model-repair.log')
    return True
