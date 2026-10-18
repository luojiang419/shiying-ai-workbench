from __future__ import annotations

import os
import time
from pathlib import Path


class InterProcessLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._file = None

    def __enter__(self) -> InterProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(self.path, "a+b")
        # Windows 允许锁定 EOF 以外的字节。不要在取得锁前初始化空文件：
        # 另一进程可能已锁住第 0 字节，写入会先于重试循环抛出 PermissionError。

        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                _lock_file(lock_file)
                self._file = lock_file
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    lock_file.close()
                    raise TimeoutError(f"等待进程锁超时: {self.path}")
                time.sleep(0.1)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._file is None:
            return
        try:
            _unlock_file(self._file)
        finally:
            self._file.close()
            self._file = None


if os.name == "nt":
    import msvcrt

    def _lock_file(lock_file) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(lock_file) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(lock_file) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(lock_file) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
