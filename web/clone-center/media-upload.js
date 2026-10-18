// 三类创作页面共用真实上传字节事件；传输结束后仍等待服务端确认。
export function formatBytes(value) {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes < 1024) return bytes.toFixed(0) + ' B';
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 ** 2).toFixed(1) + ' MB';
}

export function uploadText(stats) {
  const size = formatBytes(stats.loaded) + ' / ' + (stats.total ? formatBytes(stats.total) : '计算中');
  if (stats.phase === 'processing') return size + ' · 上传完成，等待服务器确认';
  if (stats.phase === 'done') return size + ' · 上传完成';
  if (stats.phase === 'failed') return stats.error || '素材上传失败，请重试';
  return size + ' · ' + formatBytes(stats.speed) + '/s' + (stats.total ? ' · ' + Math.floor(stats.percent) + '%' : '');
}

export function uploadJson(url, {body, headers = {}, signal, onProgress = () => {}, timeout = 0} = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const started = performance.now();
    let lastEmission = -Infinity, settled = false;
    let stats = {phase:'uploading', loaded:0, total:0, percent:0, speed:0};
    const cleanup = () => signal?.removeEventListener('abort', abort);
    const fail = error => {
      if (settled) return;
      settled = true; cleanup();
      onProgress({...stats, phase:'failed', error:error.message});
      reject(error);
    };
    const abort = () => xhr.abort();
    const emit = (phase, event, force = false) => {
      const now = performance.now();
      if (event?.lengthComputable) stats.total = event.total;
      if (Number.isFinite(event?.loaded)) stats.loaded = event.loaded;
      if (phase === 'processing' && stats.total) stats.loaded = stats.total;
      stats = {...stats, phase, percent:stats.total ? Math.min(100, 100 * stats.loaded / stats.total) : 0,
        speed:phase === 'uploading' ? stats.loaded / Math.max(0.001, (now - started) / 1000) : 0};
      if (force || now - lastEmission >= 160) { lastEmission = now; onProgress({...stats}); }
    };
    xhr.open('POST', url);
    xhr.timeout = timeout;
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = event => emit('uploading', event);
    xhr.upload.onload = event => emit('processing', event, true);
    xhr.onerror = () => fail(new Error('无法连接素材上传服务，请检查网络后重试'));
    xhr.ontimeout = () => fail(new Error('素材上传超时，请重试'));
    xhr.onabort = () => fail(new DOMException('素材上传已取消', 'AbortError'));
    xhr.onload = () => {
      if (settled) return;
      let data = null;
      try { data = JSON.parse(xhr.responseText); }
      catch {
        if (xhr.status >= 200 && xhr.status < 300) {
          fail(new Error('服务器未返回有效上传结果，请检查服务地址后重试')); return;
        }
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const error = new Error(typeof data?.detail === 'string' ? data.detail : '素材上传失败（HTTP ' + xhr.status + '）');
        error.status = xhr.status; fail(error); return;
      }
      settled = true; cleanup(); emit('done', null, true); resolve(data);
    };
    if (signal?.aborted) { fail(new DOMException('素材上传已取消', 'AbortError')); return; }
    signal?.addEventListener('abort', abort, {once:true});
    emit('uploading', null, true);
    lastEmission = -Infinity;
    xhr.send(body);
  });
}

const guarded = new WeakSet();
function hasFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes('Files')
    || Array.from(event.dataTransfer?.items || []).some(item => item.kind === 'file');
}
export function guardFileNavigation(document = globalThis.document) {
  if (guarded.has(document)) return;
  guarded.add(document);
  const style = document.createElement('style');
  style.textContent = '.is-file-dragging{outline:2px dashed #83c8ab!important;outline-offset:-4px}';
  document.head.append(style);
  for (const name of ['dragover', 'drop']) document.addEventListener(name, event => {
    if (hasFiles(event)) event.preventDefault();
  });
}

