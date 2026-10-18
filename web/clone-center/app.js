import {cloudErrorText} from '../cloud-error-zh.js';
import { lightweightSourcePrompt } from './cloud-source-prompt.js';
import { bindResolutionSelectors } from '../resolution-selectors.js';
import { requestGenerationService } from '../generation-service.js';
import '../generation-eta.js';
import { ReferenceMenu, referenceEntries, assetManifest, normalizeUserReferences, validateReferences, remapReferences } from './references.js';
import { saveNativeMaterial, readNativeMaterial, isNativeMaterial } from './native-materials.js';

// HTTP 远程访问不提供 randomUUID；iframe 不继承主页面的兼容实现。
// 同时供素材标识、云端消息及 generation-service 的服务检查使用。
if (!crypto.randomUUID) crypto.randomUUID = () => '10000000-1000-4000-8000-100000000000'.replace(/[018]/g,
  value => (Number(value) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(value)/4).toString(16));

const $ = (id) => document.getElementById(id);
const resolutionControls = bindResolutionSelectors($('resolution'), $('resolutionAspect'), $('resolutionSize'));
const cloneMobileViewButtons = document.querySelectorAll("[data-clone-mobile-target]");
const isEmbedded = window.self !== window.top;
document.documentElement?.classList.toggle("is-embedded", isEmbedded);

function isCloneMobileLayout() {
  return window.matchMedia?.("(max-width: 700px)").matches ?? false;
}

function setCloneMobileView(view, scroll = true) {
  if (!new Set(["flow", "workspace", "queue"]).has(view)) return;
  document.body.dataset.cloneMobileView = view;
  $('assetsToggle').setAttribute('aria-expanded', String(view === 'flow'));
  cloneMobileViewButtons.forEach((button) => {
    const active = button.dataset.cloneMobileTarget === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (scroll && isCloneMobileLayout()) window.scrollTo?.({ top: 0, behavior: "smooth" });
}

cloneMobileViewButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const view = button.dataset.cloneMobileTarget;
    setCloneMobileView(view);
  });
});

const video = $("video");
const depthVideo = $("depthVideo");
const resultVideo = $("resultVideo");
function reportResultPlaybackAvailable(available) {
  window.parent.postMessage({ type: "clone-result-playback-available", available }, "*");
}
function toggleResultPlayback() {
  if (resultVideo.hidden || !state.previewJobId || !resultVideo.getAttribute("src")) return;
  if (resultVideo.paused) resultVideo.play().catch(() => {});
  else resultVideo.pause();
}
let API = /^https?:$/.test(location.protocol) && !location.hostname.endsWith('tauri.localhost')
  ? location.origin
  : "http://127.0.0.1:7860";
