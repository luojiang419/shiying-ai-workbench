"""Qwen 图片模型按需校验和补齐；失败不影响 H3 视频能力。"""
from __future__ import annotations

import hashlib
import os
import json
import re
import shutil
from pathlib import Path

from .clone_depth_runtime import download_verified, matches
from .process_lock import InterProcessLock


def load_models(root: Path) -> dict:
    manifest = json.loads((root / "scripts/qwen21-models.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise ValueError("Qwen 模型清单版本不受支持")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", manifest.get("version", "")):
        raise ValueError("Qwen 模型清单版本非法")
    for relative, spec in manifest["models"].items():
        p = relative.replace("\\", "/")
        if p.startswith("/") or ":" in p or ".." in p.split("/") or not p.startswith(("diffusion_models/", "text_encoders/", "vae/")):
            raise ValueError("Qwen 模型清单包含非法路径")
        assets = spec.get("parts") or [spec]
        if spec.get("parts") and sum(p["size"] for p in assets) != spec["size"]:
            raise ValueError("Qwen 模型分卷大小不完整")
        for asset in assets:
            if not asset.get("urls") or not all(u.startswith("https://modelscope.cn/models/") for u in asset["urls"]):
                raise ValueError("Qwen 模型必须从已验证的魔塔地址补齐")
    return manifest


def prepare_qwen_models(root: Path, comfy_dir: Path, emit=lambda *_: None) -> bool:
    root, comfy_dir = root.resolve(), comfy_dir.resolve()
    if os.name != 'nt' and os.environ.get('MINIMAX_MANAGED_NODE') == '1':
        from .linux_native import prepare_managed_qwen
        return prepare_managed_qwen(root, emit)
    # 用户指定的自定义/远程后端由其所有者管理，不能向另一个运行时写模型。
    if comfy_dir != root / "vendor/ComfyUI":
        return False
    manifest = load_models(root)
    changed = False
    with InterProcessLock(root / "outputs/runtime/qwen-models.lock", 6 * 3600):
        license_source = root / "third_party/qwen-image21"
        if license_source.is_dir():
            shutil.copytree(license_source, comfy_dir / "models/qwen21-license", dirs_exist_ok=True)
        for relative, spec in manifest["models"].items():
            target = comfy_dir / "models" / relative
            if matches(target, spec, emit=emit):
                continue
            emit(0, "正在自动补齐图片模型：" + target.name)
            if spec.get("parts"):
                cache = root / "outputs/runtime/qwen-model-parts" / manifest["version"] / target.name
                parts = [download_verified(p["urls"], cache / f"{index:03d}.part", p, emit)
                         for index, p in enumerate(spec["parts"])]
                temporary = target.with_name(target.name + ".assembling")
                target.parent.mkdir(parents=True, exist_ok=True)
                emit(0, "正在合并并校验图片模型分卷")
                digest = hashlib.sha256()
                with temporary.open("wb") as output:
                    for part in parts:
                        with part.open("rb") as source:
                            while block := source.read(8 * 1024 * 1024):
                                output.write(block)
                                digest.update(block)
                if temporary.stat().st_size != spec["size"] or digest.hexdigest() != spec["sha256"]:
                    raise ValueError("Qwen 模型分卷合并校验失败，保留原模型")
                temporary.replace(target)
                # 仅删除本函数生成且已验证合并成功的缓存分卷。
                for part in parts:
                    part.unlink()
            else:
                download_verified(spec["urls"], target, spec, emit)
            changed = True
    return changed
