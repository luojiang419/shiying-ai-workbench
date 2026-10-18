"""轻量目录变更信号；Windows 不依赖可能合并的目录时间戳。"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class DirectoryWatch:
    def __init__(self, directory: Path) -> None:
        self.handle = None
        if os.name != "nt":
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        kernel.FindFirstChangeNotificationW.restype = wintypes.HANDLE
        kernel.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
        kernel.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel = kernel
        handle = kernel.FindFirstChangeNotificationW(str(directory), False, 0x19)
        if handle and handle != ctypes.c_void_p(-1).value:
            self.handle = handle

    def changed(self) -> bool:
        if self.handle and self.kernel.WaitForSingleObject(self.handle, 0) == 0:
            self.kernel.FindNextChangeNotification(self.handle)
            return True
        return False

    def close(self) -> None:
        if self.handle:
            self.kernel.FindCloseChangeNotification(self.handle)
            self.handle = None

    def __del__(self) -> None:
        self.close()
