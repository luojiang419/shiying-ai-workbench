export const NODE_STORAGE_KEY = 'minimax-compute-node-v1';
export const NODE_MODE_KEY = 'minimax-compute-node-enabled-v1';
export const DEFAULT_NODE_PORT = 7890;

export function normalizeNodeAddress(raw) {
  const value = String(raw || '').trim().replace(/\/$/, '');
  if (!value) throw new Error('请输入算力节点 IP 或地址');
  if (/\s|[?#@\\]/.test(value)) throw new Error('节点地址不能包含空格、账号、查询参数或片段');
  const input = /^https?:\/\//i.test(value) ? value : `http://${value}`;
  let url;
  try { url = new URL(input); } catch { throw new Error('节点地址格式无效'); }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.pathname !== '/') {
    throw new Error('只需填写 IP、域名及可选端口');
  }
  if (!url.port) url.port = String(DEFAULT_NODE_PORT);
  if (!/^\d+$/.test(url.port) || Number(url.port) < 1 || Number(url.port) > 65535) {
    throw new Error('节点端口须为 1–65535');
  }
  return url.origin;
}

export function savedNodeAddress(storage = localStorage) {
  try { return normalizeNodeAddress(storage.getItem(NODE_STORAGE_KEY)); }
  catch { return ''; }
}

export async function probeNode(address, fetcher = fetch) {
  const base = normalizeNodeAddress(address);
  const response = await fetcher(`${base}/api/node-info`, {signal:AbortSignal.timeout(7000),cache:'no-store'});
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    if (data?.error === 'network_not_allowed')
      throw new Error('节点入口拒绝当前网络访问（HTTP 403，network_not_allowed），请检查 AutoDL 应用访问入口或使用可访问的节点地址');
    throw new Error(`节点响应异常 (${response.status})`);
  }
  const info = await response.json();
  if (info.api !== 'minimax-compute-node' || info.version !== 1) throw new Error('目标服务不是兼容的 MiniMax 算力节点');
  return {base,info};
}
