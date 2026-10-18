"""兼容旧 Qwen 端口，只绑定原端口，不抢占或改用其他端口。"""
from __future__ import annotations

import os
import socket

from .qwen_keys import LEGACY_CONTROL_PORT, LEGACY_PUBLIC_PORT


def bind_legacy_listeners(keys):
    listeners = []
    keys.listeners = []
    if not keys.setting('legacy_ports_enabled', False):
        return listeners
    for host, port in [('127.0.0.1', LEGACY_CONTROL_PORT), ('0.0.0.0', LEGACY_PUBLIC_PORT)]:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == 'nt':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((host, port))
            listener.listen(128)
            listener.set_inheritable(True)
            listeners.append(listener)
            keys.listeners.append({'host': host, 'port': port, 'listening': True})
        except OSError:
            listener.close()
            keys.listeners.append({'host': host, 'port': port, 'listening': False,
                                   'error': f'{port} 端口已被占用或无法监听；请关闭原 Qwen 服务后重启当前服务'})
    return listeners
