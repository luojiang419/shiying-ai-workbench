import {cloudTaskVisible, currentCloudScope} from './cloud-scope.js';
import {cloudErrorText} from './cloud-error-zh.js';
export const CLOUD_SPEC = Object.freeze({ resolution: '480P', ratio: '16:9', duration: 5 });
export const CLOUD_RESOLUTIONS = ['480P', '768P', '1080P', '2K', '4K'];
export const CLOUD_RATIOS = ['21:9', '16:9', '4:3', '1:1', '3:4', '9:16'];
export const CLOUD_MEDIA_LIMITS = Object.freeze({image:30*1024*1024,video:50*1024*1024,audio:15*1024*1024,request:72*1024*1024});
const RAW_REQUEST_BUDGET = Math.floor((CLOUD_MEDIA_LIMITS.request-128*1024)*3/4);
const TASKS_KEY = globalThis.MinimaxBrowser ? 'minimax-browser-cloud-h3-tasks' : 'minimax-cloud-h3-tasks';
let diskWrites = Promise.resolve();
let fallbackTasks = null;
export function flushCloudTasks() { return diskWrites; }
function persistTasks(rows, task) {
  fallbackTasks = rows;
  try {localStorage.setItem(TASKS_KEY, JSON.stringify(rows));fallbackTasks=null;} catch {}
  if (!window.__TAURI__ || window.MinimaxBrowser) return;
  diskWrites = diskWrites.catch(() => {}).then(() => task
    ? window.__TAURI__.core.invoke('upsert_workbench_task', {kind:'cloud-tasks',task})
    : window.__TAURI__.core.invoke('persist_workbench_state', {kind:'cloud-tasks',data:rows}));
  diskWrites.catch(error => window.dispatchEvent(new CustomEvent('task-persistence-error',{detail:cloudErrorText(error)})));
}

export function cloudOptions() {
  return CLOUD_RESOLUTIONS.flatMap(resolution => CLOUD_RATIOS.map(ratio => ({
    value: `${resolution}|${ratio}`, label: `${resolution} · ${ratio}`
  })));
}

export function mapLocalResolution(value) {
  const ratio = String(value || '').match(/(21:9|16:9|4:3|1:1|3:4|9:16)/)?.[1] || CLOUD_SPEC.ratio;
  return `${CLOUD_SPEC.resolution}|${ratio}`;
}

export { cloudSourcePrompt, lightweightSourcePrompt } from './clone-center/cloud-source-prompt.js';

export function cloudSpec(value) {
  const [resolution, ratio, extra] = String(value).split('|');
  if (extra || !CLOUD_RESOLUTIONS.includes(resolution) || !CLOUD_RATIOS.includes(ratio)) throw new Error('请选择有效的云端分辨率和画幅');
  return { resolution, ratio };
}

export function fileDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`无法读取素材：${file.name}`));
    reader.readAsDataURL(file);
  });
}

async function cloudVideoDataUrl(file, maxBytes = CLOUD_MEDIA_LIMITS.video) {
  if (file.size > 384 * 1024 * 1024) throw new Error(`${file.name} 超过工作台可处理的 384 MiB 转码上限`);
  const dataUrl = await fileDataUrl(file);
  if (file.size <= maxBytes) return dataUrl;
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) throw new Error('优云参考视频转码需要桌面工作台');
  const compact = await invoke('cloud_h3_transcode_video', {dataUrl,maxBytes});
  if (!compact.size || compact.size > maxBytes) throw new Error(`${file.name} 转码后仍超过本次请求的容量预算`);
  return compact.data_url;
}

async function cloudImage(file, maxBytes) {
  if (file.size<=maxBytes) return {url:await fileDataUrl(file),size:file.size};
  const bitmap = await createImageBitmap(file);
  try {
    let scale=Math.min(1,1080/Math.min(bitmap.width,bitmap.height),8192/Math.max(bitmap.width,bitmap.height));
    const canvas=document.createElement('canvas');
    while (scale>0.02) {
      canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
      const context=canvas.getContext('2d');context.clearRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0,canvas.width,canvas.height);
      const encode=(type,quality)=>new Promise(resolve=>canvas.toBlob(resolve,type,quality));
      if (file.type==='image/png') {
        const lossless=await encode('image/png');
        if (lossless && lossless.size<=maxBytes) return {url:await fileDataUrl(lossless),size:lossless.size};
        scale*=0.8;
        continue;
      }
      for (const quality of [0.96,0.93,0.9,0.85,0.8]) {
        const blob=await encode('image/jpeg',quality);
        if (blob && blob.size<=maxBytes) return {url:await fileDataUrl(blob),size:blob.size};
      }
      scale*=0.8;
    }
    throw new Error(`${file.name} 无法压缩到本次请求的容量预算，请减少参考素材`);
  } finally {bitmap.close();}
}

