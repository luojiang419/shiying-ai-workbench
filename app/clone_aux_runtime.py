"""按任务补齐 Qwen 视觉解析与 Real-ESRGAN 权重。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .clone_depth_runtime import FLAGS, check_canceled, download_verified
from .process_lock import InterProcessLock

LLAMA_REQUIRED = ("llama-server.exe", "llama-server-impl.dll", "ggml-cuda.dll", "cudart64_12.dll")
REFERENCE_NAME = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TRACKING_MODEL_NAME = "tracking/sam2.1_hiera_tiny.pt"


def _manifest(root):
    return json.loads((root / "scripts/clone-models.json").read_text(encoding="utf-8"))


def _install_llama(root, manifest, emit, canceled):
    if sys.platform == 'linux':
        from .linux_native import llama_binary
        try:
            binary = llama_binary(root)
            subprocess.run([str(binary), '--version'], check=True, capture_output=True, timeout=30)
        except (RuntimeError, subprocess.CalledProcessError):
            emit(0, '正在补齐 Linux 克隆推理运行库')
            with (root / 'logs/llama-build.log').open('a') as log:
                subprocess.run([sys.executable, str(root / 'deployment/build-llama.py')],
                    check=True, stdout=log, stderr=subprocess.STDOUT, timeout=7200)
        return
    target = root / "vendor/clone-llama"
    try:
        ready = json.loads((target / "ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ready = {}
    if ready.get("release") == manifest["llama_release"] and all((target / name).is_file() for name in LLAMA_REQUIRED):
        return

    archives = []
    archive_specs = list(manifest["llama_archives"].items())
    for index, (name, spec) in enumerate(archive_specs):
        archive_progress = lambda percent, message, index=index: emit(
            round((index + percent / 100) * 90 / len(archive_specs)), message
        )
        archive_progress(0, f"正在准备 Qwen 运行程序：{name}")
        archives.append(download_verified(spec["urls"], root / "vendor/clone-downloads" / name, spec, archive_progress, canceled))
    target.parent.mkdir(parents=True, exist_ok=True)
    emit(90, "正在安装 Qwen 运行程序")
    with tempfile.TemporaryDirectory(prefix="clone-llama-", dir=target.parent) as temp:
        staging = Path(temp)
        for archive in archives:
            with zipfile.ZipFile(archive) as bundle:
                for item in bundle.infolist():
                    check_canceled(canceled)
                    if item.is_dir():
                        continue
                    # 官方包是平铺 DLL/EXE；拒绝路径与重复文件，避免覆盖任意目录。
                    name = item.filename
                    if name != Path(name).name or name in {"", ".", ".."} or (staging / name).exists():
                        raise ValueError("Qwen 运行程序压缩包包含非法或重复文件")
                    with bundle.open(item) as source, (staging / name).open("wb") as output:
                        while block := source.read(4 * 1024 * 1024):
                            check_canceled(canceled)
                            output.write(block)
        if not all((staging / name).is_file() for name in LLAMA_REQUIRED):
            raise RuntimeError("Qwen 运行程序缺少必需文件")
        target.mkdir(parents=True, exist_ok=True)
        for file in staging.iterdir():
            check_canceled(canceled)
            file.replace(target / file.name)
        (target / "ready.json").write_text(json.dumps({"release": manifest["llama_release"]}), encoding="utf-8")
    emit(100, "Qwen 运行程序已就绪")


def _prepare_tracking_source(root, manifest, emit, canceled):
    spec = manifest["tracking_python_source"]
    target = root / "outputs/runtime/clone-tracking-python" / spec["version"]
    source = target / spec["root"]
    ready_path = target / "ready.json"
    required = ("sam2/__init__.py", "sam2/build_sam.py", "sam2/configs/sam2.1/sam2.1_hiera_t.yaml")
    try:
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ready = {}
    if ready.get("sha256") != spec["sha256"] or not all((source / path).is_file() for path in required):
        archive = download_verified(
            spec["urls"],
            root / "outputs/runtime/clone-tracking-downloads" / "sam2-source.zip",
            spec,
            lambda percent, message: emit(round(percent * .6), message),
            canceled,
        )
        emit(60, "正在安装 SAM 2 视频跟踪运行时")
        prefix = spec["root"] + "/sam2/"
        target_root = target.resolve()
        with zipfile.ZipFile(archive) as bundle:
            members = [item for item in bundle.infolist() if item.filename.startswith(prefix)]
            for index, item in enumerate(members):
                check_canceled(canceled)
                relative = item.filename.replace("\\", "/")
                path = (target / relative).resolve()
                mode = (item.external_attr >> 16) & 0o170000
                if not path.is_relative_to(target_root) or ":" in relative:
                    raise ValueError("SAM 2 源码包包含非法路径")
                if mode == 0o120000:
                    continue
                if item.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as input_file, path.open("wb") as output_file:
                    while block := input_file.read(4 * 1024 * 1024):
                        check_canceled(canceled)
                        output_file.write(block)
                if index % 25 == 0:
                    emit(60 + round(index * 35 / max(1, len(members))),
                         f"正在安装 SAM 2 · {index + 1}/{len(members)}")
        if not all((source / path).is_file() for path in required):
            raise RuntimeError("SAM 2 官方源码包缺少必需文件")
        temporary = ready_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": spec["version"], "sha256": spec["sha256"]}), encoding="utf-8")
        temporary.replace(ready_path)
    environment = {**os.environ, "PYTHONPATH": str(source) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    result = subprocess.run(
        [sys.executable, "-c", "from sam2.build_sam import build_sam2_video_predictor"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=FLAGS,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("SAM 2 视频跟踪运行时加载失败：" + result.stderr[-900:])
    emit(100, "SAM 2 视频跟踪运行时已就绪")
    return source


def prepare_auxiliary(root, *, qwen=False, upscale=False, reference=False, tracking=False,
                      comfy_dir=None, emit=lambda *_: None, canceled=lambda: False):
    """串行准备所需资源；已有文件也经过清单校验，损坏文件重新下载。"""
    root = Path(root)
    lock = InterProcessLock(root / "runtime/clone-aux-install.lock", 1)
    while True:
        check_canceled(canceled)
        try:
            lock.__enter__()
            break
        except TimeoutError:
            emit(0, "等待其他任务完成模型准备")
    try:
        manifest = _manifest(root)
        tracking_source = None
        names = []
        if qwen:
            names.extend(("qwen/mmproj.gguf", "qwen/Qwen3-VL-4B-Q4_K_M.gguf"))
        if upscale:
            names.append("upscale/RealESRGAN_x2plus.pth")
        if tracking:
            names.append(TRACKING_MODEL_NAME)
        total = len(names) + int(qwen) + int(reference) + int(tracking)
        for index, name in enumerate(names):
            spec = manifest["models"][name]
            step_emit = lambda percent, message, index=index: emit(round((index + percent / 100) * 100 / total), message)
            download_verified(spec["urls"], root / "models/clone" / name, spec, step_emit, canceled)
            emit(round((index + 1) * 100 / total), f"已就绪：{Path(name).name}")
        if reference:
            spec = manifest["reference_model"]
            index = len(names)
            target = (Path(comfy_dir) if comfy_dir is not None else root / "vendor/ComfyUI") / "models/diffusion_models" / REFERENCE_NAME
            step_emit = lambda percent, message: emit(round((index + percent / 100) * 100 / total), message)
            download_verified(spec["urls"], target, spec, step_emit, canceled)
            emit(round((index + 1) * 100 / total), "Ref2VA 权重已就绪")
            # 兼容旧分块下载脚本遗留的缓存；完整权重校验成功后才清理。
            old_cache = root / "vendor/clone-downloads/ref2va"
            if old_cache.is_dir():
                for part in old_cache.glob("[0-9][0-9][0-9].part"):
                    part.unlink()
                if not any(old_cache.iterdir()):
                    old_cache.rmdir()
        if qwen:
            completed = len(names) + int(reference)
            _install_llama(root, manifest, lambda percent, message: emit(round((completed + percent / 100) * 100 / total), message), canceled)
            emit(round((completed + 1) * 100 / total), "复刻所需模型已就绪")
        if tracking:
            completed = len(names) + int(reference) + int(qwen)
            tracking_source = _prepare_tracking_source(
                root,
                manifest,
                lambda percent, message: emit(round((completed + percent / 100) * 100 / total), message),
                canceled,
            )
        check_canceled(canceled)
        return {"tracking_python": str(tracking_source) if tracking_source else None}
    finally:
        lock.__exit__(None, None, None)


def cleanup_llama_archives(root):
    """Qwen 运行成功后删除受管理的压缩包，保留安装文件与未完成任务的断点。"""
    root = Path(root)
    manifest = _manifest(root)
    target = root / "vendor/clone-llama"
    try:
        ready = json.loads((target / "ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if ready.get("release") != manifest["llama_release"] or not all((target / name).is_file() for name in LLAMA_REQUIRED):
        return
    with InterProcessLock(root / "runtime/clone-aux-install.lock", 10):
        cache = root / "vendor/clone-downloads"
        for name in manifest["llama_archives"]:
            (cache / name).unlink(missing_ok=True)
            (cache / (name + ".part")).unlink(missing_ok=True)
