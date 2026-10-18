#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/minimax-node
mkdir -p "$ROOT/logs" "$ROOT/outputs/runtime"
exec 8>"$ROOT/outputs/runtime/start.lock"
flock -w 45 8 || exit 1
CTL=/root/miniconda3/bin/supervisorctl
CONF="$ROOT/deployment/supervisord.ini"
if ! "$CTL" -c "$CONF" pid >/dev/null 2>&1; then
  # supervisord 自身会进入后台；不要再异步启动，避免外层开机脚本抢先占端口。
  /root/miniconda3/bin/supervisord -c "$CONF" >>"$ROOT/logs/supervisor-launch.log" 2>&1 8>&-
fi
# 修复进程独立持锁，保留既有 GPU/依赖适配流程。
nohup /root/miniconda3/bin/python "$ROOT/deployment/repair-node.py" </dev/null >>"$ROOT/logs/repair.log" 2>&1 8>&- &

GATEWAY_CONF="$ROOT/deployment/gateway-supervisord.ini"
if ! "$CTL" -c "$GATEWAY_CONF" pid >/dev/null 2>&1; then
  /root/miniconda3/bin/supervisord -c "$GATEWAY_CONF" 8>&-
elif ! "$CTL" -c "$GATEWAY_CONF" status minimax-gateway | grep -q RUNNING; then
  "$CTL" -c "$GATEWAY_CONF" start minimax-gateway
fi
# 外层 improved-autostart.sh 还会检查 6008；返回前等网关就绪，避免双入口竞争。
for attempt in {1..30}; do
  if curl --fail --silent --max-time 2 http://127.0.0.1:6008/api/node-info >/dev/null; then
    exit 0
  fi
  sleep 1
done
echo 'MiniMax gateway did not become ready on port 6008' >&2
exit 1
