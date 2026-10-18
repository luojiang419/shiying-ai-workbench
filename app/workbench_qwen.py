"""一次性工作台 Qwen 服务：按需补齐模型，退出时释放进程资源。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .clone_aux_runtime import cleanup_llama_archives, prepare_auxiliary

ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def serve() -> None:
    prepare_auxiliary(ROOT, qwen=True)
    model = ROOT / "models/clone/qwen/Qwen3-VL-4B-Q4_K_M.gguf"
    projector = ROOT / "models/clone/qwen/mmproj.gguf"
    executable = ROOT / "vendor/clone-llama/llama-server.exe"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = [str(executable), "-m", str(model), "--mmproj", str(projector),
               "--host", "127.0.0.1", "--port", str(port), "-c", "24576",
               "-ngl", "99", "--parallel", "1", "--threads", "8", "--no-warmup"]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=FLAGS)
    try:
        for _ in range(240):
            if process.poll() is not None:
                raise RuntimeError("Qwen 推理进程启动失败")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(1)
        else:
            raise TimeoutError("Qwen 模型加载超时")
        cleanup_llama_archives(ROOT)
        print(f"READY {port}", flush=True)
        sys.stdin.buffer.read()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    try:
        serve()
    except Exception as error:
        print(f"ERROR {error}", flush=True)
        raise
