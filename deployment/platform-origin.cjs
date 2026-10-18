const {execFileSync} = require('node:child_process');

function readPlatformUrls() {
  try {
    const code = 'import json; from node_monitor.agent import runtime_environment,access_urls; print(json.dumps(access_urls(runtime_environment())))';
    const output = execFileSync('/root/minimax-node/venv/bin/python', ['-c', code],
      {cwd:'/root/minimax-node', encoding:'utf8', timeout:5000, stdio:['ignore','pipe','ignore']});
    const urls = JSON.parse(output);
    return Array.isArray(urls) ? urls : [];
  } catch {
    return [];
  }
}

function publicHost(incoming, platformUrls) {
  if (!incoming) return incoming;
  let host;
  try {
    host = new URL(`http://${incoming}`);
    if (host.username || host.password || host.pathname !== '/' || host.search || host.hash) return incoming;
  } catch { return incoming; }
  for (const value of platformUrls) {
    try {
      const address = new URL(value);
      if (!['http:','https:'].includes(address.protocol) || address.username || address.password) continue;
      // AutoDL 可能去掉公网 Host 的端口；仅恢复平台确认为本实例的同名入口。
      if (host.hostname.toLowerCase() === address.hostname.toLowerCase() &&
          (!host.port || host.port === address.port)) return address.host;
    } catch { /* 忽略损坏的平台地址，保留原 Host。 */ }
  }
  return incoming;
}

module.exports = {readPlatformUrls, publicHost};