export async function buildCloudPayload({prompt, resolution, duration, mode, references = [], frames = {}}) {
  if (!prompt.trim()) throw new Error('请输入提示词');
  if (Array.from(prompt).length > 7000) throw new Error('提示词超过 7000 字符');
  const spec = cloudSpec(resolution);
  const seconds = Number(duration);
  if (!Number.isInteger(seconds) || seconds < 4 || seconds > 30) throw new Error('云端时长须为 4–30 秒整数');
  const content = [{ type: 'text', text: prompt.trim() }];
  if (mode === 'references') {
    const counts = { image: 0, video: 0, audio: 0 };
    const desired=references.reduce((sum,item)=>sum+Math.min(item.file.size,CLOUD_MEDIA_LIMITS[item.audio?'audio':item.video?'video':'image']),0);
    const ratio=Math.min(1,RAW_REQUEST_BUDGET/Math.max(1,desired));
    const prepared=new Map();
    let fixedBytes=0;
    for (const item of references.filter(item=>!item.video)) {
      if (item.audio && item.file.size>CLOUD_MEDIA_LIMITS.audio) throw new Error(`${item.file.name} 超过云端 15 MiB 限制`);
      const value=item.audio ? {url:await fileDataUrl(item.file),size:item.file.size}
        : await cloudImage(item.file,Math.min(CLOUD_MEDIA_LIMITS.image,Math.max(512*1024,Math.floor(item.file.size*ratio))));
      prepared.set(item,value.url);fixedBytes+=value.size;
    }
    const videos=references.filter(item=>item.video);
    const desiredVideos=videos.reduce((sum,item)=>sum+Math.min(item.file.size,CLOUD_MEDIA_LIMITS.video),0);
    const available=Math.max(0,RAW_REQUEST_BUDGET-fixedBytes);
    if (videos.length && available<videos.length*256*1024) throw new Error('其他参考素材占用过多容量，请减少图片或音频');
    const videoRatio=Math.min(1,available/Math.max(1,desiredVideos));
    for (const item of references) {
      const kind = item.audio ? 'audio' : item.video ? 'video' : 'image';
      const field = `${kind}_url`;
      const role = kind === 'image' ? 'reference_image' : `reference_${kind}`;
      counts[kind]++;
      const budget=Math.max(256*1024,Math.floor(Math.min(item.file.size,CLOUD_MEDIA_LIMITS.video)*videoRatio));
      content.push({ type: field, [field]: { url: kind === 'video' ? await cloudVideoDataUrl(item.file,budget) : prepared.get(item) }, role });
    }
    if (counts.image > 9 || counts.video > 3 || counts.audio > 3 || Object.values(counts).reduce((a,b) => a+b, 0) > 12) throw new Error('参考素材超过云端数量限制');
    if (counts.audio && !counts.image && !counts.video) throw new Error('参考音频需要同时提供参考图片或视频');
  } else {
    for (const role of ['first_frame', 'last_frame']) {
      const item = frames[role];
      if (!item) continue;
      const image=await cloudImage(item.file,CLOUD_MEDIA_LIMITS.image);
      content.push({type:'image_url', image_url:{url:image.url}, role});
    }
  }
  if (new TextEncoder().encode(JSON.stringify(content)).length > CLOUD_MEDIA_LIMITS.request-1024) throw new Error('素材请求体超过云端 72 MiB 限制，请减少参考素材');
  return {model:'MiniMax-H3',content,...spec,duration:seconds,use_context_ir:false,aigc_watermark:false};
}

export function allCloudTasks() {
  if(fallbackTasks)return fallbackTasks;
  try { const rows = JSON.parse(localStorage.getItem(TASKS_KEY) || '[]'); return Array.isArray(rows) ? rows : []; }
  catch { return []; }
}

export function savedCloudTasks(allEngines = false) {
  const scope=currentCloudScope();
  const engine = localStorage.getItem('minimax-active-generation-engine');
  return allCloudTasks().filter(row => {
    if (row.historyHidden) return false;
    if (!cloudTaskVisible(row,scope)) return false;
    if (!allEngines && engine === 'platform_h3') return row.provider === 'platform';
    if (!allEngines && engine === 'compshare_h3') return row.provider !== 'platform';
    return true;
  }).map(row=>({...row,error:row.error?cloudErrorText(row.error,{code:row.error_code}):row.error,message:row.message?cloudErrorText(row.message,{code:row.error_code}):row.message}));
}

