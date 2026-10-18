"""启动本地 H3 前，从固定魔塔清单补齐插件和必要的后端兼容依赖。"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from .clone_depth_runtime import download_verified, safe_extract
from .process_lock import InterProcessLock


def load_manifest(root: Path, manifest_name: str | None = None) -> dict:
    if manifest_name is None:
        manifest_name = "scripts/h3-dependencies.json"
        marker = root / "outputs/runtime/qwen-compat-active.json"
        candidate = root / "scripts/qwen21-dependencies.json"
        if marker.is_file() and candidate.is_file():
            try:
                active = json.loads(marker.read_text(encoding="utf-8"))
                feature = json.loads(candidate.read_text(encoding="utf-8"))
                if active.get("version") == feature.get("version"):
                    return feature
            except (OSError, ValueError):
                pass
    return json.loads((root / manifest_name).read_text(encoding="utf-8"))


def backend_python_path(root: Path, manifest: dict | None = None) -> Path | None:
    manifest = manifest or load_manifest(root)
    target = root / "outputs/runtime/h3-python" / manifest["version"]
    marker = target / "ready.json"
    try:
        if json.loads(marker.read_text(encoding="utf-8")).get("sha256") == manifest["backend"]["sha256"]:
            return target
    except (OSError, ValueError):
        pass
    return None


def _version(path: Path) -> tuple[int, ...]:
    try:
        match = re.search(r'__version__\s*=\s*[\'"]([\d.]+)', path.read_text(encoding="utf-8"))
        return tuple(map(int, match[1].split("."))) if match else ()
    except OSError:
        return ()


def _matches(path: Path, digest: str) -> bool:
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest


def _paths(spec: dict, prefix: str = ""):
    for relative, digest in spec["files"].items():
        normalized = relative.replace("\\", "/")
        if ":" in normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("H3 依赖清单包含非法路径")
        if normalized.startswith(prefix):
            yield normalized[len(prefix):], digest


def _valid_tree(target: Path, spec: dict, prefix: str = "") -> bool:
    files = list(_paths(spec, prefix))
    return bool(files) and all(_matches(target / name, digest) for name, digest in files)


def _copy_verified(source: Path, target: Path, entries) -> None:
    for relative, digest in entries:
        src = source / relative
        if not _matches(src, digest):
            raise ValueError(f"H3 依赖文件校验失败：{relative}")
    for relative, digest in entries:
        src, dst = source / relative, target / relative
        if _matches(dst, digest):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        temporary = dst.with_name(dst.name + ".h3-installing")
        shutil.copy2(src, temporary)
        temporary.replace(dst)


def prepare_h3_dependencies(root: Path, comfy_dir: Path, emit=lambda *_: None, before_install=lambda: None,
                            manifest_name: str | None = None) -> bool:
    """校验、下载、安装按顺序执行；调用方在修改运行中的后端前确认归属及空闲。"""
    root, comfy_dir = root.resolve(), comfy_dir.resolve()
    if comfy_dir != root / "vendor/ComfyUI" or not (comfy_dir / "main.py").is_file():
        return False
    manifest = load_manifest(root, manifest_name)
    if manifest.get("schema") != 1:
        raise RuntimeError("H3 依赖清单版本不受支持")
    runtime = root / "outputs/runtime"
    changed = False
    with InterProcessLock(runtime / "h3-dependencies.lock", timeout_seconds=1200):
        emit(0, "第一步：正在检查本地 H3 生成依赖")
        nodes_needed = not _valid_tree(comfy_dir, manifest["nodes"])
        current_version = _version(comfy_dir / "comfyui_version.py")
        required_version = tuple(map(int, manifest["comfy_version"].split(".")))
        core_needed = current_version < required_version
        if manifest.get("backend_revision"):
            if current_version > required_version:
                raise RuntimeError("本地 ComfyUI 高于已验证的 H3 核心版本，已停止自动覆盖；请使用匹配的本地运行时")
            # 同一个上游版本也可能需要定向兼容补丁，不能只比较版本号。
            core_needed = not _valid_tree(comfy_dir, manifest["backend"], "comfy/")
        python_target = runtime / "h3-python" / manifest["version"]
        overlay = backend_python_path(root, manifest)
        python_needed = not (overlay and _valid_tree(overlay, manifest["backend"], "python/"))
        if python_needed and overlay is None:
            try:
                python_needed = any(metadata.version(name) != version for name, version in manifest["packages"].items())
            except metadata.PackageNotFoundError:
                python_needed = True
        selected = (["backend"] if core_needed or python_needed else []) + (["nodes"] if nodes_needed else [])
        if not selected:
            emit(100, "本地 H3 依赖已齐全")
            return False
        if (core_needed or python_needed) and (sys.platform != "win32" or sys.version_info[:2] != (3, 12)
                or platform.machine().lower() not in {"amd64", "x86_64"}):
            raise RuntimeError("H3 后端兼容包需要 Windows x64 Python 3.12，请使用随工作台分发的 Python")
        cache = runtime / "h3-downloads" / manifest["version"]
        # 先完成所有下载及逐文件校验，再停止空闲后端并安装。
        with tempfile.TemporaryDirectory(prefix="h3-install-", dir=runtime) as directory:
            staging = Path(directory)
            for key in selected:
                spec = manifest[key]
                archive = download_verified(spec["urls"], cache / f"{key}.zip", spec, emit)
                destination = staging / key
                safe_extract(archive, destination, emit, lambda: False)
                if not _valid_tree(destination, spec):
                    raise ValueError(f"H3 {key} 依赖解压校验失败")
            before_install()
            emit(0, "正在安装 H3 生成依赖")
            if core_needed:
                source = staging / "backend/comfy"
                _copy_verified(source, comfy_dir, list(_paths(manifest["backend"], "comfy/")))
                changed = True
            if python_needed:
                (python_target / "ready.json").unlink(missing_ok=True)
                _copy_verified(staging / "backend/python", python_target, list(_paths(manifest["backend"], "python/")))
                (python_target / "ready.json").write_text(json.dumps({"sha256": manifest["backend"]["sha256"]}), encoding="utf-8")
                changed = True
            if nodes_needed:
                _copy_verified(staging / "nodes", comfy_dir, list(_paths(manifest["nodes"])))
                changed = True
        emit(100, "H3 依赖已补齐，正在继续启动")
    return changed
