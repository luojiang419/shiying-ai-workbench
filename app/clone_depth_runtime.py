"""深度模型按需补齐：复用可用Python，否则安装魔搭独立运行时。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .process_lock import InterProcessLock

DEPTH_MODELS = {
    "small": {"key": "vda_small_fp16_relative", "label": "Small · 轻量", "file": "video-depth-anything-small/video_depth_anything_vits.pth", "license": "Apache-2.0"},
    "base": {"key": "vda_base_fp16_relative", "label": "Base · 高质量", "file": "video-depth-anything-base/video_depth_anything_vitb.pth", "license": "CC-BY-NC-4.0，仅限非商业用途"},
}
MASK = "person-mask/human_segmentation_pphumanseg_2023mar.onnx"
QUALITY_MASK_FILES = (
    "person-mask/quality-birefnet/BiRefNet_config.py",
    "person-mask/quality-birefnet/birefnet.py",
    "person-mask/quality-birefnet/config.json",
    "person-mask/quality-birefnet/model.safetensors",
)
QUALITY_DEPENDENCY_ARCHIVE = "clone-quality-python-1.0.0-win-py312.zip"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def check_canceled(canceled):
    if canceled():
        raise InterruptedError("资源准备已取消，下载进度已保留")


def matches(path, spec, canceled=lambda: False, emit=None):
    if not path.is_file() or path.stat().st_size != spec["size"]:
        return False
    digest = hashlib.sha256()
    checked = 0
    last = 0.0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            check_canceled(canceled)
            digest.update(block)
            checked += len(block)
            if emit and time.monotonic() - last >= 0.4:
                emit(round(checked * 100 / spec["size"]), f"正在校验 {path.name} · {checked / 1048576:.1f}/{spec['size'] / 1048576:.1f} MB")
                last = time.monotonic()
    return digest.hexdigest() == spec["sha256"].lower()


def download_verified(urls, target, spec, emit=lambda *_: None, canceled=lambda: False):
    """校验后原子替换；支持Range续传、200重下、断点损坏恢复及取消。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    emit(0, f"正在校验 {target.name}")
    if matches(target, spec, canceled, emit):
        return target
    if target.is_file():
        emit(0, f"校验未通过，自动重新下载 {target.name}")
    part = target.with_name(target.name + ".part")
    errors = []
    for url in urls:
        for attempt in range(2):
            check_canceled(canceled)
            if part.exists() and part.stat().st_size >= spec["size"]:
                if matches(part, spec, canceled, emit):
                    part.replace(target)
                    return target
                emit(0, f"下载断点校验未通过，重新下载 {target.name}")
                part.unlink()
            offset = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": "MiniMax-H3-depth/1.0"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status == 206:
                        content_range = response.headers.get("Content-Range", "")
                        if not content_range.startswith(f"bytes {offset}-") or not content_range.endswith(f"/{spec['size']}"):
                            raise ValueError("下载断点响应不匹配")
                    elif response.status == 200:
                        offset = 0
                    else:
                        raise ValueError(f"下载失败 HTTP {response.status}")
                    last = 0.0
                    with part.open("ab" if offset else "wb") as stream:
                        while True:
                            check_canceled(canceled)
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            stream.write(block)
                            offset += len(block)
                            if offset > spec["size"]:
                                raise ValueError("下载文件超过清单大小")
                            if time.monotonic() - last >= 0.4:
                                emit(round(offset * 100 / spec["size"]), f"魔搭下载 {target.name} · {offset / 1048576:.1f}/{spec['size'] / 1048576:.1f} MB")
                                last = time.monotonic()
                emit(99, f"正在验证SHA-256：{target.name}")
                if not matches(part, spec, canceled, emit):
                    # 已完成但校验不符不能继续拼接到坏断点。
                    if part.stat().st_size >= spec["size"]:
                        part.unlink()
                        emit(0, f"下载校验失败，自动重新下载 {target.name}")
                    raise ValueError("大小或SHA-256校验失败")
                part.replace(target)
                emit(100, f"已就绪：{target.name}")
                return target
            except InterruptedError:
                raise
            except (OSError, ValueError, urllib.error.URLError) as error:
                errors.append(f"{target.name} 第{attempt + 1}次：{error}")
    raise RuntimeError("资源下载失败，可重试续传：" + "；".join(errors)[-1200:])


