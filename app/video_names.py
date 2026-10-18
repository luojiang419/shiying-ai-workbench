"""本地和云端共用的 LWai 视频序号协议。"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

_NAME = re.compile(r"^(?:(?:本地H3|优云H3)-)?(\d+)-LWai-\d{8}-\d{6}(?:(?:-topaz-[a-z0-9-]+)?-2x)?\.mp4$", re.IGNORECASE)


def _next_number(directory: Path) -> int:
    counter = directory / ".lwai-sequence"
    try:
        previous = int(counter.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        previous = 0
    existing = max((int(match.group(1)) for path in directory.iterdir()
                    if (match := _NAME.fullmatch(path.name))), default=0)
    number = max(previous, existing) + 1
    temporary = directory / f".lwai-sequence.{os.getpid()}.tmp"
    temporary.write_text(str(number), encoding="ascii")
    temporary.replace(counter)
    return number


def reserve_video_name(directory: Path, *, now: datetime | None = None) -> str:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".lwai-sequence.lock"
    deadline = time.monotonic() + 15
    while True:
        try:
            with lock.open("x", encoding="ascii") as stream:
                stream.write(str(os.getpid()))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("视频序号正在被其他任务占用")
            time.sleep(0.05)
    try:
        number = _next_number(directory)
    finally:
        lock.unlink(missing_ok=True)
    return f"本地H3-{number:03d}-LWai-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}.mp4"


def promote_generated_video(source: Path, directory: Path) -> Path:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".mp4":
        raise ValueError("H3 输出不是 MP4，不能按视频规范归档")
    target = Path(directory) / reserve_video_name(directory)
    if source.resolve() == target.resolve():
        return target
    source.replace(target)
    return target