if (isEmbedded) {
  try {
    const endpoint = new URL(new URLSearchParams(location.search).get('apiBase'));
    if (['http:', 'https:'].includes(endpoint.protocol) && !endpoint.username && !endpoint.password && endpoint.pathname === '/') API = endpoint.origin;
  } catch {}
}
let generationEngine = 'local';
let useWorkbenchServiceCheck = false;
let referenceModeExplicit = false;
const isCloudEngine = () => generationEngine === 'compshare_h3' || generationEngine === 'platform_h3';
let cloudReferenceType = 'source';
let cloudJobs = [];
let materialCloudProtocol = false;
let backgroundSubmissionProtocol = false;
let nativeMaterials = false;
const materialPromptPending = new Map();
let upscaleStates = new Map();
const cloudPending = new Map();
const upscalePending = new Map();
const compareExportPending = new Map();
function syncEngineControls() {
  const cloud = isCloudEngine();
  const usesDepth = ['extracted_depth','depth_video'].includes($('referenceMode')?.value);
  $('cloudDepthOptions').hidden = !cloud || !usesDepth;
  $('cloudSceneOpen').hidden = !cloud || !usesDepth;
  $('controlStrength').disabled = cloud || !usesDepth;
  if ($('referenceMode').value === 'region_video') $('controlStrength').disabled = true;
  const toggle = $('parametersToggle');
  toggle.disabled = false;
  toggle.title = '';
  $('generationParameters').classList.toggle('cloud-parameters', cloud);
  document.querySelectorAll('.cloud-generation-field').forEach(item => item.hidden = !cloud);
  document.querySelectorAll('.local-generation-field').forEach(item => item.hidden = cloud);
}
window.addEventListener('message', event => {
  if (event.source !== window.parent) return;
  const data = event.data || {};
  if(data.type==='cloud-h3-account-changed'){
    cloudJobs=[];state.selectedJobId=null;state.generationSelectionExplicit=true;
    processing.hidden=true;processing.classList.remove('cloud-active','is-failed');processing.dataset.taskId='';
    progressEtaJob=null;globalThis.GenerationEta.render($('generationEta'),null);
    sourceView();resultVideo.removeAttribute('src');resultVideo.load();renderQueue();
    return;
  }
  if (data.type === 'cloud-h3-state') {
    useWorkbenchServiceCheck = data.serviceCheck === true;
    materialCloudProtocol = data.materialInput === 'v1';
    backgroundSubmissionProtocol = data.backgroundSubmission === 'v1';
    nativeMaterials = data.nativeMaterials === 'v1' && isEmbedded;
    if (data.assistantRevision !== undefined && state.assistantRevision !== data.assistantRevision) {
      state.assistantRevision = data.assistantRevision;
      state.compiledMaterialDraft = null;
    }
    if (typeof data.apiBase === 'string' && /^https?:\/\//.test(data.apiBase)) API = data.apiBase;
    generationEngine = data.engine;
    cloudReferenceType = data.referenceType === 'depth' ? 'depth' : 'source';
    // 引擎状态不改变当前创作目标，避免异步消息把人物替换切成原片参考。
    cloudJobs = data.jobs || [];
    upscaleStates = new Map((data.upscales || []).map(record => [record.sourceId, record]));
    $('steps').disabled = generationEngine !== 'local';
    $('seed').disabled = generationEngine !== 'local';
    syncEngineControls();
    renderQueue(); update();
    const selected = cloudJobs.find(job => job.id === state.selectedJobId);
    if (selected && processing.classList.contains('cloud-active')) {
      if (selected.status === 'done' && selected.result?.video) showResultInPreview(selected);
      else if (['queued', 'running'].includes(selected.status)) showRunningJob(selected);
      else if (['failed','error'].includes(selected.status)) showFailedJob(selected);
      else { processing.hidden = true; processing.classList.remove('cloud-active'); }
    }
  }
  if (data.type === 'cloud-h3-balance') {
    const badge = $('cloudPoints');
    badge.hidden = !data.visible;
    if (typeof data.label === 'string') badge.textContent = data.label;
  }
  if (data.type === 'clone-submission-complete' && state.selectedJobId === data.pendingId) {
    state.selectedJobId = data.taskId;
    poll().catch(error => toast(error.message));
  }
  if (data.type === 'clone-cloud-submit-result') {
    const pending = cloudPending.get(data.nonce);
    if (!pending) return;
    cloudPending.delete(data.nonce);
    data.error ? pending.reject(new Error(data.error)) : pending.resolve({id:data.taskId});
  }
  if (data.type === 'clone-material-prompt-result') {
    const pending = materialPromptPending.get(data.nonce);
    if (!pending) return;
    materialPromptPending.delete(data.nonce);
    data.error ? pending.reject(new Error(data.error)) : pending.resolve(data.result);
  }
  if (data.type === 'clone-upscale-result') {
    const pending = upscalePending.get(data.nonce);
    if (!pending) return;
    upscalePending.delete(data.nonce);
    data.error ? pending.reject(new Error(data.error)) : pending.resolve();
  }
  if (data.type === 'clone-export-compare-result') {
    const pending = compareExportPending.get(data.nonce);
    if (!pending) return;
    compareExportPending.delete(data.nonce);
    data.error ? pending.reject(new Error(data.error)) : pending.resolve(data.result);
  }
});
function submitCloudClone(request) {
  if (request.prepare_prompt && !backgroundSubmissionProtocol)
    return Promise.reject(new Error('请更新工作台以使用后台提示词队列；任务尚未提交'));
  return new Promise((resolve,reject) => {
    const nonce = crypto.randomUUID();
    cloudPending.set(nonce,{resolve,reject});
    // 旧安装版桥接只读取 people.images；以单个无分类素材列表兼容，界面和新协议均不使用人物槽位。
    const bridgeRequest = request.input_mode === 'materials' && !materialCloudProtocol
      ? {...request,people:[{images:request.reference_images}]} : request;
    window.parent.postMessage({type:'clone-cloud-submit',nonce,request:bridgeRequest},'*');
  });
}
function requestCloneUpscale(job) {
  if (window.parent === window) return Promise.reject(new Error('请在工作台中生成超分视频'));
  return new Promise((resolve, reject) => {
    const nonce = crypto.randomUUID();
    upscalePending.set(nonce, {resolve, reject});
    window.parent.postMessage({type:'clone-upscale',nonce,taskId:job.id,cloud:Boolean(job.cloud)}, '*');
  });
}
function requestCloneCompareExport(job, variant = 'upscaled') {
  if (window.parent === window) return Promise.reject(new Error('请在桌面工作台中导出对比视频'));
  return new Promise((resolve, reject) => {
    const nonce = crypto.randomUUID();
    compareExportPending.set(nonce, {resolve, reject});
    window.parent.postMessage({type:'clone-export-compare', nonce, taskId:job.id, cloud:Boolean(job.cloud), variant}, '*');
  });
}
const api = async (path, options = {}) => {
  if (nativeMaterials && isCloudEngine()) {
    if (path.startsWith('/tasks?')) return {items:[],paused:false};
    const id = path.match(/^\/assets\/([^/]+)$/)?.[1];
    if (id && isNativeMaterial(id)) {
      const asset = await readNativeMaterial(id);
      return {...asset,url:URL.createObjectURL(asset.file)};
    }
    const clip = path.match(/^\/assets\/([^/]+)\/clip\?start=([^&]+)&end=([^&]+)$/);
    if (clip && isNativeMaterial(clip[1])) return new Promise((resolve,reject) => {
      const nonce = crypto.randomUUID();
      materialPromptPending.set(nonce,{resolve:result=>resolve({url:URL.createObjectURL(result.file)}),reject});
      window.parent.postMessage({type:'clone-material-clip',nonce,request:{source_id:clip[1],start:Number(clip[2]),end:Number(clip[3])}},'*');
    });
    if (path === '/compile-prompt') return new Promise((resolve,reject) => {
      const nonce = crypto.randomUUID();
      materialPromptPending.set(nonce,{resolve,reject});
      window.parent.postMessage({type:'clone-material-prompt',nonce,request:JSON.parse(options.body)},'*');
    });
  }
  const r = await fetch(API + "/api/clone" + path, {
    cache: "no-store",
    ...options,
  });
  const data = await r.json();
  if (!r.ok) throw Error(cloudErrorText(data.detail || "服务请求失败"));
  return data;
};
const post = (path, data) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
async function upload(file, kind) {
  if (nativeMaterials && isCloudEngine()) return saveNativeMaterial(file, kind);
  const endpoint = API;
  const body = new FormData();
  body.append("file", file);
  body.append("kind", kind);
  let response;
  try {
    response = await fetch(endpoint + '/api/clone/assets', {method:'POST', body, cache:'no-store'});
  } catch {
    throw new Error(`无法连接素材上传服务（${endpoint}），请检查后端服务与网络连接后重试。`);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : '';
    throw new Error(detail || `素材上传失败（HTTP ${response.status}，${endpoint}），请检查服务连接后重试。`);
  }
  if (!data?.id) throw new Error(`素材上传服务未返回素材编号（${endpoint}），请检查服务地址与版本。`);
  return data;
}

// 处理状态叠加在视频画面中央，不占用底部编辑空间。
const processing = document.querySelector(".processing");
$("dropzone").append(processing);
processing.hidden = true;
const state = {
  materialWorkflow: true,
  materials: [],
  compiledMaterialDraft: null,
  compilingMaterialPrompt: false,
  sourceId: null,
  sourceFile: null,
  sourceUploadError: '',
  sourceUploading: false,
  backgroundImage: null,
  depthId: null,
  regionId: null,
  regions: [],
  regionBox: null,
  regionStats: null,
  regionDraftDirty: false,
  regionEditingIndex: null,
  depthModel: "small",
  depthModels: [],
  analysisId: null,
  pending: null,
  sourceToken: 0,
  inputEpoch: 0,
  restoreRange: null,
  editingJobId: null,
  sourceDimensions: null,
  desiredResolution: null,
  name: "",
  url: "",
  duration: 0,
  start: 0,
  end: 0,
  mode: null,
  people: [],
  depth: false,
  analyzed: false,
  busy: false,
  epoch: 0,
  clearedProcessingEpoch: -1,
  jobs: [],
  selectedJobId: null,
  generationSelectionExplicit: false,
  paused: false,
  sample: false,
  nextId: 1,
  pollFailures: 0,
};
const positionNames = ["左侧", "中间", "右侧"];
const positionEn = { 左侧: "left", 中间: "center", 右侧: "right" };
function clearCreationInput() {
  const urls = [state.url, state.backgroundImage?.url, ...state.materials.map(item => item.url),
    ...state.people.flatMap(person => person.images.map(item => item.url))];
  state.clearedProcessingEpoch = state.epoch;
  state.epoch++;
  state.sourceToken++;
  state.inputEpoch++;
  Object.assign(state, {sourceId:null, sourceFile:null, sourceUploadError:'', sourceUploading:false, depthId:null, analysisId:null, pending:null, busy:false,
    backgroundImage:null, materials:[], compiledMaterialDraft:null, compilingMaterialPrompt:false,
    sourceDimensions:null, restoreRange:null, editingJobId:null, previewJobId:null,
    name:'', url:'', duration:0, start:0, end:0, mode:null, people:[], depth:false, analyzed:false,
    sample:false, selectedJobId:null, generationSelectionExplicit:true});
  invalidateRegion(true);
  for (const id of ['video', 'uploadPreview', 'depthVideo', 'resultVideo', 'regionPreview', 'depthAdjustPreview']) {
    const media = $(id);
    media.pause(); media.removeAttribute('src'); media.load();
  }
  for (const url of new Set(urls)) if (url?.startsWith('blob:')) URL.revokeObjectURL(url);
  for (const input of document.querySelectorAll('input[type="file"]')) input.value = '';
  for (const id of ['supplement', 'sceneText', 'lightweightPromptText']) $(id).value = '';
  $('supplementTarget').value = 'all';
  $('materialOptimizerState').textContent = '';
  $('sourceMeta').textContent = '等待素材';
  $('aspectHint').textContent = '导入视频后自动匹配 H3 画幅';
  $('uploadPlaceholder').hidden = false;
  $('uploadPreview').hidden = true;
  $('uploadReplace').hidden = true;
  $('uploadVideo').classList.remove('has-video');
  $('resultTab').hidden = true;
  $('videoEmpty').hidden = false;
  $('videoLabel').hidden = true;
  $('playtime').textContent = `${fmt(0)} / ${fmt(0)}`;
  $('seek').value = 0;
  for (const id of ['play', 'seek', 'inTime', 'outTime']) $(id).disabled = true;
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  document.querySelectorAll('.mention-menu').forEach(menu => { menu.hidden = true; });
  persistCloudDraft(); renderCloudBackground();
  sourceView(); renderPeople(); update();
  processing.hidden = true;
  $('supplement').focus();
}
$('clearInput').onclick = clearCreationInput;
let activeSlot = 0,
  visiblePerson = 0,
  toastTimer,
  processTimer;
const fmt = (n) =>
  `${String(Math.floor(n / 60)).padStart(2, "0")}:${(n % 60).toFixed(1).padStart(4, "0")}`;
const validRange = () =>
  state.duration > 0 &&
  state.end - state.start >= 2 &&
  state.end - state.start <= 15.001;
const referenceMode = () => $('referenceMode').value;
const isLightweightClone = () => referenceMode() === 'prompt_video';
const usesDepthReference = () => ['extracted_depth', 'depth_video'].includes(referenceMode());
const referenceReady = () => referenceMode() === 'region_video'
  ? Boolean(state.regionId && $('regionConfirm').checked && !state.regionStats?.missing_frames && !isCloudEngine())
  : referenceMode() !== 'extracted_depth' || state.depth;
const cloudDepthSelected = () => isCloudEngine() && usesDepthReference();
function referenceFields() {
  if (referenceMode() === 'region_video') return {reference_mode:'region_video',region_id:state.regionId,
    region_confirmed:$('regionConfirm').checked,source_id:state.sourceId,start:state.start,end:state.end};
  return referenceMode() === 'extracted_depth'
    ? { reference_mode: 'extracted_depth', depth_id: state.depthId }
    : {
        reference_mode: referenceMode(), source_id: state.sourceId,
        start: state.start, end: state.end, depth_mode: $('depthMode').value,
      };
}
const CLOUD_DRAFT_KEY = 'clone-center.cloud-depth-draft.v2';
function persistCloudDraft() {
  try { localStorage.setItem(CLOUD_DRAFT_KEY, JSON.stringify({backgroundImage:state.backgroundImage,sceneText:$('sceneText').value})); }
  catch { /* 素材仍保留在本机任务目录 */ }
}
function renderCloudBackground() {
  const item = state.backgroundImage;
  $('backgroundImageDetails').hidden = !item;
  $('backgroundImageName').textContent = item ? item.name : '';
  if (item) {
    const src = API + (item.url || `/api/clone/assets/${item.id}/media`);
    if ($('backgroundImagePreview').getAttribute('src') !== src) $('backgroundImagePreview').src = src;
  } else {
    $('backgroundImagePreview').src = '';
  }
}
const escape = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function presetAspect(name) {
  const ratio = name.match(/(\d+):(\d+)/);
  if (ratio) return { label: `${ratio[1]}:${ratio[2]}`, value: Number(ratio[1]) / Number(ratio[2]) };
  if (name.startsWith("Square")) return { label: "1:1", value: 1 };
  return null;
}
function presetArea(name) {
  const dimensions = name.match(/(\d+)x(\d+)/);
  return dimensions ? Number(dimensions[1]) * Number(dimensions[2]) : 0;
}
function matchSourceResolution(notify = false) {
  const source = state.sourceDimensions;
  if (!source?.width || !source?.height) return;
  const select = $("resolution");
  const presets = Array.from(select.options, (option) => ({
    name: option.value,
    aspect: presetAspect(option.value),
    area: presetArea(option.value),
  })).filter((preset) => preset.aspect && preset.area);
  const ratios = new Map(presets.map((preset) => [preset.aspect.label, preset.aspect.value]));
  if (ratios.size < 2) {
    $("aspectHint").textContent = "正在读取 H3 画幅预设…";
    return;
  }
  const sourceRatio = source.width / source.height;
  const label = Array.from(ratios).reduce((best, candidate) =>
    Math.abs(Math.log(sourceRatio / candidate[1])) < Math.abs(Math.log(sourceRatio / best[1])) ? candidate : best
  )[0];
  const preferredArea = presetArea(state.desiredResolution || select.value) || presets[0].area;
  const candidates = presets.filter((preset) => preset.aspect.label === label);
  const selected = candidates.reduce((best, candidate) =>
    Math.abs(Math.log(candidate.area / preferredArea)) < Math.abs(Math.log(best.area / preferredArea)) ? candidate : best
  ).name;
  const changed = select.value !== selected;
  select.value = selected;
  state.desiredResolution = selected;
  $("aspectHint").textContent = `源视频约 ${label} · 已匹配同画幅预设`;
  $("sourceMeta").textContent = `${source.width} × ${source.height} · ${state.duration.toFixed(1)} s · 匹配 ${label}`;
  if (notify && changed) toast(`已按源视频画幅自动选择 ${selected}`);
}
function toast(message) {
  message = cloudErrorText(message);
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 3600);
}
let progressEtaJob = null;
function progress(title, detail, value, job = null) {
  processing.classList.remove('is-failed');
  processing.dataset.taskId=job?.id || '';
  const percent = Number.isFinite(Number(value))
    ? Math.max(0, Math.min(100, Number(value)))
    : 0;
  $("progressTitle").textContent = title;
  $("progressDetail").textContent = detail;
  $("percent").textContent = `${Math.round(percent)}%`;
  $("progress").value = percent;
  if (job?.status === 'queued' || job?.phase === 'prompt') {
    $('percent').textContent = job.status === 'queued' ? '排队中' : '准备中';
    $('progress').removeAttribute('value');
  }
  const timing = job?.sampling_timing;
  if (timing?.stage === 'sample' && timing.total_steps > 0) {
    $('percent').textContent = `${Math.round(100 * timing.completed_steps / timing.total_steps)}%`;
    $('progress').value = 100 * timing.completed_steps / timing.total_steps;
  } else if (timing && ['decode', 'save'].includes(timing.stage)) {
    $('percent').textContent = '处理中';
    $('progress').value = 100;
  }
  progressEtaJob = job;
  globalThis.GenerationEta.render($('generationEta'), job);
  globalThis.GenerationEta.renderPipeline(document.querySelector('.pipeline'), job);
  processing.classList.toggle('has-generation-eta', !$('generationEta').hidden);
}
function taskDetail(task) {
  const message = String(task.message || "").trim();
  const detail = message || (task.status === "queued" ? "正在等待执行器启动" : "正在同步任务状态");
  if (!task.cloud || !Number(task.created_at)) return detail;
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - Number(task.created_at)));
  return `${detail} · 已用 ${Math.floor(elapsed / 60)} 分 ${String(elapsed % 60).padStart(2, '0')} 秒`;
}
function stopProcessing() {
  state.epoch++;
  if (state.pending)
    api("/tasks/" + state.pending.id, { method: "DELETE" }).catch(() => {});
  state.pending = null;
  state.busy = false;
}
function invalidate() {
  state.editingJobId = null;
  // 同一视频调整区间或处理参数时保留需求草稿；换视频由 loadVideo 清空。
  stopProcessing();
  state.depthId = null;
  invalidateRegion(true);
  state.analysisId = null;
  state.depth = false;
  state.analyzed = false;
  if (state.mode === "multi" && !isLightweightClone()) state.people = [];
  $("depthHint").textContent = `${state.depthModel === "base" ? "Base" : "Small"} · 当前配置待提取`;
  $("pipeDepth").classList.remove("done");
  $("pipeVlm").classList.remove("done");
  sourceView();
  renderPeople();
  progress(
    "区间配置已更新",
    referenceMode() === 'extracted_depth'
      ? "请重新提取当前区间的深度视频，已入队任务不受影响。"
      : "当前参考区间已更新，可直接配置人物并生成。",
    0,
  );
}
function update() {
  resolutionControls.sync();
  const length = state.end - state.start;
  const sourceReference = referenceMode() === 'source_video';
  const lightweight = isLightweightClone();
  const region = referenceMode() === 'region_video';
  $('regionOptions').hidden = !region;
  $('regionPrepare').disabled = !validRange() || !state.sourceId || !state.regions.length || state.regionDraftDirty || state.busy || isCloudEngine();
  $('regionCancelDraft').hidden = !state.regionDraftDirty;
  $('regionAdd').textContent = state.regionEditingIndex === null ? '添加这段演员范围' : '应用这段范围修改';
  $('regionDraw').disabled = !state.duration || state.busy || isCloudEngine();
  $('regionAdd').disabled = !state.regionBox || state.busy || isCloudEngine();
  for (const id of ['regionStart','regionEnd','regionFill','regionLeft','regionTop','regionRight','regionBottom']) $(id).disabled = state.busy;
  $('supplement').disabled = region;
  $('generationGoal').value = sourceReference ? 'reference' : 'replace';
  $('goalHint').textContent = sourceReference
    ? '允许重新演绎；原演员可能残留，动作与镜头也可能变化。'
    : '目标人物来自参考图；通过深度结构尽量贴合原片动作与镜头。';
  if (region) $('goalHint').textContent = '人物图提供目标外观；所选时间段与范围决定替换哪个演员。';
  if (lightweight) $('goalHint').textContent = '原视频 + 人物图 + 提示词直接生成；无需深度、跟踪或视觉解析。';
  const cloud = isCloudEngine();
  $('upscale').disabled = cloud;
  $('lightweightPromptPreview').hidden = !lightweight;
  $('pipeVlm').textContent = lightweight ? '提示词绑定' : '人物解析';
  $('useRecommended').hidden = !sourceReference;
  const directReference = referenceMode() !== 'extracted_depth';
  $('depthExtractionOptions').hidden = directReference;
  $('depthTab').hidden = directReference;
  $('referenceModeHint').textContent = referenceMode() === 'depth_video'
    ? cloud ? '当前深度视频只提供云端结构语义参考，无法逐帧控制；请确认上传的确实是深度视频。'
      : '当前视频只进入逐帧深度控制，不会被当作灰度画面参考。请确认上传的确实是深度视频。'
    : referenceMode() === 'source_video'
      ? '自由参考会同时看到原演员与目标图，可能保留原演员；人物替换请用自动深度。'
      : cloud ? '提取当前区间的深度作为云端结构参考；云端无本地逐帧控制，动作仍可能变化。'
        : '原片用于解析场景与动作；生成器只接收人物图和独立深度控制，避免源人物身份泄漏。';
  if (region) $('referenceModeHint').textContent = '按切镜拆段，在每段首帧紧贴目标演员画框；不替换未选镜头中的观众。当前只支持一个目标演员。';
  if (lightweight) $('referenceModeHint').textContent = '跳过深度、跟踪与人物解析；本地生成后可选 2× 超分。多人时手工配置人数及起始位置，正面、侧面、背面或多视图放在同一人物槽位。';
  $('pipeDepth').textContent = referenceMode() === 'source_video'
    ? '原视频参考' : referenceMode() === 'depth_video' ? '深度直用' : '深度提取';
  if (region) $('pipeDepth').textContent = '演员区域';
  if (lightweight) $('pipeDepth').textContent = '原片直用';
  $('pipeDepth').classList.toggle('done', directReference || state.depth);
  syncGenerationParameters(length);
  $("filename").textContent = state.name || "尚未添加视频";
  $("peopleMode").disabled = !state.duration;
  $("peopleMode").textContent =
    state.mode === "single"
      ? "单人复刻 · 切换"
      : state.mode === "multi"
        ? "多人复刻 · 切换"
        : "选择单人 / 多人";
  $("durationBadge").textContent = `${length.toFixed(1)} s / 15 s`;
  $("durationBadge").classList.toggle(
    "error",
    state.duration > 0 && !validRange(),
  );
  if (document.activeElement !== $("inTime"))
    $("inTime").value = state.start.toFixed(1);
  if (document.activeElement !== $("outTime"))
    $("outTime").value = state.end.toFixed(1);
  const left = state.duration ? (state.start / state.duration) * 100 : 0,
    right = state.duration ? (state.end / state.duration) * 100 : 100;
  $("selectedRange").style.left = `${left}%`;
  $("selectedRange").style.width = `${right - left}%`;
  for (const [id, value] of [
    ["inHandle", state.start],
    ["outHandle", state.end],
  ]) {
    $(id).style.left = `${id === "inHandle" ? left : right}%`;
    $(id).disabled = !state.duration;
    $(id).setAttribute("aria-valuemax", state.duration);
    $(id).setAttribute("aria-valuenow", value.toFixed(1));
    $(id).setAttribute(
      "aria-valuetext",
      `${value.toFixed(1)} 秒，选中 ${length.toFixed(1)} 秒`,
    );
  }
  $("extract").disabled =
    !validRange() || !state.mode || !state.sourceId || state.busy;
  $("extract").textContent = state.depth ? "重新提取" : "提取深度视频";
  $("depthAdjustOpen").hidden = !state.depth;
  $("depthAdjustOpen").disabled = state.busy;
  if ($("depthMode").value !== "portrait") $("personMask").checked = false;
  $("personMask").disabled = state.busy;
  $("analyze").hidden = state.mode !== "multi";
  if (region || lightweight) $("analyze").hidden = true;
  $("analyze").disabled = !validRange() || !state.sourceId || !referenceReady() || state.busy;
  $("analyze").textContent = state.analyzed
    ? "重新解析视频人数"
    : "解析视频人数";
  const allImages =
    state.people.length > 0 &&
    state.people.every(
      (p) => p.images.length && p.images.every((img) => img.id),
    );
  const personImageCount = state.people.reduce((total, person) => total + person.images.length, 0);
  const ready =
    validRange() &&
    state.sourceId &&
    referenceReady() &&
    allImages &&
    (!region || (state.mode === 'single' && state.people.length === 1 && !cloud)) &&
    (!cloudDepthSelected() || personImageCount <= 8) &&
    (state.mode === "single" || state.analyzed || (lightweight && state.mode === 'multi')) &&
    (!cloudDepthSelected() || (state.backgroundImage && $('sceneText').value.trim())) &&
    !state.busy;
  $("generate").disabled = !ready;
  $("generate").textContent = state.editingJobId ? "重新生成" : lightweight ? "开始轻量克隆" : sourceReference ? "生成自由参考视频" : "开始替换人物";
  const missingPerson = state.people.findIndex(person => !person.images.length);
  let reason = '';
  if (!ready) {
    if (!state.duration || !state.sourceId) reason = '请先导入参考视频并等待上传完成。';
    else if (!validRange()) reason = '请选择 2–15 秒的视频区间。';
    else if (state.busy) reason = '正在处理参考素材，请稍候。';
    else if (!state.mode) reason = '请选择单人或多人。';
    else if (missingPerson >= 0) reason = `人物 ${missingPerson + 1} 尚未添加参考图；只替换一位时可将人数改为 1。`;
    else if (!allImages) reason = state.people.length ? '人物参考图正在上传，请稍候。' : '请先解析视频人数并添加人物参考图。';
    else if (region && cloud) reason = '演员区域替换需要使用本地引擎。';
    else if (region && (state.mode !== 'single' || state.people.length !== 1)) reason = '演员区域替换请选择单人模式。';
    else if (!referenceReady()) reason = region ? '请准备演员遮罩，并检查确认预览。' : '请先提取当前区间的深度视频。';
    else if (state.mode !== 'single' && !state.analyzed && !lightweight) reason = '请先解析视频人数。';
    else if (cloudDepthSelected() && personImageCount > 8) reason = '云端深度参考最多支持 8 张人物图。';
    else if (cloudDepthSelected()) reason = '请配置背景图片与场景说明。';
  }
  $('generateHint').textContent = reason;
  $('generateHint').hidden = !reason;
  $('generate').title = reason;
  $('completePerson').hidden = ready || missingPerson < 0;
  $('completePerson').textContent = `配置人物 ${missingPerson + 1} 参考图`;
  renderSupplementTargets();
  const supplement = $("supplement").value.trim();
  $("supplementState").textContent = supplement
    ? `将仅作用于本次提交（${supplement.length}/600），提交后结合素材自动规划。`
    : "提交后结合素材自动规划，不会带入下一条视频。";
  if (region) $("supplementState").textContent = '当前区域模式通过人物参考图配置外观，已输入的补充草稿暂不提交。';
  if (lightweight && !state.materialWorkflow) {
    $('supplementState').textContent = '直接合并本次文字要求，不调用解析或翻译模型；不会带入下一条视频。';
    const request = payload();
    $('lightweightPromptText').value = lightweightSourcePrompt(request.people, length,
      cloud ? Number($('cloudDuration').value) : length, request.supplement);
  }
  if (state.materialWorkflow) updateMaterialControls();
}