def gpu_info():
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=compute_cap,memory.total,driver_version", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10, creationflags=FLAGS, check=True)
        rows = []
        for line in result.stdout.strip().splitlines():
            capability, memory, driver = [value.strip() for value in line.split(",")]
            rows.append({"capability": float(capability), "memory": int(memory) * 1024**2, "driver": tuple(int(v) for v in driver.split("."))})
        return rows
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def select_variant(manifest, gpus=None):
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("自动运行时仅支持Windows x64，请部署当前系统的Python深度依赖")
    gpus = gpu_info() if gpus is None else gpus
    allow_cpu = os.environ.get("MINIMAX_ALLOW_CPU_INFERENCE") == "1"
    for variant in sorted(manifest["variants"], key=lambda item: item["priority"], reverse=True):
        rule = variant["constraints"]
        if rule["accelerator"] == "cpu":
            if allow_cpu:
                return variant
            continue
        minimum_driver = tuple(int(v) for v in rule["min_driver_version"].split("."))
        if any(rule["min_compute_capability"] <= gpu["capability"] <= rule.get("max_compute_capability", float("inf"))
               and gpu["memory"] >= rule["min_gpu_memory_bytes"] and gpu["driver"] >= minimum_driver for gpu in gpus):
            return variant
    if gpus:
        raise RuntimeError("检测到 NVIDIA 显卡，但显存、驱动版本或算力不满足现有 CUDA 深度运行时要求；请更新显卡驱动后重试，不会自动回退到 CPU")
    raise RuntimeError("未检测到可用的 NVIDIA CUDA 显卡；请检查驱动和 nvidia-smi，深度推理不会自动回退到 CPU")


def find_tool(root, name):
    suffix = ".exe" if os.name == "nt" else ""
    paths = [root / "runtime/ffmpeg" / (name + suffix)]
    paths += sorted((root / "runtime/clone-depth").glob(f"*/runtime/bin/{name}{suffix}"))
    return next((str(path) for path in paths if path.is_file()), shutil.which(name))


def source_runtime(root, needs_cuda=False):
    worker = root / "tools/clone_depth/worker/main.py"
    source = root / "third_party/video-depth-anything/video_depth_anything/video_depth.py"
    if not worker.is_file() or not source.is_file() or not find_tool(root, "ffmpeg") or not find_tool(root, "ffprobe"):
        return None
    code = (f"import sys; sys.path.insert(0, {str(source.parent.parent)!r}); "
            "import torch,cv2,numpy; from video_depth_anything.video_depth import VideoDepthAnything; "
            "ok=torch.cuda.is_available(); "
            "x=torch.ones((1,3,8,8),device='cuda') if ok else None; "
            "y=torch.nn.Conv2d(3,3,3).cuda()(x) if ok else None; "
            "print('CUDA_OK' if ok and y.is_cuda else 'CPU_OK')")
    try:
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, creationflags=FLAGS)
        if result.returncode or (needs_cuda and "CUDA_OK" not in result.stdout):
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    return {"command": [sys.executable, str(worker)], "sources": root / "third_party", "bin": Path(find_tool(root, "ffmpeg")).parent, "mode": "python"}


def safe_extract(archive, destination, emit, canceled):
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        for index, item in enumerate(members):
            check_canceled(canceled)
            relative = item.filename.replace("\\", "/")
            path = (destination / relative).resolve()
            if not path.is_relative_to(destination) or ":" in relative or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("运行时压缩包包含非法路径")
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, path.open("wb") as output:
                while block := source.read(4 * 1024 * 1024):
                    check_canceled(canceled)
                    output.write(block)
            if index % 50 == 0:
                emit(round(index * 100 / max(1, len(members))), f"正在安装运行时 · {index + 1}/{len(members)}")


