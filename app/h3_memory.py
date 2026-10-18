"""工作台与克隆中心共用的显存预算；预算是经验保护线，不是显存保证。"""
from __future__ import annotations

import math
import os
import re
import subprocess

from .h3_workflow import RESOLUTION_PRESETS, duration_to_frame_length

LOW_VRAM_GPU_LIMIT_GB = 16.5
LOW_VRAM_CLONE_MAX_TEMPORAL_PIXELS = 45_000_000
LOW_VRAM_VERIFIED_DEPTH_PROFILES = {
    "0.4MP 16:9 - 864x480", "0.6MP 16:9 - 1056x608", "0.8MP 16:9 - 1216x672",
}
LOW_VRAM_VERIFIED_MAX_FRAMES = 158


def gpu_memory_total_gb() -> float | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        return float(result.stdout.splitlines()[0].strip()) / 1024
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def preset_aspect(name: str) -> str:
    match = re.search(r"\b\d+:\d+\b", name)
    return match.group() if match else "1:1" if name.startswith("Square") else name


def smaller_resolution(resolution: str) -> str | None:
    area = math.prod(RESOLUTION_PRESETS[resolution])
    candidates = [
        name for name, size in RESOLUTION_PRESETS.items()
        if preset_aspect(name) == preset_aspect(resolution) and math.prod(size) < area
    ]
    return max(candidates, key=lambda name: math.prod(RESOLUTION_PRESETS[name])) if candidates else None


def compatible_resolution(
    resolution: str, duration: float, gpu_memory_gb: float | None,
    *, verified_depth: bool = False,
) -> str:
    if gpu_memory_gb is not None and gpu_memory_gb > LOW_VRAM_GPU_LIMIT_GB:
        return resolution
    frames = duration_to_frame_length(duration)
    if (verified_depth and resolution in LOW_VRAM_VERIFIED_DEPTH_PROFILES
            and frames <= LOW_VRAM_VERIFIED_MAX_FRAMES
            and (gpu_memory_gb is None or gpu_memory_gb >= 15)):
        return resolution
    # 小卡更保守；未知硬件沿用 16GB 保护线，不能跳过保护。
    budget = LOW_VRAM_CLONE_MAX_TEMPORAL_PIXELS * min(1, (gpu_memory_gb or 16) / 16)
    if math.prod(RESOLUTION_PRESETS[resolution]) * frames <= budget:
        return resolution
    candidates = [
        name for name in RESOLUTION_PRESETS if preset_aspect(name) == preset_aspect(resolution)
        and math.prod(RESOLUTION_PRESETS[name]) <= math.prod(RESOLUTION_PRESETS[resolution])
    ]
    safe = [name for name in candidates if math.prod(RESOLUTION_PRESETS[name]) * frames <= budget]
    return (max(safe, key=lambda name: math.prod(RESOLUTION_PRESETS[name])) if safe else
            min(candidates, key=lambda name: math.prod(RESOLUTION_PRESETS[name])))