function materialEntries() {
  const source = state.url ? [{id:state.sourceId || `source-${state.sourceToken}`,file:{name:state.name},video:true}] : [];
  return referenceEntries([...source, ...state.materials.map(item => ({...item,id:item.key,file:{name:item.name}}))]);
}
function materialRequest() {
  const entries = materialEntries();
  const original = $('supplement').value;
  validateReferences(original, 'references', entries);
  const normalized = normalizeUserReferences(original, assetManifest('references', entries));
  const request = {input_mode:'materials', reference_mode:'prompt_video', source_id:state.sourceId,
    start:state.start,end:state.end,reference_images:state.materials.map(item => item.id),
    user_prompt:original,prompt:normalized.text,auto_prompt:false,manual_prompt:true,source_name:state.name,
    resolution:$('resolution').value,upscale:generationEngine === 'local' && $('upscale').checked,
    steps:Number($('steps').value),seed:$('seed').value.trim()};
  if (isCloudEngine()) Object.assign(request,{reference_type:'source',cloud_resolution:$('cloudResolution').value,
    cloud_ratio:$('cloudRatio').value,output_duration:Number($('cloudDuration').value)});
  return request;
}
function updateMaterialControls() {
  document.body.dataset.inputWorkflow = 'materials';
  $('supplement').disabled = false;
  $('assetCount').textContent = `${state.materials.length} / 9 张`;
  let reason = '';
  if (state.sourceUploadError) reason = state.sourceUploadError;
  else if (!state.duration || !state.sourceId) reason = '请添加参考视频并等待上传完成。';
  else if (!validRange()) reason = '请选择 2–15 秒的视频区间。';
  else if (!isCloudEngine() && (isNativeMaterial(state.sourceId) || state.materials.some(item => isNativeMaterial(item.id)))) reason = '请切回云端生成；本地生成请重新添加素材。';
  else if (state.materials.some(item => item.error)) reason = '参考图片上传失败，请查看图片下方原因并重试或移除。';
  else if (state.materials.some(item => !item.id)) reason = '参考素材正在上传，请稍候。';
  else if (!$('supplement').value.trim()) reason = '请输入提示词，可用 @ 引用素材并描述需求。';
  else {
    try { materialRequest(); } catch (error) { reason = error.message; }
  }
  if (state.compilingMaterialPrompt) reason = '正在准备提示词…';
  $('generate').disabled = Boolean(reason) || submittingGeneration;
  $('generate').textContent = state.compilingMaterialPrompt ? '正在准备提示词…' : submittingGeneration ? '提交中…' : state.editingJobId ? '重新生成' : '开始生成';
  $('generate').title = reason;
  $('sourceUploadError').hidden = !state.sourceUploadError;
  $('sourceUploadError').textContent = state.sourceUploadError;
  $('retryVideoUpload').hidden = !state.sourceUploadError || !state.sourceFile;
  $('retryVideoUpload').disabled = state.sourceUploading;
  $('generateHint').hidden = !reason;
  $('generateHint').textContent = reason;
  $('completePerson').hidden = true;
  $('useRecommended').hidden = true;
  $('lightweightPromptPreview').hidden = false;
  $('lightweightPromptOpen').disabled = Boolean(reason) || submittingGeneration;
  $('supplementState').textContent = '配置 AI 助手时自动优化，未配置时使用默认克隆模板；整幅生成，不使用对象蒙板。';
  $('depthTab').hidden = true;
  $('pipeVlm').textContent = '素材引用';
}
function renderMaterials() {
  const entries = materialEntries();
  const pictures = entries.filter(item => !item.video);
  const source = entries.find(item => item.video);
  $('sourceReference').innerHTML = source ? `<div class="material-card"><button class="material-reference" type="button" data-material-reference="${escape(source.id)}" title="插入 ${escape(source.tag)} · ${escape(source.file.name)}">${source.label}</button></div>` : '';
  $('assetsPanel').dataset.materialCount = String(pictures.length);
  $('materialSlots').innerHTML = pictures.map(item => `<div class="material-card${item.error ? ' upload-failed' : ''}" data-material-id="${escape(item.id)}"><img src="${escape(item.url)}" alt="${escape(item.file.name)}"><button class="material-reference" type="button" data-material-reference="${escape(item.id)}" title="插入 ${escape(item.tag)} · ${escape(item.file.name)}">${item.label} · ${escape(item.file.name)}</button><div class="material-actions">${item.error ? `<button type="button" data-material-retry="${escape(item.id)}" aria-label="重试上传 ${item.label}">重试</button>` : `<button type="button" data-material-move="${escape(item.id)}:-1" aria-label="前移 ${item.label}">←</button><button type="button" data-material-move="${escape(item.id)}:1" aria-label="后移 ${item.label}">→</button>`}<button type="button" data-material-remove="${escape(item.id)}" aria-label="移除 ${item.label}">×</button></div></div>`).join('');
  const failures = pictures.filter(item => item.error);
  $('materialUploadError').hidden = !failures.length;
  $('materialUploadError').textContent = failures.map(item => `${item.label} · ${item.file.name}：${item.error}`).join('\n');
  $('addMaterials').hidden = pictures.length >= 9;
  [$('sourceReference'), $('materialSlots')].flatMap(container => [...container.querySelectorAll('[data-material-reference]')]).forEach(button => button.onclick = () => {
    const item = materialEntries().find(entry => entry.id === button.dataset.materialReference);
    const input = $('supplement');
    input.setRangeText(`${item.tag} `, input.selectionStart, input.selectionEnd, 'end');
    input.focus(); update();
  });
  $('materialSlots').querySelectorAll('[data-material-remove]').forEach(button => button.onclick = () => {
    const previous = materialEntries();
    state.materials = state.materials.filter(item => item.key !== button.dataset.materialRemove);
    materialReferencesChanged(previous);
  });
  $('materialSlots').querySelectorAll('[data-material-retry]').forEach(button => button.onclick = () => {
    const item = state.materials.find(entry => entry.key === button.dataset.materialRetry);
    if (item) uploadMaterial(item);
  });
  $('materialSlots').querySelectorAll('[data-material-move]').forEach(button => button.onclick = () => {
    const [id, offset] = button.dataset.materialMove.split(':');
    const index = state.materials.findIndex(item => item.key === id);
    const next = index + Number(offset);
    if (next < 0 || next >= state.materials.length) return;
    const previous = materialEntries();
    [state.materials[index],state.materials[next]] = [state.materials[next],state.materials[index]];
    materialReferencesChanged(previous);
  });
}
function materialReferencesChanged(previous) {
  $('supplement').value = remapReferences($('supplement').value, previous, materialEntries());
  state.compiledMaterialDraft = null;
  renderMaterials(); update();
}
async function importMaterials(files) {
  if (files.some(file => !isImageFile(file))) { toast('请选择参考图片'); return; }
  if (state.materials.length + files.length > 9) { toast('最多添加 9 张参考图片'); return; }
  const items = files.map(file => ({key:crypto.randomUUID(),file,name:file.name,url:URL.createObjectURL(file)}));
  state.materials.push(...items); renderMaterials(); update();
  for (const item of items) await uploadMaterial(item);
}
async function uploadMaterial(item) {
  if (!state.materials.includes(item) || item.uploading || item.id) return;
  item.uploading = true;
  item.error = '';
  renderMaterials(); update();
  try {
    const asset = await upload(item.file, 'image');
    if (!state.materials.includes(item)) return;
    item.id = asset.id;
    delete item.file;
  } catch (error) {
    if (!state.materials.includes(item)) return;
    item.error = error.message || String(error);
  } finally {
    item.uploading = false;
    if (state.materials.includes(item)) { renderMaterials(); update(); }
  }
}
async function compileMaterialDraft() {
  const sourceToken = state.sourceToken;
  const request = materialRequest();
  const fingerprint = JSON.stringify(request);
  if (state.compiledMaterialDraft?.fingerprint === fingerprint) return state.compiledMaterialDraft.result;
  state.compilingMaterialPrompt = true; update();
  $('materialOptimizerState').textContent = '正在准备克隆提示词…';
  try {
    const result = await post('/compile-prompt', request);
    if (sourceToken !== state.sourceToken) throw Error('输入已清空或更换');
    if (fingerprint !== JSON.stringify(materialRequest())) throw Error('需求、素材或区间已变化，请重新提交');
    if (result.optimizer?.warning) throw Error(result.optimizer.warning);
    if (!['assistant','template'].includes(result.optimizer?.source))
      throw Error('提示词服务版本不兼容，请更新服务后重试；未提交原始提示词');
    validateReferences(result.prompt, 'references', materialEntries());
    state.compiledMaterialDraft = result.optimizer?.warning ? null : {fingerprint,result};
    $('lightweightPromptText').value = result.prompt;
    $('materialOptimizerState').textContent = result.optimizer?.warning || (result.optimizer?.source === 'assistant'
      ? `已通过 ${result.optimizer.name || 'AI 助手'} 优化提示词` : '未配置 AI 助手，已使用默认克隆模板');
    return result;
  } catch (error) {
    if (sourceToken === state.sourceToken) $('materialOptimizerState').textContent = `提示词编译失败：${error.message}`;
    throw error;
  } finally { if (sourceToken === state.sourceToken) { state.compilingMaterialPrompt = false; update(); } }
}
$('addMaterials').onclick = () => $('materialFiles').click();
$('materialFiles').onchange = event => { const files = Array.from(event.target.files); event.target.value = ''; importMaterials(files); };
bindImageDrop(document.querySelector('.reference-assets'), importMaterials);
$('queueToggle').onclick = () => {
  const collapsed = document.querySelector('.layout').classList.toggle('queue-collapsed');
  $('queueToggle').setAttribute('aria-expanded', String(!collapsed));
  $('queueToggle').title = collapsed ? '展开生成任务' : '收起生成任务';
  $('queueBody').inert = collapsed;
};
if ($('materialEditor').querySelector('.work-prompt')) new ReferenceMenu($('materialEditor'), materialEntries);
function renderSupplementTargets() {
  const select = $("supplementTarget");
  const previous = select.value || "all";
  select.replaceChildren();
  select.append(new Option("按文字指定对象", "all"));
  state.people.forEach((person, index) => {
    const trackId = person.trackId || index + 1;
    select.append(new Option(`人物 ${index + 1}（${person.position}，轨迹 ${trackId}）`, `subject:${index + 1}:${trackId}`));
  });
  select.value = previous;
  if (!select.value) select.value = "all";
}
function syncGenerationParameters(length) {
  const duration = Math.max(0, length);
  $("parameterDuration").textContent = duration ? `${duration.toFixed(1)} 秒` : "-- 秒";
}
function makePeople(count) {
  let nextTrack = Math.max(0, ...state.people.map((p) => p.trackId || p.id)) + 1;
  state.people = Array.from(
    { length: count },
    (_, i) =>
      state.people[i] || {
        id: i + 1,
        trackId: nextTrack++,
        position:
          count === 1
            ? "中间"
            : i === 0
              ? "左侧"
              : i === count - 1
                ? "右侧"
                : "中间",
        images: [],
      },
  );
  renderPeople();
  update();
}
function renderPeople() {
  if (state.materialWorkflow) { renderMaterials(); return; }
  $("assetCount").textContent = `${state.people.length} 位人物`;
  $("peopleTools").hidden = !state.people.length || state.mode === 'single';
  $("personCount").disabled = state.mode === "single";
  $("personCount").value = String(state.people.length || 2);
  visiblePerson = Math.min(visiblePerson, Math.max(0, state.people.length - 1));
  $('personPicker').hidden = state.people.length < 2;
  $('activePerson').replaceChildren(...state.people.map((person, index) =>
    new Option(`人物 ${index + 1} · ${person.images.length ? `${person.images.length} 张图` : '缺少参考图'}`, String(index))));
  $('activePerson').value = String(visiblePerson);
  if (!state.people.length) {
    $("slots").innerHTML =
      '<div class="slotempty"><strong>添加人物参考图</strong><span>先导入视频，再选择单人或多人</span></div>';
    return;
  }
  $("slots").innerHTML = state.people
    .map(
      (p, i) =>
        `<div class="slot" data-image-slot="${i}"${i !== visiblePerson ? " hidden" : ""} style="--image-columns:${Math.min(3, Math.max(1, p.images.length))};--image-rows:${Math.max(1, Math.ceil(p.images.length / 3))}"><div class="slotheader"><strong>人物 ${i + 1}</strong><select aria-label="人物 ${i + 1} 空间位置" data-position="${i}">${positionNames.map((x) => `<option${x === p.position ? " selected" : ""}>${x}</option>`).join("")}</select></div><p>Subject ${i + 1} · 起始位置，身份持续绑定</p><div class="slotimages">${p.images.map((img, j) => `<div class="thumb"><img src="${escape(img.url)}" alt="${escape(img.name)}"><button data-remove="${i}:${j}" aria-label="移除人物 ${i + 1} 第 ${j + 1} 张图片">×</button></div>`).join("")}</div><button data-upload="${i}">添加图片</button><small>${p.images.length ? `${p.images.length} 张图已绑定` : "支持多张 · 正面 / 侧面 / 全身 · 可拖入图片"}</small></div>`,
    )
    .join("");
  $("slots")
    .querySelectorAll("[data-upload]")
    .forEach(
      (b) =>
        (b.onclick = () => {
          activeSlot = Number(b.dataset.upload);
          $("imageFiles").click();
        }),
    );
  $("slots").querySelectorAll("[data-image-slot]").forEach((slot) => {
    bindImageDrop(slot, (files) => importPersonImages(Number(slot.dataset.imageSlot), files));
  });
  $("slots")
    .querySelectorAll("[data-position]")
    .forEach(
      (s) =>
        (s.onchange = () => {
          state.people[Number(s.dataset.position)].position = s.value;
          update();
        }),
    );
  $("slots")
    .querySelectorAll("[data-remove]")
    .forEach(
      (b) =>
        (b.onclick = () => {
          const [i, j] = b.dataset.remove.split(":").map(Number);
          state.people[i].images.splice(j, 1);
          renderPeople();
          update();
        }),
    );
}
$('activePerson').onchange = () => {
  visiblePerson = Number($('activePerson').value);
  renderPeople();
};
$('completePerson').onclick = () => {
  const index = state.people.findIndex(person => !person.images.length);
  if (index < 0) return;
  visiblePerson = index;
  setAssetView(true);
  renderPeople();
  $('slots').querySelector(`[data-upload="${index}"]`)?.focus();
};
function setAssetView(assets) {
  if (isCloneMobileLayout()) setCloneMobileView(assets ? 'flow' : 'workspace');
}
$('assetsToggle').onclick = () => setAssetView(true);
$('lightweightPromptOpen').onclick = async () => {
  const token = state.sourceToken;
  try {
    if (state.materialWorkflow) await compileMaterialDraft();
    if (token !== state.sourceToken) return;
    $('lightweightPromptDialog').showModal();
  } catch (error) { if (token === state.sourceToken) toast(error.message); }
};
$('lightweightPromptClose').onclick = () => $('lightweightPromptDialog').close();
$('cloudSceneOpen').onclick = () => $('cloudSceneDialog').showModal();
$('cloudSceneClose').onclick = () => $('cloudSceneDialog').close();
function loadVideo(url, name, sample = false, restoreRange = null) {
  invalidateRegion(true);
  const preferredResolution = $("resolution").value || state.desiredResolution;
  state.sourceId = null;
  state.sourceFile = null;
  state.sourceUploadError = '';
  state.sourceUploading = false;
  state.depthId = null;
  state.analysisId = null;
  state.sourceToken++;
  if (!state.materialWorkflow) {
    $("supplement").value = "";
    $("supplementTarget").value = "all";
  }
  state.compiledMaterialDraft = null;
  stopProcessing();
  video.pause();
  Object.assign(state, {
    url,
    name,
    sample,
    restoreRange,
    sourceDimensions: null,
    desiredResolution: preferredResolution,
    duration: 0,
    start: 0,
    end: 0,
    mode: null,
    people: [],
    depth: false,
    analyzed: false,
    editingJobId: null,
  });
  state.previewJobId = null;
  reportResultPlaybackAvailable(false);
  $("resultTab").hidden = true;
  resultVideo.pause();
  resultVideo.removeAttribute("src");
  resultVideo.load();
  video.src = url;
  video.load();
  const uploadPreview = $("uploadPreview");
  uploadPreview.src = url;
  uploadPreview.load();
  if (state.materialWorkflow) renderMaterials();
  $("uploadPlaceholder").hidden = true;
  uploadPreview.hidden = false;
  $("uploadReplace").hidden = false;
  $("uploadVideo").classList.add("has-video");
  $("videoEmpty").hidden = true;
  $("videoLabel").hidden = false;
  sourceView();
  renderPeople();
  update();
  progress("正在读取视频", "读取时长与画面尺寸，随后选择单人或多人。", 0);
}
$("uploadPreview").addEventListener("loadedmetadata", (event) => {
  const preview = event.currentTarget;
  if (Number.isFinite(preview.duration) && preview.duration > 0.2)
    preview.currentTime = Math.min(0.2, preview.duration / 2);
});
video.addEventListener("loadedmetadata", () => {
  if (!state.url) return;
  if (!Number.isFinite(video.duration) || video.duration <= 0) {
    toast("无法读取视频时长，请换用 MP4 或 WebM。");
    return;
  }
  state.duration = video.duration;
  state.sourceDimensions = { width: video.videoWidth, height: video.videoHeight };
  const restored = state.restoreRange;
  state.restoreRange = null;
  state.start = restored ? Math.max(0, Math.min(restored.start, video.duration)) : 0;
  state.end = restored ? Math.min(restored.end, video.duration) : Math.min(8, video.duration);
  $("seek").max = video.duration;
  $("seek").value = 0;
  for (const id of ["play", "seek", "inTime", "outTime"])
    $(id).disabled = false;
  $("inTime").max = video.duration;
  $("outTime").max = video.duration;
  $("sourceMeta").textContent =
    `${video.videoWidth} × ${video.videoHeight} · ${video.duration.toFixed(1)} s`;
  matchSourceResolution(true);
  $("playtime").textContent = `${fmt(0)} / ${fmt(video.duration)}`;
  $("midtick").textContent = fmt(video.duration / 2);
  $("endtick").textContent = fmt(video.duration);
  progress("视频已就绪", restored ? "深度视频已复用，可调整参数后重新生成。" : "选择单人或多人，再调整复刻区间。", 0);
  update();
  if (!state.materialWorkflow && !restored && !$("modeDialog").open) $("modeDialog").showModal();
});
video.addEventListener("error", () => {
  if (!state.url) return;
  state.duration = 0;
  state.sourceDimensions = null;
  state.depth = false;
  state.people = [];
  state.mode = null;
  for (const id of ["play", "seek", "inTime", "outTime"]) $(id).disabled = true;
  renderPeople();
  update();
  toast("浏览器无法解码该视频，请使用 H.264 MP4 或 WebM。");
});
video.addEventListener("timeupdate", () => {
  $("seek").value = video.currentTime;
  $("playtime").textContent =
    `${fmt(video.currentTime)} / ${fmt(state.duration)}`;
  if (
    !video.paused &&
    $("loop").getAttribute("aria-pressed") === "true" &&
    video.currentTime >= state.end
  )
    video.currentTime = state.start;
});
video.onplay = () => {
  $("play").textContent = "暂停";
  if (
    !state.busy &&
    !state.jobs.some((j) => ["生成中", "超分中"].includes(j.status))
  )
    processing.hidden = true;
};
video.onpause = () => ($("play").textContent = "播放");
$("play").onclick = async () => {
  if (!depthVideo.hidden) {
    if (depthVideo.paused) {
      try {
        await depthVideo.play();
      } catch (e) {
        toast(e.message);
      }
    } else depthVideo.pause();
    return;
  }
  if (video.paused) {
    if (video.currentTime < state.start || video.currentTime >= state.end)
      video.currentTime = state.start;
    try {
      await video.play();
    } catch {
      toast("播放失败，请检查视频格式。");
    }
  } else video.pause();
};
$("seek").oninput = () => {
  video.currentTime = Number($("seek").value);
  if (!depthVideo.hidden)
    depthVideo.currentTime = Math.max(0, video.currentTime - state.start);
};
depthVideo.onplay = () => {
  $("play").textContent = "暂停";
  if (!state.busy) processing.hidden = true;
};
depthVideo.onpause = () => ($("play").textContent = "播放");
depthVideo.ontimeupdate = () => {
  if (depthVideo.hidden) return;
  $("seek").value = state.start + depthVideo.currentTime;
  $("playtime").textContent =
    fmt(depthVideo.currentTime) + " / " + fmt(state.end - state.start);
  if (depthVideo.ended && $("loop").getAttribute("aria-pressed") === "true") {
    depthVideo.currentTime = 0;
    depthVideo.play().catch(() => {});
  }
};
$("loop").onclick = () => {
  const on = $("loop").getAttribute("aria-pressed") !== "true";
  $("loop").setAttribute("aria-pressed", String(on));
  $("loop").classList.toggle("selected", on);
};
function setRange(which, value) {
  if (!Number.isFinite(value)) return;
  const v = Math.max(0, Math.min(state.duration, value));
  if (which === "in") state.start = Math.min(v, state.end - 0.1);
  else state.end = Math.max(v, state.start + 0.1);
  invalidate();
  video.currentTime =
    which === "in" ? state.start : Math.max(state.start, state.end - 0.04);
  update();
}
for (const [id, which] of [
  ["inHandle", "in"],
  ["outHandle", "out"],
]) {
  const el = $(id);
  el.onpointerdown = (e) => {
    if (!state.duration) return;
    video.pause();
    el.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const r = $("timeline").getBoundingClientRect();
      setRange(which, ((ev.clientX - r.left) / r.width) * state.duration);
    };
    el.onpointermove = move;
    el.onpointerup = () => {
      el.onpointermove = null;
    };
    el.onlostpointercapture = () => (el.onpointermove = null);
  };
  el.onkeydown = (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      setRange(
        which,
        (which === "in" ? state.start : state.end) +
          (e.key === "ArrowRight" ? 0.1 : -0.1),
      );
    }
  };
}
for (const [id, which] of [
  ["inTime", "in"],
  ["outTime", "out"],
]) {
  $(id).oninput = () => {
    if ($(id).value !== "") setRange(which, Number($(id).value));
  };
  $(id).onchange = () => {
    if ($(id).value !== "") setRange(which, Number($(id).value));
  };
  $(id).onblur = () =>
    ($(id).value = (which === "in" ? state.start : state.end).toFixed(1));
}
async function importVideo(file) {
  if (!file) return;
  if (
    !file.type.startsWith("video/") &&
    !/\.(mp4|webm|mov|m4v)$/i.test(file.name)
  ) {
    toast("请选择视频文件");
    return;
  }
  loadVideo(URL.createObjectURL(file), file.name);
  state.sourceFile = file;
  await uploadSourceVideo(file, state.sourceToken);
}
async function uploadSourceVideo(file, token) {
  state.sourceUploading = true;
  state.sourceUploadError = '';
  update();
  try {
    const asset = await upload(file, "video");
    if (token !== state.sourceToken) return;
    state.sourceId = asset.id;
    if (state.materialWorkflow) renderMaterials();
    update();
  } catch (e) {
    if (token === state.sourceToken) {
      state.sourceUploadError = `参考视频上传失败：${e.message}`;
      toast(e.message);
    }
  } finally {
    if (token === state.sourceToken) {
      state.sourceUploading = false;
      update();
    }
  }
}
$('retryVideoUpload').onclick = () => {
  if (state.sourceFile && !state.sourceUploading) uploadSourceVideo(state.sourceFile, state.sourceToken);
};
async function importBackgroundImage(file) {
  if (!file) return;
  if (!file.type.startsWith('image/') && !/\.(png|jpe?g|webp)$/i.test(file.name)) {
    toast('请选择背景图片文件'); return;
  }
  const token = state.inputEpoch;
  try {
    const asset = await upload(file, 'image');
    if (token !== state.inputEpoch) return;
    state.backgroundImage = {id:asset.id,name:asset.name,url:asset.url};
    persistCloudDraft(); renderCloudBackground(); update();
  } catch (error) { if (token === state.inputEpoch) toast(error.message); }
}
$('backgroundImageUpload').onclick = () => $('backgroundImageFile').click();
$('backgroundImageFile').onchange = event => {
  importBackgroundImage(event.target.files[0]);
  event.target.value = '';
};
$('backgroundImageRemove').onclick = () => {
  state.backgroundImage = null; persistCloudDraft(); renderCloudBackground(); update();
};
bindImageDrop($('cloudDepthOptions'), (files) => importBackgroundImage(files[0]));
$('sceneText').oninput = () => { persistCloudDraft(); update(); };
try {
  const draft = JSON.parse(localStorage.getItem(CLOUD_DRAFT_KEY) || '{}');
  if (draft.backgroundImage?.id) state.backgroundImage = draft.backgroundImage;
  if (typeof draft.sceneText === 'string') $('sceneText').value = draft.sceneText;
  renderCloudBackground();
} catch { /* 无有效草稿时继续 */ }
$("uploadVideo").onclick = () => $("videoFile").click();
$("videoFile").onchange = (e) => {
  importVideo(e.target.files[0]);
  e.target.value = "";
};
for (const id of ["dropzone", "uploadVideo"]) {
  const el = $(id);
  el.ondragover = (e) => {
    e.preventDefault();
    el.classList.add("dragover");
  };
  el.ondragleave = () => el.classList.remove("dragover");
  el.ondrop = (e) => {
    e.preventDefault();
    el.classList.remove("dragover");
    importVideo(e.dataTransfer.files[0]);
  };
}

