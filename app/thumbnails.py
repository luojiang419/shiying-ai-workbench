"""按源文件指纹缓存预览图；限制 CPU 解码并发，不占用生成 GPU。"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from threading import BoundedSemaphore

from .config import OUTPUT_DIR
from .media_access import media_source_lock

_slots = BoundedSemaphore(2)


def video_thumbnail(source: Path, cache_dir: Path | None = None) -> Path:
    with media_source_lock(source):
        return _video_thumbnail(source, cache_dir)


def _video_thumbnail(source: Path, cache_dir: Path | None = None) -> Path:
    stat = source.stat()
    key = hashlib.sha256(f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    cache = cache_dir or OUTPUT_DIR / "thumbnails"
    target = cache / f"{key}.jpg"
    if target.is_file():
        return target
    # 同一文件的请求在获取槽位后再次检查，避免重复转码。
    if not _slots.acquire(timeout=10):
        raise RuntimeError("预览生成繁忙，请稍后重试")
    try:
        if target.is_file():
            return target
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            bundled = Path(__file__).resolve().parents[1] / "runtime/ffmpeg/ffmpeg.exe"
            ffmpeg = str(bundled) if bundled.is_file() else None
        if not ffmpeg:
            raise RuntimeError("未找到 FFmpeg")
        cache.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-threads", "1", "-i", str(source),
             "-frames:v", "1", "-vf", "scale=384:384:force_original_aspect_ratio=decrease",
             "-threads", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=10, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not result.stdout:
            raise RuntimeError("视频没有可用画面")
        # 并发同源请求也通过独立临时文件原子发布。
        from uuid import uuid4
        temporary = cache / f"{key}-{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(result.stdout)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("视频预览暂不可用") from exc
    finally:
        _slots.release()
