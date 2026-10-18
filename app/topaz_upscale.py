"""使用用户本机 Topaz 独立进程超分；失败清理临时输出并保留原片。"""
from __future__ import annotations

import subprocess
from collections import deque
from dataclasses import replace
from pathlib import Path

from .topaz_video import (
    TopazUpscaleSettings,
    available_topaz_models,
    build_topaz_upscale_command,
    humanize_topaz_ffmpeg_error,
    parse_ffmpeg_progress,
    probe_video,
    resolve_target_dimensions,
    resolve_topaz_installation,
    topaz_child_environment,
)


def run_topaz_upscale(source: Path, target: Path, settings: dict, emit=lambda *_args, **_kwargs: None) -> dict:
    if source.resolve() == target.resolve():
        raise ValueError("Topaz 输出路径不能覆盖输入视频")
    installation = resolve_topaz_installation(settings["topaz_path"])
    if not installation.ready:
        raise RuntimeError(installation.error)
    models = available_topaz_models(installation.model_dir)
    if not models:
        raise RuntimeError("没有可自动调用的经典 Topaz 模型")
    options = TopazUpscaleSettings(model=settings["topaz_model"]).validated(available_models=[m["id"] for m in models])
    info = probe_video(installation, source)
    width, height = resolve_target_dimensions(info["width"], info["height"], "2x")
    options = replace(options, output_width=width, output_height=height)
    staging = target.parent / ".topaz-tmp"
    staging.mkdir(parents=True, exist_ok=True)
    temporary = staging / f"{target.stem}.partial.mp4"
    temporary.unlink(missing_ok=True)
    command = build_topaz_upscale_command(installation, source, temporary, options, available_models=[m["id"] for m in models])
    process = None
    tail = deque(maxlen=80)
    try:
        emit(0, f"Topaz {options.model} · 正在加载模型，缺失权重由 Topaz 自动下载")
        process = subprocess.Popen(command, cwd=installation.install_dir, env=topaz_child_environment(installation),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        last_percent = -1
        for line in process.stdout:
            tail.append(line)
            if line.startswith("progress="):
                progress = parse_ffmpeg_progress(tail, info["duration"])
                percent = min(98, round(progress["progress"] * 98))
                if percent != last_percent:
                    emit(percent, f"Topaz {options.model} · {progress['frame']} 帧 · {progress['speed']}")
                    last_percent = percent
        if process.wait() != 0:
            detail = humanize_topaz_ffmpeg_error(tail, output_width=width, output_height=height)
            raise RuntimeError(f"Topaz 处理失败：{detail}")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("Topaz 未生成可用视频")
        output_info = probe_video(installation, temporary)
        if (output_info["width"], output_info["height"]) != (width, height):
            raise RuntimeError("Topaz 输出尺寸与 2× 超分不符")
        if info["duration"] > 0 and abs(output_info["duration"] - info["duration"]) > max(0.25, info["duration"] * 0.02):
            raise RuntimeError("Topaz 输出时长与原片不符，已丢弃不完整视频")
        temporary.replace(target)
        emit(100, f"Topaz {options.model} · 2× 超分完成")
        return {"engine": "topaz", "model": options.model, "width": width, "height": height}
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout:
                process.stdout.close()
        temporary.unlink(missing_ok=True)