export function bindFileDrop(element, receive) {
  if (!element) return;
  guardFileNavigation(element.ownerDocument);
  element.addEventListener('dragover', event => {
    if (!hasFiles(event)) return;
    event.preventDefault(); event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    element.classList.add('is-file-dragging');
  });
  element.addEventListener('dragleave', event => {
    if (!element.contains(event.relatedTarget)) element.classList.remove('is-file-dragging');
  });
  element.addEventListener('drop', event => {
    if (!hasFiles(event) && !event.dataTransfer?.files?.length) return;
    event.preventDefault(); event.stopPropagation(); element.classList.remove('is-file-dragging');
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length) receive(files, event);
  });
}

export function createUploadPanel(container) {
  const document = container.ownerDocument;
  if (!document.querySelector('style[data-media-upload]')) {
    const style = document.createElement('style'); style.dataset.mediaUpload = '';
    style.textContent = '.is-file-dragging{outline:2px dashed #83c8ab!important;outline-offset:-4px}'
      + '.media-upload-panel{position:absolute;inset:0;z-index:15;display:flex;align-items:center;justify-content:center;background:#101719b8;pointer-events:none;padding:20px;box-sizing:border-box}'
      + '.media-upload-panel[hidden]{display:none}.media-upload-card{width:min(460px,100%);padding:24px;border:1px solid #58776b;border-radius:14px;background:#162321f2;color:#f0f6f2;font:14px/1.7 system-ui;box-sizing:border-box}'
      + '.media-upload-card strong{display:block;font-size:20px}.media-upload-card p{overflow-wrap:anywhere;margin:8px 0;color:#d0ded6}.media-upload-card progress{width:100%;height:10px;accent-color:#83c8ab}';
    document.head.append(style);
  }
  const panel = document.createElement('section');
  panel.className = 'media-upload-panel'; panel.hidden = true;
  panel.setAttribute('aria-label','素材上传进度'); panel.setAttribute('aria-live','polite');
  panel.innerHTML = '<div class="media-upload-card"><strong></strong><p class="media-upload-name"></p><progress max="100"></progress><p class="media-upload-detail"></p></div>';
  container.append(panel);
  let items = new Map(), sequence = 0, timer;
  const active = () => [...items.values()].filter(item => ['uploading','processing'].includes(item.phase));
  const clear = () => {clearTimeout(timer);items.clear();panel.hidden=true;};
  return {
    clear,
    watch(name) {
      clearTimeout(timer);
      if (!active().length) items.clear();
      const id = ++sequence;
      items.set(id,{name,phase:'uploading',loaded:0,total:0,speed:0,percent:0});
      const update = value => {
        if (!items.has(id)) return;
        clearTimeout(timer);
        items.set(id,{...items.get(id),...value});
        const entries = [...items.values()], running = active();
        const failed = entries.find(item => item.phase === 'failed');
        const stats = {loaded:entries.reduce((n,item)=>n+item.loaded,0), total:entries.reduce((n,item)=>n+item.total,0),
          speed:running.reduce((n,item)=>n+item.speed,0), phase:running.some(item=>item.phase==='uploading') ? 'uploading' : running.length ? 'processing' : failed ? 'failed' : 'done',
          error:failed?.error};
        stats.percent = stats.total ? Math.min(100,100*stats.loaded/stats.total) : 0;
        if (running.some(item=>!item.total)) stats.total = 0;
        panel.hidden = false; panel.dataset.phase = stats.phase;
        panel.querySelector('strong').textContent = {uploading:'正在上传素材',processing:'等待素材确认',done:'素材上传完成',failed:'素材上传失败'}[stats.phase];
        panel.querySelector('.media-upload-name').textContent = entries.length === 1 ? name : entries.length + ' 个素材 · ' + name;
        panel.querySelector('.media-upload-detail').textContent = uploadText(stats);
        const bar = panel.querySelector('progress');
        if (stats.total) bar.value = stats.percent; else bar.removeAttribute('value');
        if (stats.phase === 'done') timer = setTimeout(clear, 1200);
      };
      update({});
      return update;
    },
  };
}