def prepare_quality_dependencies(root, manifest, emit, canceled):
    """从魔塔补齐质量分割依赖，在当前任务中立即用于 worker。"""
    if sys.platform == 'linux':
        from .linux_native import background_dependencies
        return background_dependencies(root, emit)
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"} or sys.version_info[:2] != (3, 12):
        raise RuntimeError("quality 人物分割依赖包需要 Windows x64 Python 3.12")
    spec = manifest["quality_python_dependencies"]
    target = root / "outputs/runtime/clone-quality-pydeps" / spec["version"]
    ready_path = target / "ready.json"
    try:
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ready = {}
    required = ("timm/__init__.py", "transformers/__init__.py", "safetensors/__init__.py",
                "tokenizers/__init__.py", "quality-deps.json")
    if ready.get("sha256") == spec["sha256"] and all((target / path).is_file() for path in required):
        return target
    cache = root / "outputs/runtime/clone-quality-downloads" / QUALITY_DEPENDENCY_ARCHIVE
    archive = download_verified(spec["urls"], cache, spec, emit, canceled)
    emit(0, "正在安装 quality 人物分割 Python 依赖")
    safe_extract(archive, target, emit, canceled)
    package_info = json.loads((target / "quality-deps.json").read_text(encoding="utf-8"))
    if package_info.get("schema") != 1 or package_info.get("python") != "3.12" or package_info.get("platform") != "win_amd64":
        raise RuntimeError("quality 依赖包与当前 Python 环境不匹配")
    environment = {**os.environ, "PYTHONPATH": str(target) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    result = subprocess.run([sys.executable, "-c", "import timm, transformers, safetensors, tokenizers; from transformers import AutoModelForImageSegmentation"],
                            env=environment, capture_output=True, text=True, timeout=120, creationflags=FLAGS)
    if result.returncode:
        raise RuntimeError("quality 人物分割依赖加载失败：" + result.stderr[-900:])
    temporary = ready_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": spec["version"], "sha256": spec["sha256"]}), encoding="utf-8")
    temporary.replace(ready_path)
    emit(100, "quality 人物分割依赖已就绪")
    return target


def install_runtime(root, manifest, variant, emit, canceled):
    target = root / "runtime/clone-depth" / variant["id"]
    try:
        ready = json.loads((target / "ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ready = {}
    if ready.get("version") == manifest["version"] and ready.get("adapter") == 1 and all((target / name).is_file() for name in variant["required_paths"]) and (target / "runtime/video-depth-worker/_internal/numpy/__init__.pyc").is_file():
        return target
    (target / "ready.json").unlink(missing_ok=True)
    packages = {item["id"]: item for item in manifest["packages"]}
    selected = [packages[key] for key in variant["packages"]]
    cache = root / "runtime/clone-depth-downloads"
    cache.mkdir(parents=True, exist_ok=True)
    total = sum(item["size"] for item in selected)
    if shutil.disk_usage(cache).free < total * 3:
        raise RuntimeError(f"深度运行时安装空间不足，请至少留出 {total * 3 / 1024**3:.1f} GB")
    parts = []
    for item in selected:
        parts.append(download_verified([item["domestic_url"]], cache / item["file"], item, emit, canceled))
    archive = parts[0]
    if "assemble_archive" in variant:
        archive = cache / (variant["id"] + ".zip")
        if not matches(archive, variant["assemble_archive"], canceled):
            emit(0, "正在合并运行时分块")
            staging_archive = archive.with_suffix(".assembling")
            with staging_archive.open("wb") as output:
                for part in parts:
                    with part.open("rb") as source:
                        while block := source.read(4 * 1024 * 1024):
                            check_canceled(canceled)
                            output.write(block)
            if not matches(staging_archive, variant["assemble_archive"], canceled):
                raise ValueError("运行时合并校验失败")
            staging_archive.replace(archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="depth-install-", dir=target.parent) as temp:
        staging = Path(temp)
        safe_extract(archive, staging, emit, canceled)
        if not all((staging / name).is_file() for name in variant["required_paths"]):
            raise RuntimeError("运行时包缺少必需文件")
        from tools.clone_depth.runtime_adapter import unpack_dependencies

        emit(98, "正在适配运行时依赖与当前深度处理器")
        unpack_dependencies(staging / variant["command"][0], staging / "runtime/video-depth-worker/_internal", canceled)
        # target仅为此受管理变体目录；中断的安装可覆盖恢复，最后才写ready。
        shutil.copytree(staging, target, dirs_exist_ok=True)
        (target / "ready.json").write_text(json.dumps({"version": manifest["version"], "variant": variant["id"], "adapter": 1}), encoding="utf-8")
    return target


def prepare_depth(root, model="small", remove_background=False, emit=lambda *_: None, canceled=lambda: False,
                  person_mask_model="lite"):
    root = Path(root)
    if model not in DEPTH_MODELS:
        raise ValueError("深度模型无效")
    if person_mask_model not in {"lite", "quality"}:
        raise ValueError("人物分割档位无效")
    lock = InterProcessLock(root / "runtime/clone-depth-install.lock", 1)
    while True:
        check_canceled(canceled)
        try:
            lock.__enter__()
            break
        except TimeoutError:
            emit(0, "等待另一个任务完成资源准备")
    try:
        manifest = json.loads((root / "scripts/clone-models.json").read_text(encoding="utf-8"))
        runtime_manifest = json.loads((root / "scripts/clone-depth-runtime.json").read_text(encoding="utf-8"))
        emit(0, "正在检查深度推理环境与 CUDA")
        allow_cpu = os.environ.get("MINIMAX_ALLOW_CPU_INFERENCE") == "1"
        runtime = source_runtime(root, needs_cuda=not allow_cpu)
        variant = select_variant(runtime_manifest) if runtime is None and os.name == "nt" else None
        if runtime is None and variant is None:
            raise RuntimeError("当前 Python 无法执行 CUDA 深度推理，且此系统没有可用的自动运行时")
        if remove_background and person_mask_model == "quality" and (runtime is None or runtime["mode"] != "python"):
            raise RuntimeError("quality 人物分割需要包含 transformers、timm 的完整 Python 运行时")
        quality_deps = prepare_quality_dependencies(root, manifest, emit, canceled) if remove_background and person_mask_model == "quality" else None
        names = [DEPTH_MODELS[model]["file"]]
        if remove_background:
            names.extend(QUALITY_MASK_FILES if person_mask_model == "quality" else (MASK,))
        for name in names:
            spec = manifest["models"]["depth/" + name]
            download_verified(spec["urls"], root / "models/clone/depth" / name, spec, emit, canceled)
        check_canceled(canceled)
        if runtime is None:
            installed = install_runtime(root, runtime_manifest, variant, emit, canceled)
            runtime = {"command": [sys.executable, "-I", "-S", "-X", "utf8", str(root / "tools/clone_depth/runtime_adapter.py"),
                                   str(installed / "runtime/video-depth-worker/_internal"), str(root / "tools/clone_depth/worker/main.py")],
                       "sources": installed / "runtime/sources", "bin": installed / "runtime/bin", "mode": variant["id"]}
        check_canceled(canceled)
        env = {**os.environ, "PYTHONUTF8": "1", "SHIYIN_VIDEO_DEPTH_MODEL_ROOT": str(root / "models/clone/depth"), "SHIYIN_VIDEO_DEPTH_SOURCE_ROOT": str(runtime["sources"])}
        if quality_deps is not None:
            env["PYTHONPATH"] = str(quality_deps) + os.pathsep + env.get("PYTHONPATH", "")
        env["PATH"] = str(runtime["bin"]) + os.pathsep + env.get("PATH", "")
        runtime["env"] = env
        runtime["ffmpeg"] = str(runtime["bin"] / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
        emit(100, f"{DEPTH_MODELS[model]['label']} 模型与运行时已就绪")
        return runtime
    finally:
        lock.__exit__(None, None, None)
