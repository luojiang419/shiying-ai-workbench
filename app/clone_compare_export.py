"""Export a synchronized side-by-side clone comparison as an MP4."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def _video_info(ffprobe: Path, video: Path) -> tuple[int, int, float]:
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,duration:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"无法读取视频：{video.name}")
    data = json.loads(result.stdout)
    stream = (data.get("streams") or [{}])[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
    if width <= 0 or height <= 0 or duration <= 0:
        raise RuntimeError(f"视频尺寸或时长无效：{video.name}")
    return width, height, duration


def _font_path() -> Path:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("未找到可用于中文标签的字体")


def _labels_filter(panel_height: int) -> str:
    # A filter graph has two escaping layers; drive letters need both backslashes.
    font = str(_font_path()).replace("\\", "/").replace(":", r"\\:")
    size = max(18, min(36, round(panel_height * .045)))
    common = f"fontfile={font}:fontcolor=white:fontsize={size}:box=1:boxcolor=black@0.7:boxborderw=8:y=20"
    return (
        "drawbox=x=iw/2-1:y=0:w=2:h=ih:color=white@0.75:t=fill,"
        f"drawtext={common}:text=原视频:x=w/2-text_w-20,"
        f"drawtext={common}:text=克隆视频:x=w/2+20"
    )


def export_comparison(source: Path, generated: Path, destination: Path,
                      ffmpeg: Path, ffprobe: Path) -> Path:
    if not source.is_file() or not generated.is_file():
        raise FileNotFoundError("原参考视频或克隆视频文件不存在")
    if destination.suffix.lower() != ".mp4" or not destination.parent.is_dir():
        raise ValueError("请选择有效的 MP4 保存路径")
    width, height, duration = _video_info(ffprobe, generated)
    panel_width = min(width, 960)
    panel_width -= panel_width % 2
    panel_height = min(round(height * panel_width / width), 1080)
    panel_height -= panel_height % 2
    if panel_width < 2 or panel_height < 2:
        raise RuntimeError("克隆视频尺寸过小")
    temporary = destination.with_name(f".{destination.stem}-{uuid4().hex}.tmp.mp4")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    panels = (
        f"fps=24,scale={panel_width}:{panel_height}:force_original_aspect_ratio=decrease,"
        f"pad={panel_width}:{panel_height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    graph = (
        f"[0:v]{panels},tpad=stop_mode=clone:stop_duration=120,"
        f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[left];"
        f"[1:v]{panels},trim=duration={duration:.3f},setpts=PTS-STARTPTS[right];"
        f"[left][right]hstack=inputs=2,{_labels_filter(panel_height)},format=yuv420p[out]"
    )
    command = [str(ffmpeg), "-v", "error", "-y", "-i", str(source), "-i", str(generated),
               "-filter_complex", graph, "-map", "[out]", "-map", "1:a?", "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-b:a", "160k", "-movflags", "+faststart", "-t", f"{duration:.3f}", str(temporary)]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=1800,
                                creationflags=flags)
        if result.returncode or not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(result.stderr.strip()[-1000:] or "对比视频合成失败")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 6:
        raise SystemExit("usage: clone_compare_export.py SOURCE GENERATED DESTINATION FFMPEG FFPROBE")
    export_comparison(*(Path(argument) for argument in sys.argv[1:]))
