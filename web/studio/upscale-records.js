const KEY = 'minimax-upscale-records';
function persistRecords(records) {
  localStorage.setItem(KEY, JSON.stringify(records));
  window.__TAURI__?.core?.invoke('persist_workbench_state', {kind:'upscales',data:records}).catch(() => {});
}

export function upscaleRecords() {
  try {
    const records = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(records) ? records : [];
  } catch { return []; }
}

export function upscaleRecord(sourceId) {
  return upscaleRecords().find(item => item.sourceId === sourceId);
}

export function saveUpscaleRecord(record) {
  const records = upscaleRecords().filter(item => item.sourceId !== record.sourceId);
  records.unshift(record);
  persistRecords(records.slice(0, 200));
}

export function removeUpscaleRecord(sourceId) {
  persistRecords(upscaleRecords().filter(item => item.sourceId !== sourceId));
}
