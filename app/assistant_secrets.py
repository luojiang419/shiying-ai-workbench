"""服务端助手密钥：共享配置仅存密文，节点主密钥独立存放。"""
from __future__ import annotations

import os
from pathlib import Path

PREFIX = 'node:v1:'


def _master_key(root: Path, *, create=False) -> bytes:
    path = root / 'config/assistant-node.key'
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            from cryptography.fernet import Fernet
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(Fernet.generate_key())
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise ValueError('节点助手主密钥文件权限须为 0600')
    return path.read_bytes()


def seal_node_key(value: str, root: Path) -> str:
    from cryptography.fernet import Fernet
    return PREFIX + Fernet(_master_key(root, create=True)).encrypt(value.encode()).decode()


def open_node_key(value: str, root: Path) -> str:
    if not value.startswith(PREFIX):
        raise ValueError('节点助手密文格式无效')
    try:
        from cryptography.fernet import Fernet
        return Fernet(_master_key(root)).decrypt(value[len(PREFIX):].encode()).decode()
    except Exception as error:
        # 不把密文、主密钥或底层异常请求内容带入公开错误信息。
        raise RuntimeError('节点 AI 助手密钥无法解密，请检查私有主密钥及配置') from error