$("peopleMode").onclick = () => $("modeDialog").showModal();
function chooseMode(mode) {
  if (state.mode === mode) {
    $("modeDialog").close();
    return;
  }
  if (state.depth) {
    state.analysisId = null;
    state.analyzed = false;
    $("pipeVlm").classList.remove("done");
  } else invalidate();
  state.mode = mode;
  state.analyzed = false;
  state.people = [];
  if (mode === "single") makePeople(1);
  else if (isLightweightClone()) makePeople(2);
  else renderPeople();
  $("modeDialog").close();
  progress("人物模式已更新", referenceReady()
    ? "视频参考已就绪，继续配置人物参考图。"
    : "拖动 I / O 手柄后，点击「提取深度视频」。", 0);
  update();
}
$("single").onclick = () => chooseMode("single");
$("multi").onclick = () => chooseMode("multi");
$("referenceMode").onchange = () => {
  referenceModeExplicit = true;
  $('regionSelection').hidden = true;
  stopProcessing();
  state.analysisId = null;
  state.analyzed = false;
  if (state.mode === 'multi') {
    if (isLightweightClone()) { if (!state.people.length) makePeople(2); }
    else state.people = [];
  }
  if (referenceMode() !== 'extracted_depth' && $('depthTab').classList.contains('active')) sourceView();
  $('pipeVlm').classList.remove('done');
  renderPeople();
  syncEngineControls();
  progress('视频参考方式已更新', referenceMode() === 'extracted_depth'
    ? '请提取当前区间的深度视频。'
    : referenceMode() === 'region_video' ? '添加演员出现的时间段与范围，准备并检查遮罩预览。'
    : isLightweightClone() ? '原片与人物图直接参与生成，补充要求直接合并为提示词。'
    : referenceMode() === 'depth_video'
      ? '将直接使用当前深度视频，无需提取。'
      : '将跳过逐帧结构控制，仅近似参考原视频动作；严格复刻请使用自动深度。', 0);
  update();
  persistGenerationSettings();
};

