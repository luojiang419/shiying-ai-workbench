from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from .config import WEB_OUTPUT_DIR, WORK_LIBRARY_FILE
from .directory_watch import DirectoryWatch
from .media_access import media_source_lock

VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}
_VIDEO_INDEX_LOCK = Lock()
_VIDEO_INDEX: dict[str, tuple[tuple, float, tuple[tuple[Path, float], ...], DirectoryWatch]] = {}


def invalidate_video_index(output_dir: Path) -> None:
    with _VIDEO_INDEX_LOCK:
        _VIDEO_INDEX.pop(str(output_dir.resolve()), None)


def _indexed_video_files(output_dir: Path) -> tuple[tuple[Path, float], ...]:
    """目录变更通知失效缓存；时间戳与三十秒过期兜底。"""
    key = str(output_dir.resolve())
    with _VIDEO_INDEX_LOCK:
        try:
            stat = output_dir.stat()
            stamp = (stat.st_dev, stat.st_ino, stat.st_mtime_ns)
        except OSError:
            _VIDEO_INDEX.pop(key, None)
            return ()
        cached = _VIDEO_INDEX.get(key)
        now = time.monotonic()
        changed = cached[3].changed() if cached else False
        if cached and not changed and cached[0] == stamp and now - cached[1] < 30:
            return cached[2]
        watch = cached[3] if cached else DirectoryWatch(output_dir)
        files = []
        try:
            with os.scandir(output_dir) as candidates:
                for entry in candidates:
                    if Path(entry.name).suffix.lower() not in VIDEO_SUFFIXES:
                        continue
                    try:
                        if entry.is_file():
                            files.append((Path(entry.path), entry.stat().st_mtime))
                    except OSError:
                        continue
        except OSError:
            _VIDEO_INDEX.pop(key, None)
            return ()
        files.sort(key=lambda entry: (entry[1], entry[0].name.casefold()), reverse=True)
        snapshot = tuple(files)
        if len(_VIDEO_INDEX) >= 8:
            _VIDEO_INDEX.pop(next(iter(_VIDEO_INDEX)))
        _VIDEO_INDEX[key] = (stamp, now, snapshot, watch)
        return snapshot


@dataclass
class WorkItem:
    id: str
    output: str
    filename: str
    prompt: str
    resolution: str
    duration: float
    steps: int
    seed: int | None
    mode: str
    first_frame: str | None
    last_frame: str | None
    reference_images: list[str]
    reference_videos: list[str]
    reference_audios: list[str]
    created_at: float
    completed_at: float
    elapsed_seconds: float
    user_brief: str = ""
    prompt_plan: dict | None = None


class WorkLibrary:
    def __init__(self) -> None:
        self._lock = Lock()
        WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        WORK_LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)

    def add(self, item: WorkItem) -> None:
        with self._lock:
            items = self._read()
            items.insert(0, asdict(item))
            self._write(items[:200])
        invalidate_video_index(WEB_OUTPUT_DIR)

    def list(self) -> list[dict]:
        with self._lock:
            return self._read()

    def list_videos(self, offset: int = 0, limit: int = 100, order: str = "desc", *, allowed_outputs: set[str] | None = None, query: str = "", extra_items: list[dict] | None = None, include_videos: bool = True) -> dict:
        with self._lock:
            works = self._read()
        return paginate_video_files(WEB_OUTPUT_DIR, works, offset=offset, limit=limit, order=order, allowed_outputs=allowed_outputs, query=query, extra_items=extra_items, include_videos=include_videos)

    def count_videos(self) -> int:
        return count_video_files(WEB_OUTPUT_DIR)

    def delete(self, work_id: str, fallback: dict | None = None) -> bool:
        with self._lock:
            items = self._read()
            removed = next((item for item in items if item.get("id") == work_id), None) or fallback
        if not removed:
            return False
        output_name = Path(str(removed.get("filename", ""))).name
        if output_name:
            output_file = (WEB_OUTPUT_DIR / output_name).resolve()
            # 等待本文件转码时不持有作品库锁，其他列表和写入仍可继续。
            with media_source_lock(output_file):
                if _is_inside(output_file, WEB_OUTPUT_DIR.resolve()) and output_file.exists():
                    output_file.unlink()
        with self._lock:
            self._write([item for item in self._read() if item.get("id") != work_id])
        invalidate_video_index(WEB_OUTPUT_DIR)
        return True

    def _read(self) -> list[dict]:
        if not WORK_LIBRARY_FILE.exists():
            return []
        try:
            data = json.loads(WORK_LIBRARY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = WORK_LIBRARY_FILE.with_name(f"works-corrupt-{int(time.time())}.json")
            WORK_LIBRARY_FILE.replace(backup)
            return []
        return data if isinstance(data, list) else []

    def _write(self, items: list[dict]) -> None:
        tmp = WORK_LIBRARY_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(WORK_LIBRARY_FILE)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def count_video_files(output_dir: Path) -> int:
    return len(_indexed_video_files(output_dir))


def paginate_video_files(
    output_dir: Path,
    works: list[dict],
    offset: int = 0,
    limit: int = 100,
    order: str = "desc",
    *,
    allowed_outputs: set[str] | None = None,
    query: str = "",
    extra_items: list[dict] | None = None,
    include_videos: bool = True,
) -> dict:
    if offset < 0:
        raise ValueError("视频分页偏移不能小于 0")
    if limit < 1 or limit > 200:
        raise ValueError("作品分页数量必须在 1 到 200 之间")
    if order not in {"asc", "desc"}:
        raise ValueError("视频排序只支持 asc 或 desc")

    metadata_by_filename = {
        Path(str(item.get("filename", ""))).name: item
        for item in works
        if Path(str(item.get("filename", ""))).name
    }
    files: list[tuple[Path, float]] = []
    for path, modified_at in (_indexed_video_files(output_dir) if include_videos else ()):
        if (allowed_outputs is not None
                and f"/outputs/{quote(path.name)}" not in allowed_outputs
                and f"/outputs/{path.name}" not in allowed_outputs
                and (not query or query.casefold() not in path.name.casefold())):
            continue
        files.append((path, modified_at))
    candidates = [(modified_at, path.name.casefold(), path) for path, modified_at in files]
    needle = query.casefold()
    for item in extra_items or []:
        if needle and not any(needle in str(item.get(key, "")).casefold() for key in ("filename", "prompt", "id", "job_id")):
            continue
        candidates.append((item["created_at"], item["filename"].casefold(), item))
    candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=order == "desc")
    total = len(candidates)
    page = candidates[offset:offset + limit]
    items = []
    for modified_at, _, path in page:
        if isinstance(path, dict):
            items.append(path)
            continue
        metadata = metadata_by_filename.get(path.name, {})
        completed_at = metadata.get("completed_at")
        generated_at = float(completed_at) if isinstance(completed_at, (int, float)) else modified_at
        item = dict(metadata)
        item.update(
            {
                "id": metadata.get("id") or f"file:{path.name}",
                "filename": path.name,
                "output": f"/outputs/{quote(path.name)}",
                "created_at": generated_at,
                "thumbnail_version": str(modified_at),
                "has_metadata": bool(metadata),
                "media_type": "video",
            }
        )
        items.append(item)

    next_offset = offset + len(items)
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
        "has_more": next_offset < total,
        "order": order,
    }
