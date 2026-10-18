"""Native Qwen 2.1 profiles for managed AutoDL nodes."""
import os
import subprocess
import csv
import io
from pathlib import Path

BF16_MODEL = 'qwen_image_2.1_bf16.safetensors'
INT8_MODEL = 'qwen_image_2.1_int8_convrot.safetensors'
LEGACY_MODEL = 'qwen-image-2.1-UC-Q6_K.gguf'
MANAGED = os.name != 'nt' and os.environ.get('MINIMAX_MANAGED_NODE') == '1'


def detect_gpu():
    try:
        output = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=index,uuid,name,memory.total,compute_cap,driver_version', '--format=csv,noheader,nounits'],
            text=True, timeout=5,
        )
        devices = list(csv.reader(io.StringIO(output)))
        visible = os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',')[0].strip()
        rows = [row for row in devices if not visible or row[0].strip() == visible or row[1].strip().startswith(visible)]
        if not rows:
            raise ValueError('No matching visible GPU')
        row = rows[0]
        return {'index': row[0].strip(), 'uuid': row[1].strip(), 'name': row[2].strip(), 'memory_mib': int(row[3]),
                'compute_cap': row[4].strip() if len(row)>4 else '', 'driver': row[5].strip() if len(row)>5 else ''}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {'name': 'unknown', 'memory_mib': 0}


def capability(gpu):
    try:
        return float(gpu.get('compute_cap', ''))
    except ValueError:
        name=gpu['name'].lower()
        if 'rtx 20' in name or 'turing' in name: return 7.5
        if 'rtx 30' in name: return 8.6
        if 'rtx 40' in name or 'ada' in name: return 8.9
        if 'rtx 50' in name or 'blackwell' in name: return 12.0
        return 0.0


def model_for_gpu(gpu):
    # Turing lacks native BF16; use unquantized weights with FP32 CPU offload.
    if 7.5 <= capability(gpu) < 8.0:
        return BF16_MODEL
    # The 5090 always uses the tested 32GB profile, including unusual VRAM reports.
    if '5090' in gpu['name']:
        return INT8_MODEL
    return BF16_MODEL if gpu['memory_mib'] >= 80 * 1024 else INT8_MODEL


GPU = detect_gpu() if MANAGED else {'name': 'unmanaged', 'memory_mib': 0}
MODEL = model_for_gpu(GPU) if MANAGED else LEGACY_MODEL
CLIP = 'qwen3vl_8b_bf16.safetensors' if MODEL == BF16_MODEL else 'qwen3vl_8b_int8_convrot.safetensors'
LOADER = 'UNETLoader' if MANAGED else 'UnetLoaderGGUF'
MODEL_FIELD = 'unet_name'
LOW_VRAM = MANAGED and 0 < GPU['memory_mib'] <= 18 * 1024
LEGACY_PRECISION = MANAGED and 7.5 <= capability(GPU) < 8.0


def prepare_shared_models(comfy_dir, emit):
    """Link existing public-library weights; never copy multi-GB assets to the system disk."""
    assets = {
        ('diffusion_models', MODEL): Path('/root/ComfyUI/models/diffusion_models/qwen') / MODEL,
        ('text_encoders', CLIP): Path('/root/ComfyUI/models/text_encoders') / CLIP,
        ('vae', 'qwen_image_2.1_vae_bf16.safetensors'): Path('/root/ComfyUI/models/vae/qwen_image_2.1_vae_bf16.safetensors'),
    }
    for (category, name), source in assets.items():
        target = Path(comfy_dir) / 'models' / category / name
        if target.is_file():
            if not target.resolve().is_relative_to(Path('/.autodl-model/data')):
                raise RuntimeError(f'模型必须链接 AutoDL 公共库，拒绝使用系统盘副本：{target}')
        else:
            if not source.is_file() or not source.resolve().is_relative_to(Path('/.autodl-model/data')):
                raise RuntimeError(f'AutoDL 公共库模型缺失，请挂载：{source}')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            target.symlink_to(source.resolve())
        if target.stat().st_size < 1024 * 1024:
            raise RuntimeError(f'模型文件不完整：{target}')
    emit(100, f'GPU {GPU["name"]}，公共库模型已就绪：{MODEL} / {CLIP}')
    return True
