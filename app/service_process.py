"""只读核验系统 Python 启动的本项目服务，不执行停止操作。"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import psutil


def _normalized(path):
    return os.path.normcase(os.path.realpath(path))


def _entrypoint(arguments):
    """解析解释器入口，避免把 -c 脚本的参数误当成 -m 服务。"""
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-m":
            return ("module", arguments[index + 1]) if index + 1 < len(arguments) else None
        if argument.startswith("-c") or argument in {"-", "--"}:
            return None
        if argument in {"-W", "-X"}:
            index += 2
        elif argument.startswith("-"):
            index += 1
        else:
            return "script", argument
    return None


def matches_service(executable, arguments, cwd, root, comfy_dir=None):
    if not re.fullmatch(r"pythonw?(?:\d+(?:\.\d+)*)?(?:\.exe)?", Path(executable).name, re.IGNORECASE):
        return False
    entrypoint = _entrypoint(arguments)
    if not entrypoint:
        return False
    root = Path(root).resolve()
    kind, target = entrypoint
    if kind == "module":
        return target == "app.server" and _normalized(cwd) == _normalized(root) and (root / "app/server.py").is_file()
    script = Path(target)
    if not script.is_absolute():
        script = Path(cwd) / script
    comfy_main = (Path(comfy_dir).resolve() if comfy_dir is not None else root / "vendor/ComfyUI") / "main.py"
    return comfy_main.is_file() and _normalized(script) == _normalized(comfy_main)


def inspect_service(pid, root, comfy_dir=None):
    try:
        process = psutil.Process(pid)
        with process.oneshot():
            return matches_service(process.exe(), process.cmdline(), process.cwd(), root, comfy_dir)
    except (psutil.Error, OSError, ValueError):
        # 无法读取或进程已退出时不认领，不能按端口推测归属。
        return False


if __name__ == "__main__":
    print(json.dumps({"pid": int(sys.argv[1]), "matches": inspect_service(int(sys.argv[1]), sys.argv[2])}))