function invalidateRegion(clear = false) {
  state.regionId = null;
  state.regionStats = null;
  $('regionConfirm').checked = false;
  $('regionConfirm').disabled = true;
  $('regionPreview').pause();
  $('regionPreview').hidden = true;
  $('regionSelection').hidden = true;
  if (clear) {
    state.regions = [];
    state.regionBox = null;
    state.regionDraftDirty = false;
    state.regionEditingIndex = null;
    $('regionList').replaceChildren();
  }
  $('regionReviewHint').textContent = '范围已变化，请准备并检查完整遮罩预览。';
}
function renderRegions() {
  $('regionList').replaceChildren();
  state.regions.forEach((r, index) => {
    const row = document.createElement('div'); row.className='region-row';
    const label = document.createElement('span');
    label.textContent = `${r.start.toFixed(3)}–${r.end.toFixed(3)}s · ${r.fill === 'track' ? '视频跟踪轮廓' : r.fill === 'box' ? '整框补全' : r.fill === 'crop' ? '局部精细轮廓' : '自动轮廓'}`;
    const edit = document.createElement('button'); edit.type='button';edit.textContent='编辑';edit.setAttribute('aria-label',`编辑演员区域 ${index+1}`);
    edit.onclick = () => {
      if(state.busy)return;
      invalidateRegion(); state.regionEditingIndex=index;state.regionBox=[...r.box];state.regionDraftDirty=true;
      $('regionStart').value=r.start.toFixed(6);$('regionEnd').value=r.end.toFixed(6);$('regionFill').value=r.fill;
      ['regionLeft','regionTop','regionRight','regionBottom'].forEach((id,i)=>$(id).value=(r.box[i]*100).toFixed(2));
      $('regionBoxHint').textContent=`正在编辑第 ${index+1} 段；修改时间、坐标或画框后应用。`;
      update();
    };
    const remove = document.createElement('button');remove.type='button';remove.textContent='删除';remove.setAttribute('aria-label',`删除演员区域 ${index+1}`);
    remove.onclick = () => {
      if(state.busy)return;
      state.regions.splice(index,1);state.regionEditingIndex=null;state.regionDraftDirty=false;state.regionBox=null;
      invalidateRegion();renderRegions();update();
    };
    row.append(label,edit,remove);$('regionList').append(row);
  });
}
$('regionDraw').onclick = () => {
  invalidateRegion();
  state.regionDraftDirty = true;
  sourceView(); video.pause();
  video.currentTime = state.start + Number($('regionStart').value || 0);
  $('regionSelection').hidden = false;
  $('regionSelectionBox').style.width = '0';
  $('regionSelectionBox').style.height = '0';
  $('regionBoxHint').textContent = '在该段首帧紧贴目标演员画框，完成后点击「添加这段演员范围」。';
  setCloneMobileView('workspace');
  update();
};
let regionDrag = null;
function regionPoint(event) {
  const outer = $('regionSelection').getBoundingClientRect();
  const ratio = video.videoWidth / video.videoHeight || state.sourceDimensions?.width / state.sourceDimensions?.height || 4 / 3;
  const width = Math.min(outer.width, outer.height * ratio), height = width / ratio;
  const left = outer.left + (outer.width - width) / 2, top = outer.top + (outer.height - height) / 2;
  return {x:Math.max(0,Math.min(1,(event.clientX-left)/width)),y:Math.max(0,Math.min(1,(event.clientY-top)/height)),
    left:left-outer.left,top:top-outer.top,width,height};
}
$('regionSelection').onpointerdown = event => {
  invalidateRegion();
  $('regionSelection').hidden = false;
  state.regionDraftDirty = true;
  regionDrag = regionPoint(event);
  $('regionSelection').setPointerCapture(event.pointerId);
};
$('regionSelection').onpointermove = event => {
  if (!regionDrag) return;
  const p = regionPoint(event), a = regionDrag;
  state.regionBox = [Math.min(a.x,p.x), Math.min(a.y,p.y), Math.max(a.x,p.x), Math.max(a.y,p.y)];
  const [left,top,right,bottom] = state.regionBox;
  Object.assign($('regionSelectionBox').style,{left:`${p.left+left*p.width}px`,top:`${p.top+top*p.height}px`,
    width:`${(right-left)*p.width}px`,height:`${(bottom-top)*p.height}px`});
};
$('regionSelection').onpointerup = () => {
  regionDrag = null;
  $('regionSelection').hidden = true;
  $('regionBoxHint').textContent = state.regionBox ? '演员范围已选取；回到流程区添加时间段。' : '请拖动画框选取演员。';
  if (state.regionBox) ['regionLeft','regionTop','regionRight','regionBottom'].forEach((id,i)=>$(id).value=(state.regionBox[i]*100).toFixed(1));
  if (isCloneMobileLayout()) setCloneMobileView('flow');
  update();
};
$('regionSelection').onpointercancel = () => {regionDrag=null;$('regionSelection').hidden=true;};
for (const id of ['regionLeft','regionTop','regionRight','regionBottom']) $(id).oninput = () => {
  invalidateRegion(); state.regionDraftDirty=true;
  const box = ['regionLeft','regionTop','regionRight','regionBottom'].map(id=>Number($(id).value)/100);
  state.regionBox = box.every(v=>Number.isFinite(v)&&v>=0&&v<=1)&&box[2]>box[0]&&box[3]>box[1] ? box : null;
  update();
};
$('regionCancelDraft').onclick = () => {state.regionDraftDirty=false;state.regionEditingIndex=null;state.regionBox=null;$('regionSelection').hidden=true;update();};
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('regionSelection').hidden) {
    event.preventDefault(); regionDrag=null; $('regionCancelDraft').onclick();
  }
});
$('regionAdd').onclick = () => {
  const start = Math.round(Number($('regionStart').value)*24)/24, end = Math.round(Number($('regionEnd').value)*24)/24;
  if (!state.regionBox || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > state.end-state.start+.001) {
    toast('请填写选中区间内的有效相对时间并选取演员范围'); return;
  }
  if ((state.regionBox[2]-state.regionBox[0])*(state.regionBox[3]-state.regionBox[1])>.8) {toast('范围过大，请保留背景用于镜头校正');return;}
  if (state.regions.length>=24 && state.regionEditingIndex===null) {toast('最多24段演员范围，请缩短复刻区间');return;}
  if (state.regions.some((r,i) => i!==state.regionEditingIndex && r.start < end && start < r.end)) {toast('时间段不可重叠，请编辑对应原段');return;}
  const region = {start,end,box:[...state.regionBox],fill:$('regionFill').value};
  if (state.regionEditingIndex !== null) state.regions[state.regionEditingIndex]=region;
  else state.regions.push(region);
  state.regionEditingIndex = null;
  state.regionDraftDirty = false;
  state.regions.sort((a,b)=>a.start-b.start);
  invalidateRegion(); renderRegions(); update();
};
$('regionConfirm').onchange = update;
$('regionPrepare').onclick = async () => {
  if ($('regionPrepare').disabled) return;
  invalidateRegion();
  const epoch = state.epoch;
  state.busy = true; update();
  try {
    const task = await post('/tasks/region',{source_id:state.sourceId,start:state.start,end:state.end,regions:state.regions});
    if (epoch !== state.epoch) {if (epoch > state.clearedProcessingEpoch) await api('/tasks/'+task.id,{method:'DELETE'});return;}
    state.pending = {id:task.id,kind:'region'};
    processing.hidden = false;
    progress('演员遮罩准备', task.message, task.percent);
  } catch(error) {if (epoch === state.epoch) {state.busy=false;update();toast(error.message);}}
};
$('generationGoal').onchange = () => {
  $('referenceMode').value = $('generationGoal').value === 'reference' ? 'source_video' : 'prompt_video';
  $('referenceMode').onchange();
};
$('useRecommended').onclick = () => {
  $('generationGoal').value = 'replace';
  $('generationGoal').onchange();
};
$("depthMode").onchange = () => {
  if (state.duration) invalidate();
  persistGenerationSettings();
  update();
};
$("personMask").onchange = () => {
  if ($("personMask").checked && $("depthMode").value !== "portrait") {
    $("depthMode").value = "portrait";
    persistGenerationSettings();
  }
  if (state.duration) invalidate();
  update();
};
async function startProcess(kind) {
  if (state.busy) return;
  if (kind === "depth") invalidate();
  else {
    state.analysisId = null;
    state.analyzed = false;
    state.people = [];
    renderPeople();
  }
  const epoch = state.epoch;
  state.busy = true;
  update();
  try {
    const task = await post(
      "/tasks/" + kind,
      kind === "depth"
        ? {
            source_id: state.sourceId,
            start: state.start,
            end: state.end,
            depth_mode: $("depthMode").value,
            depth_model: state.depthModel,
            remove_background: $("depthMode").value === "portrait" && $("personMask").checked,
            person_mask_model: $("depthMode").value === "portrait" && $("personMask").checked ? "quality" : undefined,
          }
        : referenceFields(),
    );
    if (epoch !== state.epoch) {
      if (epoch > state.clearedProcessingEpoch) await api("/tasks/" + task.id, { method: "DELETE" });
      return;
    }
    state.pending = { id: task.id, kind };
    progress(
      kind === "depth" ? "深度视频提取" : "Qwen 人物解析",
      task.message,
      task.percent,
    );
    processing.hidden = false;
  } catch (e) {
    if (epoch === state.epoch) {
      state.busy = false;
      update();
      toast(e.message);
    }
  }
}
$("extract").onclick = () => startProcess("depth");
const DEPTH_DEFAULTS = {far_point:0,near_point:100,midtone:0,contrast:100,brightness:0,smooth:0,invert:false};
function depthControlsFromDialog() {
  const controls = {};
  document.querySelectorAll('[data-depth-control]').forEach(input => { controls[input.dataset.depthControl] = Number(input.value); });
  controls.invert = $('depthInvert').checked;
  return controls;
}
function previewDepthControls() {
  const controls = depthControlsFromDialog();
  const range = Math.max(1, controls.near_point - controls.far_point);
  const contrast = Math.min(3, (100 / range) * controls.contrast / 100);
  const brightness = Math.max(0.1, 1 + (controls.brightness - controls.far_point / 2 + controls.midtone / 4) / 100);
  $('depthAdjustPreview').style.filter = `contrast(${contrast}) brightness(${brightness}) blur(${controls.smooth / 12}px) invert(${controls.invert ? 1 : 0})`;
}
function setDepthControls(controls) {
  document.querySelectorAll('[data-depth-control]').forEach(input => {
    input.value = String(controls[input.dataset.depthControl] ?? DEPTH_DEFAULTS[input.dataset.depthControl]);
    input.closest('label').querySelector('output').textContent = input.value;
  });
  $('depthInvert').checked = Boolean(controls.invert);
  previewDepthControls();
}
document.querySelectorAll('[data-depth-control]').forEach(input => {
  input.addEventListener('input', () => { input.closest('label').querySelector('output').textContent = input.value; previewDepthControls(); });
});
$('depthInvert').addEventListener('change', previewDepthControls);
$("depthAdjustOpen").onclick = () => {
  if (!state.depthId || state.busy) return;
  const task = state.jobs.find(item => item.id === state.depthId);
  setDepthControls(task?.request?.controls || DEPTH_DEFAULTS);
  const preview = $('depthAdjustPreview');
  preview.src = `${API}/api/clone/tasks/${task?.request?.raw_depth_id || state.depthId}/media?v=${Date.now()}`;
  $('depthAdjustDialog').showModal();
};
$("depthAdjustClose").onclick = () => $('depthAdjustDialog').close();
$("depthAdjustDialog").addEventListener('close', () => {
  $('depthAdjustPreview').pause();
  $('depthAdjustPreview').removeAttribute('src');
  $('depthAdjustPreview').load();
});
$("depthAdjustReset").onclick = () => setDepthControls(DEPTH_DEFAULTS);
$("depthAdjustApply").onclick = async () => {
  if (state.busy || !state.depthId) return;
  const controls = depthControlsFromDialog();
  if (controls.near_point <= controls.far_point) { toast('近景白点必须大于远景黑点'); return; }
  const epoch = state.epoch;
  const originalId = state.depthId;
  state.busy = true;
  update();
  try {
    const task = await post('/tasks/depth', {adjusted_from:originalId,controls});
    if (epoch !== state.epoch) { if (epoch > state.clearedProcessingEpoch) await api('/tasks/' + task.id, {method:'DELETE'}); return; }
    state.pending = {id:task.id,kind:'depth'};
    $('depthAdjustDialog').close();
    progress('深度视频调参', task.message, task.percent);
    processing.hidden = false;
  } catch (error) {
    if (epoch === state.epoch) { state.busy = false; update(); toast(error.message); }
  }
};
$("analyze").onclick = () => startProcess("analyze");
$("personCount").onchange = () => {
  makePeople(Number($("personCount").value));
  toast("人数已修正，请检查各人物位置与参考图。");
};
function isImageFile(file) {
  return file.type.startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(file.name);
}
function bindImageDrop(element, receive) {
  element.addEventListener('dragover', (event) => {
    if (!event.dataTransfer?.types?.includes('Files')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    element.classList.add('dragover');
  });
  element.addEventListener('dragleave', (event) => {
    if (!element.contains(event.relatedTarget)) element.classList.remove('dragover');
  });
  element.addEventListener('drop', (event) => {
    event.preventDefault();
    element.classList.remove('dragover');
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length) receive(files);
  });
}
async function importPersonImages(index, droppedFiles) {
  const p = state.people[index];
  if (!p) return;
  const files = droppedFiles.filter(isImageFile);
  if (files.length !== droppedFiles.length) {
    toast("请选择图片文件");
    return;
  }
  if (
    state.people.reduce((n, x) => n + x.images.length, 0) + files.length >
    9
  ) {
    toast("H3参考图总数不能超过9张");
    return;
  }
  for (const f of files) {
    const img = { name: f.name, url: URL.createObjectURL(f) };
    p.images.push(img);
    renderPeople();
    update();
    try {
      const a = await upload(f, "image");
      Object.assign(img, { id: a.id });
    } catch (err) {
      p.images.splice(p.images.indexOf(img), 1);
      toast(err.message);
    }
    renderPeople();
    update();
  }
}
$("imageFiles").onchange = (e) => {
  const files = Array.from(e.target.files);
  e.target.value = "";
  importPersonImages(activeSlot, files);
};
$("supplement").oninput = () => {
  if (state.materialWorkflow) {
    state.compiledMaterialDraft = null;
    $('materialOptimizerState').textContent = '';
  }
  update();
};
async function persistGenerationSettings() {
  try {
    await post("/settings", {
      reference_mode: referenceMode(),
      depth_mode: $("depthMode").value,
      depth_model: state.depthModel,
      resolution: $("resolution").value,
      upscale: $("upscale").checked,
      steps: Number($("steps").value),
      seed: $("seed").value.trim(),
    });
  } catch {
    // 离线浏览模式不写入，服务恢复后再次调整即可保存。
  }
}
$("upscale").onchange = () => { persistGenerationSettings(); update(); };
$("steps").onchange = persistGenerationSettings;
$("seed").onchange = persistGenerationSettings;
function sourceView() {
  processing.dataset.taskId='';
  if(processing.classList.contains('is-failed')){processing.hidden=true;processing.classList.remove('is-failed');}
  const switching = video.hidden;
  setAssetView(false);
  resultVideo.pause();
  resultVideo.hidden = true;
  reportResultPlaybackAvailable(false);
  depthVideo.hidden = true;
  depthVideo.pause();
  video.hidden = false;
  if (switching && state.url) globalThis.WorkbenchMotion?.enter(video, 200);
  $("sourceTab").classList.add("active");
  $("depthTab").classList.remove("active");
  $("resultTab").classList.remove("active");
}
$("sourceTab").onclick = sourceView;
$("depthTab").onclick = () => {
  if (!state.depthId) {
    toast("请先提取深度视频");
    return;
  }
  const switching = depthVideo.hidden;
  const src = API + "/api/clone/tasks/" + state.depthId + "/media";
  const previousSrc = depthVideo.getAttribute("src");
  const changed = previousSrc !== src;
  const resume = changed && previousSrc && !depthVideo.hidden && !depthVideo.paused && !depthVideo.ended;
  const position = previousSrc && !depthVideo.hidden
    ? depthVideo.currentTime : Math.max(0, video.currentTime - state.start);
  video.pause();
  resultVideo.pause();
  resultVideo.hidden = true;
  reportResultPlaybackAvailable(false);
  if (changed) {
    depthVideo.pause();
    depthVideo.src = src;
    const duration = state.end - state.start;
    depthVideo.currentTime = position >= duration - 0.1 ? 0 : Math.max(0, position);
  }
  depthVideo.hidden = false;
  if (switching) globalThis.WorkbenchMotion?.enter(depthVideo, 200);
  video.hidden = true;
  $("sourceTab").classList.remove("active");
  $("depthTab").classList.add("active");
  $("resultTab").classList.remove("active");
  if (resume) depthVideo.play().catch((error) => { $("play").textContent = "播放"; toast(error.message); });
  else if (depthVideo.paused) $("play").textContent = "播放";
};
function resultUrl(job, videoPath) { return job.cloud ? videoPath : API + videoPath; }
function showResultInPreview(job, videoPath = job.result.video) {
  const switching = resultVideo.hidden;
  const src = resultUrl(job, videoPath);
  state.previewJobId = job.id;
  state.selectedJobId = job.id;
  state.generationSelectionExplicit = true;
  processing.hidden = true;
  processing.classList.remove('cloud-active');
  video.pause();
  depthVideo.pause();
  video.hidden = true;
  depthVideo.hidden = true;
  $("videoEmpty").hidden = true;
  $("videoLabel").hidden = false;
  $("videoLabel").textContent = `复刻 ${job.id.replace(/^prepare-/, '').slice(0, 6)} · ${job.result_check && job.result_check.status !== 'passed' ? '候选片 · 替换核验未通过' : '生成结果'}`;
  $("resultTab").hidden = false;
  $("sourceTab").classList.remove("active");
  $("depthTab").classList.remove("active");
  $("resultTab").classList.add("active");
  if (resultVideo.getAttribute("src") !== src) resultVideo.src = src;
  resultVideo.hidden = false;
  if (switching) globalThis.WorkbenchMotion?.enter(resultVideo, 200);
  reportResultPlaybackAvailable(true);
  resultVideo.play().catch(() => {});
  renderQueue();
}
// Adapted from SHIYIN-AI's depth-video synchronized playback and Atlas split/wipe comparison.
const compareDialog = $("videoCompareDialog");
const compareSource = $("videoCompareSource");
const compareResult = $("videoCompareResult");
const compareSeek = $("videoCompareSeek");
const compareStage = $("videoCompareStage");
let compareSeeking = false;
let compareJob = null;
let compareVariant = 'original';
function setCompareVariant(variant) {
  if (!compareJob) return;
  const result = compareJob.result;
  compareVariant = variant === 'upscaled' && result.upscaled_video ? 'upscaled' : 'original';
  $("videoCompareVariants").hidden = !result.upscaled_video;
  for (const [id, value] of [['videoCompareOriginal', 'original'], ['videoCompareUpscaled', 'upscaled']]) {
    const button = $(id);
    button.classList.toggle('active', compareVariant === value);
    button.setAttribute('aria-pressed', String(compareVariant === value));
  }
  $("videoCompareResultLabel").textContent = compareVariant === 'upscaled' ? '生成视频 · 超分' : '生成视频 · 未超分';
  const src = resultUrl(compareJob, compareVariant === 'upscaled' ? result.upscaled_video : result.original_video || result.video);
  if (compareResult.getAttribute('src') === src) return;
  const position = compareResult.currentTime || 0;
  const resume = !compareResult.paused && Boolean(compareResult.getAttribute('src'));
  compareResult.pause();
  compareSource.pause();
  compareResult.onloadedmetadata = () => {
    compareResult.onloadedmetadata = null;
    compareResult.currentTime = Math.min(position, compareResult.duration || position);
    compareSource.currentTime = Math.min(compareResult.currentTime, compareSource.duration || compareResult.currentTime);
    syncCompareFrame();
    if (resume) {
      compareResult.play().catch(() => {});
      compareSource.play().catch(() => {});
    }
  };
  compareResult.src = src;
}
const compareView = {scale:1, x:0, y:0};
let compareDrag = null;
function applyCompareView() {
  for (const video of [compareSource, compareResult]) {
    const bounds = video.parentElement.getBoundingClientRect();
    video.style.transform = `translate(${compareView.x * bounds.width}px, ${compareView.y * bounds.height}px) scale(${compareView.scale})`;
  }
  compareStage.classList.toggle('is-zoomed', compareView.scale > 1);
}
function resetCompareView() {
  Object.assign(compareView, {scale:1, x:0, y:0});
  compareStage.classList.remove('is-panning');
  applyCompareView();
}
function compareTime(seconds) {
  const value = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}
