"""协调同一视频的缩略图读取与删除，空闲后回收锁。"""
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

_guard = Lock()
_sources = {}


@contextmanager
def media_source_lock(source: Path):
    key = str(source.resolve()).casefold()
    with _guard:
        entry = _sources.setdefault(key, [Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _guard:
            entry[1] -= 1
            if not entry[1]:
                del _sources[key]
