"""本机临时端口选择与运行地址发现，不修改用户配置。"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import psutil

from .service_process import inspect_service


def endpoint_file(root: Path) -> Path:
    return Path(os.environ.get("MINIMAX_SERVICE_ENDPOINTS_FILE") or root / "outputs/runtime/service-endpoints.json")


def read_endpoints(root: Path) -> dict:
    try:
        value = json.loads(endpoint_file(root).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def owned_listener(root: Path, port: int, kind: str) -> bool:
    try:
        for connection in psutil.net_connections(kind="tcp"):
            if connection.status != psutil.CONN_LISTEN or connection.laddr.port != port or not connection.pid:
                continue
            configured_comfy = os.environ.get("MINIMAX_COMFY_DIR") if kind == "comfy" else None
            if not inspect_service(connection.pid, root, configured_comfy):
                continue
            args = psutil.Process(connection.pid).cmdline()
            if ("app.server" in args) == (kind == "web"):
                return True
    except (psutil.Error, OSError):
        pass
    return False


def bind_available(host: str, preferred: int) -> socket.socket:
    """持有真实监听 socket 直至交给服务器，避免检查与绑定之间的竞争。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, preferred))
        except OSError:
            if os.environ.get('MINIMAX_MANAGED_NODE') == '1':
                raise
            listener.bind((host, 0))
        listener.listen(128)
        listener.set_inheritable(True)
        return listener
    except BaseException:
        listener.close()
        raise


def select_comfy_port(root: Path, host: str, preferred: int) -> int:
    # 远端端口是用户明确指定的服务，不在本机分配。
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return preferred
    saved = read_endpoints(root).get("comfy_port")
    for port in dict.fromkeys([preferred, saved]):
        if isinstance(port, int) and 0 < port < 65536 and owned_listener(root, port, "comfy"):
            return port
    with bind_available("0.0.0.0", preferred) as listener:
        return listener.getsockname()[1]


def save_endpoints(root: Path, web_port: int, comfy_port: int) -> None:
    path = endpoint_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"pid": os.getpid(), "web_port": web_port, "comfy_port": comfy_port}), encoding="utf-8")
    temporary.replace(path)