function syncCompareFrame() {
  const duration = Number.isFinite(compareResult.duration) ? compareResult.duration : 0;
  if (!compareSeeking) compareSeek.value = duration ? Math.round(compareResult.currentTime / duration * 1000) : 0;
  $("videoCompareTime").textContent = `${compareTime(compareResult.currentTime)} / ${compareTime(duration)}`;
  if (compareSource.readyState && Math.abs(compareSource.currentTime - compareResult.currentTime) > 0.12) {
    compareSource.currentTime = Math.min(compareResult.currentTime, compareSource.duration || compareResult.currentTime);
  }
}
function setCompareMode(mode) {
  const wipe = mode === "wipe";
  $("videoCompareStage").classList.toggle("wipe", wipe);
  $("videoCompareWipe").hidden = !wipe;
  $("videoCompareDivider").hidden = !wipe;
  compareDialog.querySelectorAll("[data-compare-mode]").forEach(button => {
    button.classList.toggle("active", button.dataset.compareMode === mode);
    button.setAttribute("aria-pressed", String(button.dataset.compareMode === mode));
  });
  applyCompareView();
}
function applyCompareWipe() {
  const split = Number($("videoCompareWipe").value);
  $("videoCompareStage").style.setProperty("--compare-split", `${split}%`);
}
function closeVideoCompare() {
  compareDrag = null;
  resetCompareView();
  compareResult.pause();
  compareSource.pause();
  compareResult.onloadedmetadata = null;
  if (compareSource.getAttribute('src')?.startsWith('blob:')) URL.revokeObjectURL(compareSource.src);
  compareResult.removeAttribute("src");
  compareSource.removeAttribute("src");
  compareResult.load();
  compareSource.load();
  compareJob = null;
  if (compareDialog.open) compareDialog.close();
}
async function openVideoCompare(job) {
  const request = job.request || {};
  const mode = request.reference_mode || 'extracted_depth';
  if (!job.result?.video || (mode === 'extracted_depth' ? !request.depth_id : !request.source_id)) {
    toast("原参考视频或生成视频不可用，无法对比");
    return;
  }
  closeVideoCompare();
  compareJob = job;
  resultVideo.pause();
  if (isNativeMaterial(request.source_id)) {
    try {
      const source = await api(`/assets/${request.source_id}/clip?start=${request.start}&end=${request.end}`);
      if (compareJob !== job) { URL.revokeObjectURL(source.url); return; }
      compareSource.src = source.url;
    } catch (error) { toast(error.message); return; }
  } else compareSource.src = !request.reference_mode
    ? `${API}/api/clone/tasks/${encodeURIComponent(request.depth_id)}/source`
    : job.cloud
    ? mode === 'extracted_depth'
      ? `${API}/api/clone/tasks/${encodeURIComponent(request.depth_id)}/source`
      : `${API}/api/clone/assets/${encodeURIComponent(request.source_id)}/clip?start=${encodeURIComponent(request.start)}&end=${encodeURIComponent(request.end)}`
    : `${API}/api/clone/tasks/${encodeURIComponent(job.id)}/source`;
  compareSource.muted = true;
  setCompareVariant('upscaled');
  $("videoCompareTitle").textContent = `复刻 ${job.id.replace(/^prepare-/, '').slice(0, 6)} · ${job.request.source_name || "参考视频"}`;
  compareSeek.value = 0;
  $("videoCompareWipe").value = 50;
  applyCompareWipe();
  setCompareMode("split");
  $("videoComparePlay").textContent = "播放";
  $("videoComparePlay").setAttribute("aria-label", "播放");
  syncCompareFrame();
  compareDialog.showModal();
  resetCompareView();
  $("videoComparePlay").focus();
}
function toggleVideoCompare() {
  if (compareResult.paused) {
    compareSource.currentTime = Math.min(compareResult.currentTime, compareSource.duration || compareResult.currentTime);
    compareResult.play().catch(() => {});
    compareSource.play().catch(() => {});
  } else {
    compareResult.pause();
    compareSource.pause();
  }
}
compareDialog.querySelectorAll("[data-compare-mode]").forEach(button => button.addEventListener("click", () => setCompareMode(button.dataset.compareMode)));
$("videoCompareOriginal").addEventListener("click", () => setCompareVariant('original'));
$("videoCompareUpscaled").addEventListener("click", () => setCompareVariant('upscaled'));
$("videoCompareWipe").addEventListener("input", applyCompareWipe);
compareStage.addEventListener('wheel', event => {
  if (event.target.closest('button')) return;
  event.preventDefault();
  const pane = event.target.closest('.video-compare-pane') || compareStage.querySelector('.video-compare-pane.source');
  const bounds = pane.getBoundingClientRect();
  const px = (event.clientX - bounds.left - bounds.width / 2) / bounds.width;
  const py = (event.clientY - bounds.top - bounds.height / 2) / bounds.height;
  const nextScale = Math.max(1, Math.min(5, compareView.scale * (event.deltaY < 0 ? 1.15 : 1 / 1.15)));
  compareView.x = px - (px - compareView.x) * nextScale / compareView.scale;
  compareView.y = py - (py - compareView.y) * nextScale / compareView.scale;
  compareView.scale = nextScale;
  if (nextScale === 1) compareView.x = compareView.y = 0;
  applyCompareView();
}, {passive:false});
compareStage.addEventListener('pointerdown', event => {
  if (event.button !== 0 || event.target.closest('button')) return;
  const bounds = compareStage.getBoundingClientRect();
  const splitX = bounds.left + bounds.width * Number($("videoCompareWipe").value) / 100;
  const wipe = compareStage.classList.contains('wipe') && Math.abs(event.clientX - splitX) < 24;
  event.preventDefault();
  compareDrag = {kind:wipe ? 'wipe' : 'pan', startX:event.clientX, startY:event.clientY, x:compareView.x, y:compareView.y, width:bounds.width, height:bounds.height};
  compareStage.setPointerCapture(event.pointerId);
  if (!wipe) compareStage.classList.add('is-panning');
});
compareStage.addEventListener('pointermove', event => {
  if (!compareDrag) return;
  if (compareDrag.kind === 'wipe') {
    const bounds = compareStage.getBoundingClientRect();
    $("videoCompareWipe").value = Math.max(0, Math.min(100, Math.round((event.clientX - bounds.left) / bounds.width * 100)));
    applyCompareWipe();
  } else {
    const paneWidth = compareStage.classList.contains('wipe') ? compareDrag.width : compareDrag.width / 2;
    compareView.x = compareDrag.x + (event.clientX - compareDrag.startX) / paneWidth;
    compareView.y = compareDrag.y + (event.clientY - compareDrag.startY) / compareDrag.height;
    applyCompareView();
  }
});
function stopCompareDrag() { compareDrag = null; compareStage.classList.remove('is-panning'); }
compareStage.addEventListener('pointerup', stopCompareDrag);
compareStage.addEventListener('pointercancel', stopCompareDrag);
$("videoComparePlay").addEventListener("click", toggleVideoCompare);
$("exportVideoCompare").addEventListener("click", async () => {
  if (!compareJob) return;
  const button = $("exportVideoCompare");
  button.disabled = true;
  button.textContent = "正在导出…";
  try {
    const result = await requestCloneCompareExport(compareJob, compareVariant);
    if (result?.saved) toast("对比视频已保存：" + result.path);
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = "导出对比视频"; }
});
$("closeVideoCompare").addEventListener("click", closeVideoCompare);
compareDialog.addEventListener("close", () => { if (!compareDialog.open) closeVideoCompare(); });
compareDialog.addEventListener("click", event => { if (event.target === compareDialog) closeVideoCompare(); });
compareDialog.addEventListener("keydown", event => {
  if (event.code !== "Space" || event.repeat || event.target.matches("input[type=text], textarea, select") || event.target.isContentEditable) return;
  event.preventDefault();
  event.stopPropagation();
  toggleVideoCompare();
}, true);
compareSeek.addEventListener("pointerdown", () => { compareSeeking = true; });
compareSeek.addEventListener("input", () => {
  const duration = compareResult.duration || compareSource.duration || 0;
  const target = duration * Number(compareSeek.value) / 1000;
  compareResult.currentTime = target;
  compareSource.currentTime = Math.min(target, compareSource.duration || target);
  syncCompareFrame();
});
compareSeek.addEventListener("change", () => { compareSeeking = false; syncCompareFrame(); });
compareResult.addEventListener("timeupdate", syncCompareFrame);
compareResult.addEventListener("durationchange", syncCompareFrame);
compareResult.addEventListener("play", () => { $("videoComparePlay").textContent = "暂停"; $("videoComparePlay").setAttribute("aria-label", "暂停"); });
compareResult.addEventListener("pause", () => { compareSource.pause(); $("videoComparePlay").textContent = "播放"; $("videoComparePlay").setAttribute("aria-label", "播放"); });
compareResult.addEventListener("ended", () => compareSource.pause());
compareSource.addEventListener("error", () => toast("原参考视频无法加载，请检查任务素材"));
compareResult.addEventListener("error", () => toast("生成视频无法加载，请检查任务文件"));
$("resultTab").onclick = () => {
  const job = [...state.jobs, ...cloudJobs].find((item) => item.id === state.previewJobId);
  if (job?.result?.video) showResultInPreview(job);
};
let pollStarted = false;
function payload() {
  if (state.materialWorkflow) return materialRequest();
  const request = {
    ...referenceFields(),
    generation_goal: referenceMode() === 'source_video' ? 'reference' : 'replace',
    control_strength: Number($('controlStrength').value || 1),
    analysis_id: state.analysisId,
    people: state.people.map((p, i) => ({
      track_id: p.trackId || i + 1,
      position: positionEn[p.position],
      images: p.images.map((x) => x.id),
    })),
    resolution: $("resolution").value,
    upscale: generationEngine === "local" && $("upscale").checked,
    steps: Number($("steps").value),
    seed: $("seed").value.trim(),
    source_name: state.name,
  };
  if (isLightweightClone()) request.analysis_id = null;
  if (isCloudEngine()) {
    request.cloud_resolution = $('cloudResolution').value;
    request.cloud_ratio = $('cloudRatio').value;
    request.output_duration = Number($('cloudDuration').value);
    request.reference_type = usesDepthReference() ? 'depth' : 'source';
    if (request.reference_type === 'depth') {
      request.background_image_id = state.backgroundImage?.id || null;
      request.scene_text = $('sceneText').value.trim();
    }
  }
  const text = referenceMode() === 'region_video' ? '' : $("supplement").value.trim();
  if (text) {
    const [kind, subjectIndex, trackId] = $("supplementTarget").value.split(":");
    request.supplement = {
      text,
      target: kind === "subject" ? { kind, subject_index: Number(subjectIndex), track_id: Number(trackId) } : { kind: "all" },
    };
  }
  return request;
}
let submittingGeneration = false;
$("generate").onclick = async () => {
  if ($("generate").disabled || submittingGeneration) return;
  if (isCloudEngine() && !$('cloudDuration').reportValidity()) return;
  if (!isCloudEngine() && !$('steps').reportValidity()) return;
  if (!isCloudEngine() && usesDepthReference() && !$('controlStrength').reportValidity()) return;
  submittingGeneration = true;
  const sourceToken = state.sourceToken;
  const editingJobId = state.editingJobId;
  const engine = generationEngine;
  const materialInput = state.materialWorkflow;
  let p, submittedDraft;
  try {
    p = payload();
    submittedDraft = JSON.stringify(p);
    const service = nativeMaterials && isCloudEngine()
      ? {allowed:true,apiBase:API} : await requestGenerationService(API, useWorkbenchServiceCheck);
    if (!service.allowed) return;
    API = service.apiBase;
    if (materialInput) p.prepare_prompt = true;
    let task;
    if (engine === 'local') task = await post(materialInput ? '/tasks/generate-prepared' : '/tasks/generate', p);
    else {
      p.manual_prompt = materialInput;
      p.auto_prompt = !materialInput;
      p.duration = p.end - p.start;
      task = await submitCloudClone(p);
    }
    if(engine==='local')window.parent.postMessage({type:'node-task-submitted',kind:'clone',task,apiBase:API},'*');
    state.selectedJobId = task.id;
    state.generationSelectionExplicit = true;
    const submittedJob = generationJobs().find(job => job.id === task.id) || task;
    if (['queued', 'running'].includes(submittedJob.status)) showRunningJob(submittedJob);
    toast(editingJobId ? "重新生成任务已提交" : "复刻任务已提交，可继续添加任务");
    if (sourceToken === state.sourceToken && editingJobId === state.editingJobId)
      state.editingJobId = null;
    // 等待期间修改了文字、区间或素材的草稿属于下一次，不能被旧响应清空。
    if (!state.materialWorkflow && sourceToken === state.sourceToken && JSON.stringify(payload()) === submittedDraft) {
      $("supplement").value = "";
      $("supplementTarget").value = "all";
    }
    update();
    poll().catch(error => toast(error.message));
  } catch (e) {
    toast(e.message);
  } finally {
    submittingGeneration = false;
    update();
  }
};
let queueLayoutSignature = "";
const queueCards = new Map();
function generationElapsedLabel(job) {
  if (job.status === 'queued') return '';
  const timestamp = value => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, number) : (Date.parse(value) / 1000 || 0);
  };
  const active = job.status === 'running';
  const started = timestamp(job.started_at || job.running_at || job.created_at);
  const ended = active ? Date.now() / 1000
    : timestamp(job.completed_at || job.finished_at || (!job.cloud && job.updated_at));
  const recorded = job.elapsed_seconds != null && job.elapsed_seconds !== '' ? Number(job.elapsed_seconds) : NaN;
  const seconds = Number.isFinite(recorded) && recorded >= 0 ? recorded
    : started > 0 && ended >= started ? ended - started : NaN;
  if (!Number.isFinite(seconds)) return active ? '' : '耗时 --';
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total % 3600 / 60);
  return `${active ? '已用' : '耗时'} ${hours ? `${hours} 时 ` : ''}${hours || minutes ? `${minutes} 分 ` : ''}${total % 60} 秒`;
}
function updateQueueElapsed(job, entry) {
  if (!entry) return;
  const label = generationElapsedLabel(job);
  entry.elapsed.hidden = !label;
  if (entry.elapsed.textContent !== label) entry.elapsed.textContent = label;
}
function generationPhaseLabel(job) {
  if (job.status === 'queued') return '任务排队中';
  if (job.phase === 'prompt') return '准备提示词';
  if (job.phase === 'submitting') return '提交生成';
  if (job.phase === 'verify') return '替换效果核验';
  if (job.phase === 'h3' && job.sampling_timing?.stage === 'decode') return '正在解码音画';
  if (job.phase === 'h3' && job.sampling_timing?.stage === 'save') return '正在合成保存';
  return job.phase === 'alignment' ? '镜头时间校正' : job.phase === "preparing" ? "模型自动补齐" : job.phase === "upscale" ? "生成视频超分" : job.phase === "appearance" ? "人物参考图解析" : "视频生成";
}
function showFailedJob(job) {
  state.selectedJobId=job.id;state.generationSelectionExplicit=true;
  sourceView();video.pause();
  progress('生成失败',cloudErrorText(job.error || job.message || '生成失败，平台未返回具体原因'),0);
  processing.dataset.taskId=job.id;
  processing.classList.add('is-failed');processing.classList.remove('cloud-active');
  processing.hidden=false;renderQueue();
}
$('copyFailure').onclick=async()=>{
  try{await navigator.clipboard.writeText($('progressDetail').textContent);$('copyFailure').textContent='已复制';}
  catch{$('copyFailure').textContent='请手动选择文字复制';}
};
function showRunningJob(job) {
  state.selectedJobId = job.id;
  state.generationSelectionExplicit = true;
  sourceView();
  progress(
    generationPhaseLabel(job),
    taskDetail(job),
    job.percent,
    job,
  );
  processing.hidden = false;
  processing.classList.toggle('cloud-active', Boolean(job.cloud));
  renderQueue();
}
setInterval(() => {
  if (!document.hidden) {
    for (const job of generationJobs()) updateQueueElapsed(job, queueCards.get(job.id));
  }
  if (!processing.hidden) globalThis.GenerationEta.render($('generationEta'), progressEtaJob);
  if (processing.hidden || !processing.classList.contains('cloud-active')) return;
  const selected = cloudJobs.find(job => job.id === state.selectedJobId);
  if (selected && ['queued', 'running'].includes(selected.status)) $('progressDetail').textContent = taskDetail(selected);
}, 1000);
function generationJobs() {
  const created = (job) => Number(job.created_at) || (job.cloud ? Number.MAX_SAFE_INTEGER : 0);
  return [...state.jobs.filter((job) => job.kind === "generate"), ...cloudJobs]
    .map(job => {
      const upscale = upscaleStates.get(job.id);
      const video = job.result?.upscaled_video || (upscale?.status === 'done' ? (job.cloud ? upscale.url : upscale.output) : '');
      return {...job, upscaleStatus:upscale?.status || '', upscaleError:upscale?.error || '',
        result:job.result && video ? {...job.result,upscaled_video:video} : job.result};
    })
    .sort((left, right) => created(right) - created(left));
}
function renderQueue() {
  const jobs = generationJobs();
  $("queueCount").textContent = jobs.length;
  $("mobileQueueCount").textContent = jobs.length;
  const signature = JSON.stringify(jobs.map((job) => [
    job.id, job.status, job.request, job.result?.video, job.result?.upscaled_video,
    job.upscaleStatus, job.upscaleError, job.supplement_plan,
  ]));
  if (signature === queueLayoutSignature) {
    for (const job of jobs) {
      const entry = queueCards.get(job.id);
      entry?.card.classList.toggle("selected", state.selectedJobId === job.id);
      if (entry?.progressBar) entry.progressBar.value = Number(job.percent) || 0;
      updateQueueElapsed(job, entry);
    }
    return;
  }
  queueLayoutSignature = signature;
  queueCards.clear();
  if (!jobs.length) {
    $("queue").innerHTML =
      '<div class="queueempty"><strong>你的下一支作品<br>从这里诞生</strong><p>素材与参数随每个任务独立保存</p></div>';
    return;
  }
  const names = {
    queued: "等待中",
    running: "处理中",
    done: "完成",
    error: "失败",
    canceled: "已取消",
  };
  const queue = $("queue");
  queue.replaceChildren();
  for (const job of jobs) {
    const completed = job.status === "done" && Boolean(job.result?.video);
    const card = document.createElement("article");
    card.className = `job${completed ? " can-preview" : ""}${state.selectedJobId === job.id ? " selected" : ""}`;
    if (completed) {
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `播放复刻 ${job.id.replace(/^prepare-/, '').slice(0, 6)}，双击打开视频对比`);
      card.addEventListener("click", (event) => {
        if (!event.target.closest("button")) showResultInPreview(job);
      });
      card.addEventListener("dblclick", (event) => {
        if (!event.target.closest("button")) openVideoCompare(job);
      });
      card.addEventListener("keydown", (event) => {
        if (event.target === card && ["Enter", " "].includes(event.key)) {
          event.preventDefault();
          showResultInPreview(job);
        }
      });
    } else if (['failed','error'].includes(job.status)) {
      card.classList.add('can-preview');
      card.tabIndex = 0;
      card.setAttribute('role','button');
      card.setAttribute('aria-label',`查看复刻 ${job.id.replace(/^prepare-/, '').slice(0, 6)} 的失败原因`);
      const showFailure = () => showFailedJob(generationJobs().find(item=>item.id===job.id)||job);
      card.addEventListener('click',event=>{if (!event.target.closest('button')) showFailure();});
      card.addEventListener('keydown',event=>{
        if (event.target === card && ['Enter',' '].includes(event.key)) {event.preventDefault();showFailure();}
      });
    } else if (["queued", "running"].includes(job.status)) {
      card.classList.add("can-preview");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `查看复刻 ${job.id.replace(/^prepare-/, '').slice(0, 6)} 的处理进度`);
      card.addEventListener("click", (event) => {
        if (!event.target.closest("button")) showRunningJob(generationJobs().find((item) => item.id === job.id) || job);
      });
      card.addEventListener("keydown", (event) => {
        if (event.target === card && ["Enter", " "].includes(event.key)) {
          event.preventDefault();
          showRunningJob(generationJobs().find((item) => item.id === job.id) || job);
        }
      });
    }
    const thumbnail = document.createElement("div");
    thumbnail.className = "job-thumbnail";
    if (completed) {
      const preview = document.createElement("video");
      preview.src = resultUrl(job, job.result.video);
      preview.muted = true;
      preview.preload = "metadata";
      preview.playsInline = true;
      thumbnail.append(preview);
    } else thumbnail.textContent = names[job.status] || "任务";
    const copy = document.createElement("div");
    copy.className = "jobcopy";
    const title = document.createElement("strong");
    title.className = "job-title";
    title.textContent = `复刻 ${job.id.replace(/^prepare-/, '').slice(0, 6)}`;
    const request = job.request || {};
    const meta = document.createElement("div");
    meta.className = "job-meta";
    const details = job.cloud
      ? `优云智算 H3 · ${job.resolution || "未知分辨率"} · ${job.duration || "?"}秒`
      : request.input_mode === 'materials' ? `${request.resolution || '0.2MP'} · ${(request.reference_images || []).length} 张图`
      : `${request.resolution || "0.2MP"} · ${(request.people || []).length}人`;
    const capability = request.reference_mode === 'prompt_video' ? '纯提示词轻量克隆'
      : request.reference_mode === 'region_video' ? '演员区域替换'
      : request.reference_mode === 'source_video' ? '自由参考 · 人物可能残留'
      : job.cloud ? '云端结构参考' : '深度人物替换';
    meta.textContent = `${names[job.status] || "任务"} · ${details} · ${capability}`;
    const elapsed = document.createElement('div');
    elapsed.className = 'job-elapsed';
    copy.append(title, meta, elapsed);
    let variants = null;
    if (completed) {
      variants = document.createElement('div');
      variants.className = 'job-variants';
      const original = document.createElement('button');
      original.type = 'button'; original.textContent = '原片';
      original.addEventListener('click', event => { event.stopPropagation(); showResultInPreview(job, job.result.original_video || job.result.video); });
      variants.append(original);
      if (job.result.upscaled_video) {
        const upscaled = document.createElement('button');
        upscaled.type = 'button'; upscaled.textContent = '2× 超分';
        upscaled.addEventListener('click', event => { event.stopPropagation(); showResultInPreview(job, job.result.upscaled_video); });
        variants.append(upscaled);
      } else if (job.upscaleStatus === 'running' || (job.cloud && request.upscale && !job.upscaleStatus)) {
        const pending = document.createElement('span'); pending.textContent = '超分生成中…'; variants.append(pending);
      } else {
        const create = document.createElement('button');
        create.type = 'button'; create.textContent = job.upscaleStatus === 'failed' ? '重试 2× 超分' : '生成 2× 超分';
        create.addEventListener('click', async event => {
          event.stopPropagation();
          create.disabled = true;
          try { await requestCloneUpscale(job); toast('已提交超分处理'); }
          catch (error) { toast(error.message); create.disabled = false; }
        });
        variants.append(create);
      }
    }
    let progressBar = null;
    if (["queued", "running"].includes(job.status)) {
      progressBar = document.createElement("progress");
      progressBar.max = 100;
      progressBar.value = Number(job.percent) || 0;
      copy.append(progressBar);
    }
    const action = document.createElement("button");
    action.className = "job-menu-trigger";
    action.type = "button";
    action.textContent = "⋯";
    action.title = "任务菜单";
    action.setAttribute("aria-label", `打开复刻 ${job.id.replace(/^prepare-/, '').slice(0, 6)} 的菜单`);
    action.setAttribute("aria-haspopup", "menu");
    action.setAttribute("aria-expanded", "false");
    action.addEventListener("click", (event) => {
      event.stopPropagation();
      openTaskMenu(job, action);
    });
    card.append(thumbnail, copy, action);
    if (variants) card.append(variants);
    queue.append(card);
    const entry = { card, progressBar, elapsed };
    queueCards.set(job.id, entry);
    updateQueueElapsed(job, entry);
  }
}
function closeTaskMenu() {
  const layer = $("taskMenuLayer");
  layer.hidden = true;
  layer.replaceChildren();
  document.querySelectorAll(".job-menu-trigger[aria-expanded='true']").forEach((button) => button.setAttribute("aria-expanded", "false"));
}
let restoreSequence = 0;
function materialAssetUrl(asset) {
  return asset.url?.startsWith('blob:') ? asset.url : API + (asset.url || `/api/clone/assets/${asset.id}/media`);
}
async function restoreGeneration(job) {
  if (state.busy || state.pending) throw Error("请等待当前深度处理完成后再回填任务");
  const sequence = ++restoreSequence;
  const sourceToken = state.sourceToken;
  const request = job.request || {};
  const mode = request.reference_mode || 'extracted_depth';
  const depth = mode === 'extracted_depth' ? await api(`/tasks/${request.depth_id}`) : null;
  const region = mode === 'region_video' ? await api(`/tasks/${request.region_id}`) : null;
  if (region && (region.kind !== 'region' || region.status !== 'done')) throw Error('原任务演员遮罩不可用，请重新准备');
  if (depth && (depth.kind !== "depth" || depth.status !== "done" || !depth.request?.source_id || !depth.result?.duration))
    throw Error("原任务的深度视频不可用，无法回填");
  const sourceId = depth?.request?.source_id || request.source_id;
  if (!sourceId) throw Error("原任务的参考视频不可用，无法回填");
  const source = await api(`/assets/${sourceId}`);
  const people = await Promise.all((request.people || []).map(async (person, index) => ({
    id: index + 1,
    trackId: person.track_id || index + 1,
    position: { left: "左侧", center: "中间", right: "右侧" }[person.position] || "中间",
    images: await Promise.all((person.images || []).map(async (id) => {
      const asset = await api(`/assets/${id}`);
      return { id, name: asset.name || "人物参考图", url: materialAssetUrl(asset) };
    })),
  })));
  const materialImages = request.input_mode === 'materials'
    ? await Promise.all((request.reference_images || []).map(async id => {
      const asset = await api(`/assets/${id}`);
      return {id,name:asset.name || '参考素材',url:materialAssetUrl(asset)};
    })) : people.flatMap(person => person.images);
  const background = request.background_image_id
    ? await api(`/assets/${request.background_image_id}`) : null;
  if (sequence !== restoreSequence || sourceToken !== state.sourceToken) return;
  const start = Number(depth?.request?.start ?? request.start) || 0;
  const end = depth ? start + Number(depth.result.duration) : Number(request.end);
  loadVideo(materialAssetUrl(source), source.name || request.source_name || "参考视频", false, { start, end });
  state.sourceId = source.id;
  state.depthId = depth?.id || null;
  state.analysisId = request.analysis_id || null;
  state.depth = Boolean(depth);
  state.analyzed = Boolean(request.analysis_id);
  state.mode = request.analysis_id || people.length > 1 ? "multi" : "single";
  state.people = people;
  if (state.materialWorkflow) state.materials = materialImages.map(image => ({...image,key:crypto.randomUUID()}));
  if (region) {
    state.regionId = region.id;
    state.regions = region.request.regions;
    state.regionStats = region.result;
    $('regionConfirm').checked = request.region_confirmed === true;
    $('regionConfirm').disabled = Boolean(region.result.missing_frames);
    $('regionPreview').src = API + `/api/clone/tasks/${region.id}/region-preview`;
    $('regionPreview').hidden = false;
    $('regionReviewHint').textContent = region.result.missing_frames
      ? `原遮罩有 ${region.result.missing_frames} 帧漏检，请修正后重新准备。`
      : '已复用原任务确认的遮罩；修改区域或视频区间需重新准备。';
    renderRegions();
  }
  if (background) {
    state.backgroundImage = {id:background.id,name:background.name,url:background.url};
    $('sceneText').value = request.scene_text || '';
    renderCloudBackground();
    persistCloudDraft();
  }
  state.editingJobId = job.id;
  $('referenceMode').value = state.materialWorkflow ? 'prompt_video' : mode;
  referenceModeExplicit = true;
  $("depthMode").value = depth?.request?.depth_mode || request.depth_mode || 'professional';
  state.depthModel = depth?.request?.depth_model || "small";
  $("personMask").checked = Boolean(depth?.request?.remove_background);
  state.desiredResolution = request.requested_resolution || request.resolution || $("resolution").value;
  if (Array.from($("resolution").options).some((option) => option.value === state.desiredResolution))
    $("resolution").value = state.desiredResolution;
  if (job.cloud) {
    $('cloudResolution').value = request.cloud_resolution || job.resolution || '480P';
    $('cloudRatio').value = request.cloud_ratio || job.ratio || '16:9';
    $('cloudDuration').value = request.output_duration || job.duration || 5;
  }
  $("upscale").checked = Boolean(request.upscale);
  $("steps").value = request.steps ?? 12;
  $('controlStrength').value = request.control_strength ?? 1;
  $("seed").value = request.seed ?? "";
  $("supplement").value = state.materialWorkflow ? (request.user_prompt || request.supplement?.text || request.prompt || '') : request.supplement?.text || "";
  $("depthHint").textContent = mode === 'extracted_depth'
    ? "已复用原任务深度视频；修改区间或深度模式需重新提取"
    : mode === 'depth_video' ? '已复用原任务的深度视频直用模式' : '已复用原任务的原视频参考模式';
  $("pipeDepth").classList.add("done");
  $("pipeVlm").classList.toggle("done", state.analyzed);
  renderPeople();
  update();
  if (request.supplement?.target?.kind === "subject") {
    const target = request.supplement.target;
    $("supplementTarget").value = `subject:${target.subject_index}:${target.track_id}`;
  }
  toast("原任务已回填；调整参数、生成需求或参考图后点击「重新生成」");
}
function openTaskMenu(job, trigger) {
  const wasOpen = trigger.getAttribute("aria-expanded") === "true";
  closeTaskMenu();
  if (wasOpen) return;
  const menu = document.createElement("div");
  menu.className = "task-menu";
  menu.setAttribute("role", "menu");
  const actions = [
    { label: "查看参数", run: () => { $("taskSnapshot").textContent = JSON.stringify(job, null, 2); $("taskDialog").showModal(); } },
    ...(job.result_check?.candidate_video && job.result_check.status !== 'passed'
      ? [{label:'查看未通过候选片',run:() => showResultInPreview(job,job.result_check.candidate_video)}] : []),
    { label: "下载原片", disabled: !job.result?.video, run: () => { const link = document.createElement("a"); link.href = resultUrl(job, job.result.original_video || job.result.video); link.download = ""; link.click(); } },
    job.result?.upscaled_video
      ? { label: "下载超分视频", run: () => { const link = document.createElement("a"); link.href = resultUrl(job, job.result.upscaled_video); link.download = ""; link.click(); } }
      : { label: job.upscaleStatus === 'running' ? "超分生成中…" : "生成超分视频",
          disabled: job.status !== 'done' || !job.result?.video || job.upscaleStatus === 'running',
          run: async () => { await requestCloneUpscale(job); toast('已提交超分处理'); } },
    ["queued", "running"].includes(job.status)
      ? { label: "取消任务", danger: true, run: async () => { if (job.cloud) window.parent.postMessage({type:'clone-cloud-cancel',taskId:job.id},'*'); else await api("/tasks/" + job.id, { method: "DELETE" }); await poll(); } }
      : { label: "重新生成", run: () => restoreGeneration(job) },
    { label: "对比视频", disabled: !job.result?.video || (!(job.request?.depth_id || job.request?.source_id)), run: () => openVideoCompare(job) },
  ];
  for (const item of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = item.label;
    button.disabled = Boolean(item.disabled);
    button.classList.toggle("danger", Boolean(item.danger));
    button.setAttribute("role", "menuitem");
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (button.disabled) return;
      button.disabled = true;
      try { await item.run(); } catch (error) { toast(error.message); } finally { closeTaskMenu(); }
    });
    menu.append(button);
  }
  const layer = $("taskMenuLayer");
  layer.append(menu);
  layer.hidden = false;
  trigger.setAttribute("aria-expanded", "true");
  const rect = trigger.getBoundingClientRect();
  const menuRect = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, rect.right - menuRect.width)}px`;
  menu.style.top = `${Math.min(window.innerHeight - menuRect.height - 8, rect.bottom + 5)}px`;
}
$("taskMenuLayer").onclick = (event) => { if (event.target === event.currentTarget) closeTaskMenu(); };
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeTaskMenu(); });
document.addEventListener("keydown", (event) => {
  const target = event.target;
  const editing = target instanceof HTMLElement && (target.matches("input, textarea, select") || target.isContentEditable);
  if (event.code !== "Space" || event.repeat || editing || compareDialog.open || resultVideo.hidden || !state.previewJobId) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  toggleResultPlayback();
}, true);
let polling = false;
async function poll() {
  if (polling) return;
  const epoch = state.epoch;
  polling = true;
  try {
    const response = await api(`/tasks?refresh=${Date.now()}`);
    state.pollFailures = 0;
    state.jobs = response.items;
    state.paused = response.paused;
    $("pauseQueue").textContent = state.paused ? "恢复队列" : "暂停队列";
    if (!state.generationSelectionExplicit) {
      const runningJob = state.jobs.find((job) => job.kind === "generate" && job.status === "running");
      if (runningJob) state.selectedJobId = runningJob.id;
    }
    renderQueue();
    if (epoch !== state.epoch) return;
    let pending = state.pending;
    if (!pending) {
      const activeTask = state.jobs.find(
        (job) =>
          ["depth", "analyze"].includes(job.kind) &&
          ["queued", "running"].includes(job.status) &&
          ((job.kind === "depth" && !state.depth && job.request?.source_id === state.sourceId) ||
            (job.kind === "analyze" && !state.analyzed && (
              job.request?.depth_id === state.depthId ||
              (job.request?.source_id === state.sourceId && job.request?.reference_mode === referenceMode())
            ))),
      );
      if (activeTask) {
        pending = { id: activeTask.id, kind: activeTask.kind };
        state.pending = pending;
        state.busy = true;
      }
    }
    let task = pending && state.jobs.find((t) => t.id === pending.id);
    if (pending) {
      try {
        task = await api(`/tasks/${pending.id}?refresh=${Date.now()}`);
      } catch {
        // The list snapshot remains usable while a task detail request retries.
      }
    }
    if (epoch !== state.epoch) return;
    if (task) {
      progress(
        task.kind === "depth" ? "深度视频提取" : task.kind === 'region' ? '演员遮罩准备' : "Qwen 人物解析",
        taskDetail(task),
        task.percent,
        task,
      );
      processing.hidden = false;
      if (["done", "error", "canceled"].includes(task.status)) {
        state.pending = null;
        state.busy = false;
        if (task.status === "done") {
          if (task.kind === 'region') {
            state.regionId = task.id;
            state.regionStats = task.result;
            $('regionPreview').src = API + `/api/clone/tasks/${task.id}/region-preview`;
            $('regionPreview').hidden = false;
            $('regionConfirm').disabled = Boolean(task.result.missing_frames);
            const missingTimes = (task.result.stats || []).flatMap(stat=>stat.missing_times||[]).slice(0,8).map(t=>`${t.toFixed(3)}s`).join('、');
            $('regionReviewHint').textContent = task.result.missing_frames
              ? `有 ${task.result.missing_frames} 帧漏检${missingTimes ? `（${missingTimes}）` : ''}，当前不能生成。检查演员是否被完全遮挡；调整时间段或选区，或使用局部精细轮廓。`
              : '遮罩已准备；绿色表示演员范围。请播放完整预览检查身体、遮挡与旁人，确认后才能生成。';
          } else if (task.kind === "depth") {
            state.depthId = task.id;
            state.depth = true;
            state.end = state.start + task.result.duration;
            $("depthHint").textContent = task.request?.adjusted_from
              ? "调参后深度视频已应用，后续生成将使用此版本"
              : "深度视频已生成，可切换到深度视频预览";
            $("pipeDepth").classList.add("done");
            if ($("depthTab").classList.contains("active")) $("depthTab").click();
          } else {
            state.analysisId = task.id;
            state.analyzed = true;
            state.people = task.result.people.map((p, i) => ({
              id: i + 1,
              trackId: p.id,
              position: { left: "左侧", center: "中间", right: "右侧" }[
                p.position
              ],
              images: [],
            }));
            renderPeople();
            $("pipeVlm").classList.add("done");
            toast(
              "已解析 " + state.people.length + " 位人物，请核对人数和初始位置",
            );
          }
        } else toast(task.message);
        update();
        processing.hidden = true;
      }
    } else if (pending && !task) {
      // The service no longer knows this task, so leave the editor usable.
      state.pending = null;
      state.busy = false;
      processing.hidden = true;
      update();
    } else if (!state.busy) {
      const job = state.jobs.find(
        (j) => j.id === state.selectedJobId && j.kind === "generate" && ["queued", "running"].includes(j.status),
      );
      const selectedCloud = cloudJobs.find(j => j.id === state.selectedJobId && ['queued', 'running'].includes(j.status));
      const failed=generationJobs().find(j=>j.id===state.selectedJobId&&['failed','error'].includes(j.status));
      if(failed && processing.dataset.taskId===failed.id){showFailedJob(failed);}
      else if (selectedCloud && processing.classList.contains('cloud-active')) {
        progress(generationPhaseLabel(selectedCloud), taskDetail(selectedCloud), selectedCloud.percent, selectedCloud);
        processing.hidden = false;
      } else if (job) {
        progress(
          generationPhaseLabel(job),
          taskDetail(job), job.percent,
          job,
        );
        processing.hidden = false;
      } else {
        processing.hidden = true;
      }
    }
  } catch (e) {
    state.pollFailures += 1;
    if (state.pollFailures >= 3 && (state.pending || state.busy)) {
      state.pending = null;
      state.busy = false;
      processing.hidden = true;
      update();
      toast("本地服务连接已断开，任务状态恢复后会自动刷新。");
    }
  } finally {
    polling = false;
  }
}
async function pollingLoop() {
  await poll();
  setTimeout(pollingLoop, 1000);
}
$("pauseQueue").onclick = async () => {
  try {
    await post("/queue", { paused: !state.paused });
    await poll();
    toast(state.paused ? "后续任务已暂停，正在执行的任务会完成" : "队列已恢复");
  } catch (e) {
    toast(e.message);
  }
};
let cleaningQueue = false;
$("cleanupQueue").onclick = async () => {
  if (cleaningQueue) return;
  cleaningQueue = true;
  $("cleanupQueue").disabled = true;
  try {
    const result = await post("/tasks/cleanup", {});
    await poll();
    toast(result.deferred
      ? `已清理 ${result.cleaned} 条异常任务；${result.deferred} 条运行中的取消任务将在停止后移除`
      : result.cleaned ? `已清理 ${result.cleaned} 条异常生成任务` : "没有可清理的异常任务");
  } catch (e) {
    toast(e.message);
  } finally {
    cleaningQueue = false;
    $("cleanupQueue").disabled = false;
  }
};
function openSettings() {
  $("defaultMode").value = $("depthMode").value;
  $("depthModelSetting").value = state.depthModel;
  updateDepthModelStatus();
  $("srSetting").value = $("upscale").checked ? "2" : "0";
  $("settingsDialog").showModal();
}
function updateDepthModelStatus() {
  const selected = state.depthModels.find((model) => model.id === $("depthModelSetting").value);
  $("depthModelStatus").textContent = `${selected?.installed ? "所选模型已安装" : "所选模型将在首次提取时从魔搭自动下载"}。缺失运行时也会自动补齐，下载进度显示在提取任务中。`;
}
$("depthModelSetting").onchange = updateDepthModelStatus;
$("settingsOpen").onclick = openSettings;
$("saveSettings").onclick = async () => {
  try {
    const data = {
      reference_mode: referenceMode(),
      depth_mode: $("defaultMode").value,
      depth_model: $("depthModelSetting").value,
      resolution: $("resolution").value,
      upscale: $("srSetting").value === "2",
      steps: Number($("steps").value),
      seed: $("seed").value.trim(),
    };
    await post("/settings", data);
    if ($("depthMode").value !== data.depth_mode || state.depthModel !== data.depth_model) {
      $("depthMode").value = data.depth_mode;
      state.depthModel = data.depth_model;
      if (state.duration) invalidate();
    }
    $("upscale").checked = data.upscale;
    $("settingsDialog").close();
    update();
    toast("设置已保存");
  } catch (e) {
    toast(e.message);
  }
};
$("closeTask").onclick = () => $("taskDialog").close();
$("resolution").onchange = () => {
  state.desiredResolution = $("resolution").value;
  $("aspectHint").textContent = `当前画幅 ${presetAspect(state.desiredResolution)?.label || ''}`;
  persistGenerationSettings();
  update();
};
for (const id of ['cloudResolution', 'cloudRatio', 'cloudDuration']) $(id).onchange = update;
let parametersAnimation = null;
function setParametersExpanded(expanded) {
  const panel = $('generationParameters');
  const toggle = $('parametersToggle');
  if ((toggle.getAttribute('aria-expanded') === 'true') === expanded) return;
  const reducedMotion = globalThis.WorkbenchMotion?.reduced() || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const animate = typeof panel.animate === 'function' && !reducedMotion;
  const collapsed = {opacity:0, transform:'translateY(-8px) scale(.98)'};
  const current = animate && !panel.hidden
    ? {opacity:getComputedStyle(panel).opacity, transform:getComputedStyle(panel).transform}
    : collapsed;
  parametersAnimation?.cancel();
  parametersAnimation = null;
  toggle.setAttribute('aria-expanded', String(expanded));
  panel.inert = !expanded;
  if (!expanded && panel.contains?.(document.activeElement)) toggle.focus({preventScroll:true});
  if (!animate) { panel.hidden = !expanded; return; }
  panel.hidden = false;
  const animation = panel.animate([current, expanded ? {opacity:1, transform:'none'} : collapsed], {
    duration:expanded ? 200 : 240, easing:'cubic-bezier(.22,1,.36,1)', fill:'both',
  });
  parametersAnimation = animation;
  animation.onfinish = () => {
    if (parametersAnimation !== animation) return;
    panel.hidden = !expanded;
    parametersAnimation = null;
    animation.cancel();
  };
}
$('parametersToggle').addEventListener('click', () => {
  setParametersExpanded($('parametersToggle').getAttribute('aria-expanded') !== 'true');
});
document.addEventListener('pointerdown', event => {
  if ($('generationParameters').contains(event.target) || $('parametersToggle').contains(event.target)) return;
  setParametersExpanded(false);
}, true);
(async () => {
  try {
    const config = await api("/config");
    const previousResolution = $("resolution").value;
    $("resolution").replaceChildren(
      ...config.resolutions.map((value) => new Option(value.replace(" - ", " · ").replace("x", "×"), value)),
    );
    $("depthMode").value = config.settings.depth_mode;
    // 新草稿默认纯提示词轻量克隆；编辑历史任务或本页明确选择后沿用原模式。
    if (!referenceModeExplicit) $('referenceMode').value = 'prompt_video';
    state.depthModel = config.settings.depth_model || "small";
    state.depthModels = config.depth_models || [];
    $("depthHint").textContent = `${state.depthModel === "base" ? "Base" : "Small"} · 完整场景深度`;
    const desired = state.desiredResolution || config.settings.resolution || previousResolution;
    if (config.resolutions.includes(desired)) $("resolution").value = desired;
    matchSourceResolution(Boolean(state.sourceDimensions));
    $("upscale").checked = config.settings.upscale;
    $("steps").value = config.settings.steps;
    $("seed").value = config.settings.seed;
    const autoInstall = new Set([...(config.depth_auto_install ? ["depth"] : []), ...(config.aux_auto_install ? ["h3_ref2va", "qwen", "projector", "llama", "upscale"] : [])]);
    const missing = Object.keys(config.models).filter((k) => !config.models[k] && !autoInstall.has(k));
    if (missing.length) toast("尚未部署模型组件：" + missing.join(", "));
  } catch (e) {
    // 本地服务不可用时，保留表单默认值供浏览。
  }
  renderPeople();
  update();
  pollingLoop();
})();
window.addEventListener("message", (e) => {
  if (e.source !== parent) return;
  if (e.data?.type === "clone-settings") openSettings();
  if (e.data?.type === "clone-toggle-result-playback") toggleResultPlayback();
  if (e.data?.type === "clone-hidden") {
    video.pause();
    depthVideo.pause();
    resultVideo.pause();
  }
});
// 父页面可能在 iframe 脚本注册监听前发过状态；加载就绪后主动补取一次。
window.parent.postMessage({ type: "clone-ready" }, "*");
reportResultPlaybackAvailable(false);
