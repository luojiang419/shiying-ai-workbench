"""在 AutoDL 镜像安装独立的上报守护进程与官方开机钩子。"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

BEGIN = '# BEGIN MINIMAX NODE REPORTER'
END = '# END MINIMAX NODE REPORTER'


def boot_hook(original: str, launcher: Path) -> str:
    block = f'{BEGIN}\nbash {shlex.quote(str(launcher))} || echo "MiniMax node reporter startup failed" >&2\n{END}\n'
    cleaned = re.sub(re.escape(BEGIN) + r'.*?' + re.escape(END) + r'\n?', '', original, flags=re.DOTALL)
    if not cleaned.strip():
        cleaned = '#!/usr/bin/env bash\n'
    exit_line = re.search(r'(?m)^exit\s+0\s*$', cleaned)
    if exit_line:
        return cleaned[:exit_line.start()] + block + cleaned[exit_line.start():]
    return cleaned.rstrip() + '\n\n' + block


def install(root: Path, config: Path, hook: Path, supervisord: Path, supervisorctl: Path, start=True):
    if os.name == 'nt':
        raise RuntimeError('此安装入口用于 Linux AutoDL 镜像')
    root = root.resolve()
    config = config.resolve()
    if not config.is_file() or not (root / 'node_monitor/agent.py').is_file():
        raise ValueError('缺少上报配置或 agent.py')
    config.chmod(0o600)
    deployment = root / 'deployment'
    runtime = root / 'outputs/runtime'
    logs = root / 'logs'
    for directory in [deployment, runtime, logs, root / 'scripts']:
        directory.mkdir(parents=True, exist_ok=True)
    conf = deployment / 'node-reporter-supervisord.ini'
    program = 'minimax-node-reporter'
    command = f'{shlex.quote(sys.executable)} -m node_monitor.agent --config {shlex.quote(str(config))}'
    conf.write_text(f'''[unix_http_server]
file={runtime}/node-reporter-supervisor.sock
chmod=0700
[supervisord]
logfile={logs}/node-reporter-supervisor.log
pidfile={runtime}/node-reporter-supervisor.pid
childlogdir={logs}
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{runtime}/node-reporter-supervisor.sock
[program:{program}]
command={command}
directory={root}
autostart=true
autorestart=unexpected
exitcodes=0
startsecs=1
stopasgroup=true
killasgroup=true
stopwaitsecs=15
stdout_logfile={logs}/node-reporter.log
stdout_logfile_maxbytes=2MB
stdout_logfile_backups=2
redirect_stderr=true
environment=PYTHONUNBUFFERED="1"
''', encoding='utf-8')
    conf.chmod(0o600)
    launcher = root / 'scripts/start_node_reporter.sh'
    launcher.write_text(f'''#!/usr/bin/env bash
set -euo pipefail
CTL={shlex.quote(str(supervisorctl))}
CONF={shlex.quote(str(conf))}
if ! "$CTL" -c "$CONF" pid >/dev/null 2>&1; then
  {shlex.quote(str(supervisord))} -c "$CONF"
fi
state=$("$CTL" -c "$CONF" status {program} 2>/dev/null || true)
case "$state" in
  *RUNNING*|*STARTING*) ;;
  *) "$CTL" -c "$CONF" start {program} ;;
esac
''', encoding='utf-8')
    launcher.chmod(0o755)
    original = hook.read_text(encoding='utf-8') if hook.exists() else ''
    backup = hook.with_name(hook.name + '.before-minimax-node-reporter')
    if hook.exists() and not backup.exists():
        shutil.copy2(hook, backup)
    hook.write_text(boot_hook(original, launcher), encoding='utf-8')
    hook.chmod(0o755)
    if start:
        subprocess.run(['bash', str(launcher)], check=True)
    return {'supervisor_config': str(conf), 'launcher': str(launcher), 'boot_hook': str(hook)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--boot-hook', type=Path, default=Path('/etc/autodl.sh'))
    parser.add_argument('--supervisord', type=Path, default=Path(shutil.which('supervisord') or '/root/miniconda3/bin/supervisord'))
    parser.add_argument('--supervisorctl', type=Path, default=Path(shutil.which('supervisorctl') or '/root/miniconda3/bin/supervisorctl'))
    args = parser.parse_args()
    import json
    print(json.dumps(install(args.root, args.config, args.boot_hook, args.supervisord, args.supervisorctl)))


if __name__ == '__main__':
    main()
