"""桌面与生成任务共用的超分设置，只依赖标准库。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {"engine": "realesrgan", "topaz_path": "", "topaz_model": "prob-4"}


def load_upscale_settings(root: Path = ROOT) -> dict:
    path = root / "config/upscale-settings.json"
    if not path.exists():
        return DEFAULTS.copy()
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_upscale_settings(data)


def validate_upscale_settings(data: dict) -> dict:
    from .topaz_video import TopazUpscaleSettings

    if not isinstance(data, dict):
        raise TypeError("超分设置必须是对象")
    result = {key: data.get(key, value) for key, value in DEFAULTS.items()}
    if result["engine"] not in {"realesrgan", "topaz"}:
        raise ValueError("不支持的超分引擎")
    if not isinstance(result["topaz_path"], str) or not isinstance(result["topaz_model"], str):
        raise TypeError("Topaz 路径和模型必须是文本")
    result["topaz_path"] = result["topaz_path"].strip().strip('"')
    TopazUpscaleSettings(model=result["topaz_model"]).validated()
    return result


def save_upscale_settings(data: dict, root: Path = ROOT) -> dict:
    result = validate_upscale_settings(data)
    if result["engine"] == "topaz":
        from .topaz_video import (
            TopazUpscaleSettings,
            available_topaz_models,
            resolve_topaz_installation,
        )

        installation = resolve_topaz_installation(result["topaz_path"])
        if not installation.ready:
            raise ValueError(installation.error)
        models = available_topaz_models(installation.model_dir)
        if not models:
            raise ValueError("当前安装中没有可用于自动超分的经典 Topaz 模型")
        TopazUpscaleSettings(model=result["topaz_model"]).validated(available_models=[m["id"] for m in models])
        result["topaz_path"] = str(installation.install_dir)
    path = root / "config/upscale-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="upscale-settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return result


def inspect_upscale_settings(configured_path: str | None = None, root: Path = ROOT) -> dict:
    from .topaz_video import (
        available_topaz_models,
        preferred_topaz_model,
        resolve_topaz_installation,
    )

    settings = load_upscale_settings(root)
    installation = resolve_topaz_installation(settings["topaz_path"] if configured_path is None else configured_path)
    models = available_topaz_models(installation.model_dir) if installation.ready else []
    starlight = False
    if installation.model_dir:
        for path in installation.model_dir.glob("slp*.json"):
            try:
                definition = json.loads(path.read_text(encoding="utf-8"))
                starlight |= "slp26" in json.dumps(definition).lower() or "2.6" in path.stem
            except (OSError, ValueError):
                continue
    return {"settings": settings, "topaz": installation.public(), "models": models,
            "preferred_model": preferred_topaz_model(models),
            "starlight": {"detected": starlight, "supported": False,
                          "message": ("已检测到星光 2.6 定义。" if starlight else "未检测到星光 2.6 定义。")
                          + "星光使用独立 NeuroServer 流程，当前适配仅支持经典模型；星光请在 Topaz 客户端中处理。"}}


def upscale_output_suffix(settings: dict) -> str:
    return f"-topaz-{settings['topaz_model']}-2x" if settings["engine"] == "topaz" else "-2x"
