"""镜像内的轻量上报进程，仅使用 Python 标准库。"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import os
import signal
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

LOGGER = logging.getLogger('minimax-node-reporter')
PLATFORM_KEYS = {'HOSTNAME', 'AutoDLContainerUUID', 'AutoDLRegion', 'AutoDLDataCenter',
                 'AutoDLService6006URL', 'AutoDLService6008URL', 'AutoDLServiceURL',
                 'MINIMAX_NODE_PUBLIC_URL'}


def runtime_environment(environ=None, proc: Path = Path('/proc')) -> dict:
    """优先读取本次容器启动时的平台变量，避免 SSH 环境缺失或镜像残留地址。"""
    selected = {key: value for key, value in (os.environ if environ is None else environ).items() if key in PLATFORM_KEYS}
    candidates = [proc / '1']
    try:
        for path in proc.iterdir():
            if not path.name.isdigit() or path.name == '1':
                continue
            try:
                command = (path / 'cmdline').read_bytes().split(b'\0')
            except (OSError, PermissionError):
                continue
            if b'/init/supervisor/supervisor.ini' in command:
                candidates.append(path)
    except OSError:
        pass
    for path in candidates:
        try:
            for part in (path / 'environ').read_bytes().split(b'\0'):
                key, separator, value = part.partition(b'=')
                name = key.decode(errors='replace')
                if separator and name in PLATFORM_KEYS:
                    selected[name] = value.decode(errors='replace')
        except OSError:
            continue
    return selected


def origin(value: str) -> str | None:
    try:
        if any(ord(character) <= 32 or ord(character) == 127 for character in value):
            return None
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            return None
        _ = parsed.port
        return urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
    except ValueError:
        return None


def access_urls(environment: dict, service_slot=6008) -> list[str]:
    # 平台本次启动地址优先于人为设置，避免镜像里残留的手动地址遮盖新域名。
    values = [environment.get(f'AutoDLService{service_slot}URL', '')]
    if service_slot == 6006:
        values.append(environment.get('AutoDLServiceURL', ''))
    for value in values:
        if found := origin(value):
            return [found]
    manual = origin(environment.get('MINIMAX_NODE_PUBLIC_URL', ''))
    return [manual] if manual else []


def node_identity(environment: dict, family: str, hostname: str) -> tuple[str, str]:
    # machine-id 会随镜像复制，不能用作实例标识。
    instance = environment.get('AutoDLContainerUUID') or environment.get('HOSTNAME') or hostname
    digest = hashlib.sha256((family + '\0' + instance).encode()).hexdigest()[:24]
    return digest, instance


def resolve_ips(urls: list[str]) -> list[str]:
    addresses = []
    for value in urls:
        parsed = urlsplit(value)
        try:
            records = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
            for record in records:
                ip = ipaddress.ip_address(record[4][0])
                if ip.is_global:
                    addresses.append(str(ip))
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(addresses))[:8]


def local_ips(hostname: str) -> list[str]:
    try:
        addresses = [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(hostname, None)]
        return list(dict.fromkeys(str(ip) for ip in addresses if not ip.is_loopback and not ip.is_link_local))[:12]
    except (OSError, ValueError):
        return []


def read_status(url: str) -> tuple[bool, str]:
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'MiniMax-Node-Reporter/1'})
        with urllib.request.urlopen(request, timeout=4) as response:
            data = json.loads(response.read(1024 * 1024))
        return data.get('ready') is True, str(data.get('backend', {}).get('message') or '服务已连接')[:300]
    except (OSError, ValueError, TypeError, AttributeError):
        return False, '工作台服务暂未就绪'


class Reporter:
    def __init__(self, config: dict):
        self.config = config
        self.root = Path(config.get('root', Path(__file__).resolve().parents[1]))
        self.boot_id = uuid.uuid4().hex
        self.sequence = 0
        self.started = time.monotonic()
        self.dns_at = 0.0
        self.dns_urls = []
        self.dns_ips = []
        self.context = ssl.create_default_context(cafile=config.get('ca_file'))
        target = urlsplit(config['monitor_url'])
        if target.username or target.password or target.query or target.fragment:
            raise ValueError('监控接口地址不得含凭据或查询参数')
        if target.scheme != 'https' and not (config.get('allow_local_http') and target.hostname in {'127.0.0.1', 'localhost'}):
            raise ValueError('节点上报必须使用 HTTPS')
        if len(config.get('agent_key', '')) < 32:
            raise ValueError('节点上报密钥无效')

    def collect(self) -> dict:
        environment = runtime_environment()
        hostname = socket.gethostname()
        node_id, instance = node_identity(environment, self.config.get('family', 'minimax-h3'), hostname)
        urls = access_urls(environment, self.config.get('service_slot', 6008))
        if urls != self.dns_urls or time.monotonic() - self.dns_at > 60:
            self.dns_urls = urls
            self.dns_ips = resolve_ips(urls)
            self.dns_at = time.monotonic()
        ready, message = read_status(self.config.get('status_url', 'http://127.0.0.1:7890/api/status'))
        try:
            version = json.loads((self.root / 'web/studio/version.json').read_text(encoding='utf-8'))['version']
        except (OSError, ValueError, KeyError, TypeError):
            version = ''
        self.sequence += 1
        return {'node_id': node_id, 'boot_id': self.boot_id, 'sequence': self.sequence,
                'name': str(self.config.get('name', 'MiniMax H3'))[:80], 'instance_id': instance[:150],
                'hostname': hostname[:200], 'region': environment.get('AutoDLRegion', '')[:100],
                'access_urls': urls, 'access_ips': self.dns_ips, 'local_ips': local_ips(hostname),
                'public_port': self.config.get('public_port', 8443), 'public_scheme': self.config.get('public_scheme', 'https'),
                'api_ready': ready, 'service_message': message, 'app_version': str(version)[:40],
                'uptime_seconds': int(time.monotonic() - self.started)}

    def send(self, report):
        request = urllib.request.Request(self.config['monitor_url'], data=json.dumps(report).encode(),
            headers={'Authorization': 'Bearer ' + self.config['agent_key'], 'Content-Type': 'application/json',
                     'User-Agent': 'MiniMax-Node-Reporter/1'}, method='POST')
        with urllib.request.urlopen(request, context=self.context, timeout=10) as response:
            result = json.loads(response.read(65536))
        if result.get('accepted') is not True:
            raise RuntimeError('上报序列未被接收')
        return result

    def run(self, stop: threading.Event, once=False):
        interval = max(5, min(60, int(self.config.get('interval', 15))))
        failures = 0
        while not stop.is_set():
            try:
                report = self.collect()
                self.send(report)
                if failures or self.sequence == 1 or self.sequence % 20 == 0:
                    LOGGER.info('节点上报成功 node=%s ready=%s', report['node_id'], report['api_ready'])
                failures = 0
                delay = interval
            except (OSError, ValueError, TypeError, RuntimeError) as error:
                failures += 1
                # 异常对象可能带请求参数；只记录类型，密钥不进入日志。
                LOGGER.warning('节点上报失败 type=%s attempt=%s', type(error).__name__, failures)
                delay = min(60, 5 * 2 ** min(failures - 1, 4))
                if once:
                    return 1
            if once:
                return 0
            stop.wait(delay)
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = json.loads(args.config.read_text(encoding='utf-8'))
    state = Path(config.get('state_dir', args.config.parent / 'node-reporter-state'))
    state.mkdir(parents=True, exist_ok=True)
    lock = (state / 'reporter.lock').open('a+')
    if os.name != 'nt':
        import fcntl
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOGGER.info('节点上报进程已运行')
            return 0
    stop = threading.Event()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, lambda *_: stop.set())
    try:
        return Reporter(config).run(stop, args.once)
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