export function mergeCloudTasks(disk, local) {
  const rows = new Map();
  for (const task of [...(Array.isArray(disk) ? disk : []), ...(Array.isArray(local) ? local : [])]) {
    if (!task?.id) continue;
    const previous = rows.get(task.id);
    if (!previous) { rows.set(task.id, task); continue; }
    const terminal = value => ['done','failed','error','canceled','cancelled'].includes(value.status);
    const newest = (terminal(previous) && !terminal(task)) || Number(previous.updated_at || 0)>Number(task.updated_at || 0) ? previous : task;
    rows.set(task.id, {
      ...previous, ...task, ...newest,
      clone: Boolean(previous.clone || task.clone),
      historyHidden: Boolean(previous.historyHidden || task.historyHidden),
      cloneRequest: task.cloneRequest || previous.cloneRequest,
      filename: task.filename || previous.filename,
      localPath: task.localPath || previous.localPath,
      output: task.output || previous.output,
      created_at: task.created_at || previous.created_at,
    });
  }
  return [...rows.values()];
}

export function saveCloudTask(task) {
  const current = allCloudTasks();
  const previous = current.find(row => row.id === task.id);
  const rows = current.filter(row => row.id !== task.id);
  rows.unshift(previous ? mergeCloudTasks([previous], [task])[0] : task);
  persistTasks(rows, rows[0]);
}

export function removeCloudTask(id) {
  persistTasks(allCloudTasks().filter(row => row.id !== id));
}

export function cloudTaskNeedsRefresh(task) {
  if (task.cloudStatus === 'needs_review') return true;
  return !['failed', 'error', 'canceled', 'cancelled'].includes(task.status) &&
    (task.status !== 'done' || !task.output || !task.localPath);
}

export function normalizeCloudTask(result, previous) {
  const task = result.task || result;
  // 云端实际还会返回 moderating 等中间阶段，界面统一按生成中展示并持续轮询。
  const rawStatus = String(task.status || '').toLowerCase();
  const status = ({succeeded:'done',done:'done',failed:'failed',blocked:'failed',rejected:'failed',terminated:'failed',error:'failed',aborted:'failed',timed_out:'failed',needs_review:'error',cancelled:'canceled',canceled:'canceled',queued:'queued'})[rawStatus]
    || (task.status ? 'running' : previous.status);
  const output = task.content?.url || previous.output || '';
  const error = typeof task.error === 'string' ? task.error : task.error?.message || task.error?.reason || task.error?.detail || '';
  const failureRaw = error || (['failed','error'].includes(status) ? (rawStatus === 'blocked' ? '未通过平台内容审查' : '云端任务已终止') : '');
  const failure = failureRaw ? cloudErrorText(failureRaw,{code:task.error?.code}) : '';
  const terminal = ['done','failed','error','canceled'].includes(status);
  const time = value => typeof value === 'number' ? value : value ? Date.parse(value)/1000 : 0;
  const ended = terminal ? time(task.updated_at) || previous.completed_at || Date.now()/1000 : previous.completed_at;
  const started = time(task.created_at) || previous.created_at;
  const estimate=Number.isInteger(task.estimated_remaining_seconds) && task.estimated_remaining_seconds>=0 ? task.estimated_remaining_seconds : null;
  const estimateAt=estimate!==null ? time(task.eta_updated_at) || Date.now()/1000 : null;
  return {...previous, status, output, estimated_remaining_seconds:terminal?null:estimate,eta_updated_at:terminal?null:estimateAt, output_available:!!output, error:failure, error_raw:error || previous.error_raw, error_code:task.error?.code || previous.error_code, updated_at:Date.now()/1000,
    running_at:status === 'running' ? (previous.running_at || Date.now() / 1000) : previous.running_at,
    cloudStatus:task.status || previous.cloudStatus,
    message:failure || (status==='done' ? (previous.localPath?'已自动保存到本机':previous.archiveError || '') : '') || (estimate!==null && estimate>0 && !terminal ? `平台预计剩余 ${estimate} 秒` : ''),
    resolution:task.resolution || previous.resolution,ratio:task.ratio || previous.ratio,
    duration:task.duration || previous.duration,
    created_at:started,
    completed_at:ended,
    elapsed_seconds:terminal && ended && started ? Math.max(0,ended-started) : previous.elapsed_seconds};
}
