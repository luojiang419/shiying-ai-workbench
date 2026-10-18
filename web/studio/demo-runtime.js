import {cloudTaskVisible,currentCloudScope,workVisible} from './cloud-scope.js';
import {cloudErrorText} from './cloud-error-zh.js';
import {RemoteTaskArchive, ResultSaveQueue} from './remote-tasks.js';
import { bindAssistantChat } from "./assistant-chat.js";
import { bindQwenKeySettings } from './qwen-key-settings.js';
import { readNativeMaterial, isNativeMaterial } from './clone-center/native-materials.js';
import { materialTemplate, materialReplacement } from './clone-center/material-template.js';
import { bindResolutionSelectors } from "./resolution-selectors.js";
import { SubmissionQueue } from './submission-queue.js';
import { ASSISTANT_PRESETS, normalizedAssistantUrl } from "./assistant-providers.js";
import { AssetPicker, appendAssetFiles } from "./assets.js";
import './generation-eta.js';
import { CLOUD_SPEC, cloudOptions, mapLocalResolution, cloudSourcePrompt, lightweightSourcePrompt, buildCloudPayload, fileDataUrl, savedCloudTasks, saveCloudTask, removeCloudTask, normalizeCloudTask, mergeCloudTasks, cloudTaskNeedsRefresh, flushCloudTasks, allCloudTasks } from "./cloud-h3.js";
import { platformApi } from './platform-api.js';
import { upscaleRecords, upscaleRecord, saveUpscaleRecord, removeUpscaleRecord } from './upscale-records.js';
import { bindUpscaleSettings } from './upscale-settings.js';
import { assetManifest, normalizeUserReferences, validateReferences } from "./references.js";
import { isH3Prompt, validateH3Prompt } from "./prompt-format.js";
import { pageCount, lastOffset, worksFailure, LatestRequest, createPoller, ThumbnailLoader } from "./performance.js";
import {NODE_MODE_KEY, NODE_STORAGE_KEY, normalizeNodeAddress, savedNodeAddress, probeNode} from './compute-node.js';
import {abnormalTask} from './task-cleanup.js';
const root = document.querySelector("#minimax-plan");
const qs = (selector) => root.querySelector(selector);
const qsa = (selector) => [...root.querySelectorAll(selector)];
const resolutionControls = bindResolutionSelectors(qs('.resolution-select'), qs('.aspect-select'), qs('.resolution-size-select'));
const nativeInvoke = window.__TAURI__.core.invoke;
const invoke = (name,args) => nativeInvoke(name,name==='list_local_works'?{...args,cloudScope:currentCloudScope()}:args);
const upscaleSettingsUI = bindUpscaleSettings(root, invoke);
window.__TAURI__.event?.listen('service-operation-error', event => {
  runtime.serviceCommandError = String(event.payload);
  qs('.hero-status p').textContent = runtime.serviceCommandError;
  notifyError(event.payload);
}).catch(error => console.error(error));
let LOCAL_API = "http://127.0.0.1:7860";
let nodeEnabled = !window.MinimaxBrowser && localStorage.getItem(NODE_MODE_KEY) === 'true' && Boolean(savedNodeAddress());
let API = nodeEnabled ? savedNodeAddress() : LOCAL_API;
const isNode = () => nodeEnabled && runtime.settings?.generation_engine === 'local';
const isPlatform = () => runtime.settings?.generation_engine === 'platform_h3';
const isCloud = () => ['compshare_h3','platform_h3'].includes(runtime.settings?.generation_engine);
let nativeCloneAvailable = false;
const nativeCloneReady = window.MinimaxBrowser ? Promise.resolve() : invoke('cloud_clone_capabilities').then(result => {
  nativeCloneAvailable = result?.native_materials === true;
}).catch(() => {});
const SETTINGS_GROUP_PANES = {
  generation: ['engine', 'performance', 'upscale', 'clone'],
  connection: ['service'],
  assistant: ['assistant'],
  'qwen-api': ['qwen-api'],
  files: ['storage'],
  app: ['window', 'appearance', 'update'],
  diagnostics: ['about'],
};
function showSettingsGroup(group, target) {
  if (root.dataset.publicEdition === 'video-clone' && group === 'qwen-api') return;
  const panes = SETTINGS_GROUP_PANES[group];
  if (!panes) return;
  qsa('[data-setting]').forEach(button => {
    const active = button.dataset.setting === group;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  qsa('[data-setting-pane]').forEach(pane => pane.classList.toggle('active', panes.includes(pane.dataset.settingPane)));
  if (group === 'files') loadStorageSettings();
  if (group === 'generation') upscaleSettingsUI.load();
  if (group === 'qwen-api') qwenKeySettings.load();
  const page = qs('[data-page="settings"]');
  if (target) {
    target.closest('details')?.setAttribute('open', '');
    requestAnimationFrame(() => target.scrollIntoView({block:'center'}));
  } else page.scrollTop = 0;
}
function renderSettingsMode() {
  const mode = isNode() ? 'node' : runtime.settings?.generation_engine || 'local';
  qsa('.settings-mode-only').forEach(element => {
    element.hidden = !element.dataset.settingsModes.split(' ').includes(mode);
  });
}
function bindSettingsSearch() {
  const input = qs('.settings-search');
  const results = qs('.settings-search-results');
  const rows = qsa('.settings-content .setting, .settings-content .provider-field');
  const update = () => {
    const query = input.value.trim().toLocaleLowerCase();
    results.replaceChildren();
    if (!query) { results.hidden = true; return; }
    const matches = rows.filter(row => {
      if (row.hidden || row.closest('.settings-mode-only[hidden]')) return false;
      if (row.closest('[data-browser-unavailable]')) return false;
      if (root.dataset.publicEdition === 'video-clone' && row.closest('[data-setting-pane="qwen-api"]')) return false;
      return row.textContent.toLocaleLowerCase().includes(query);
    }).slice(0, 12);
    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'settings-search-empty';
      empty.textContent = '没有找到设置项';
      results.append(empty);
    }
    for (const row of matches) {
      const pane = row.closest('[data-setting-pane]');
      const group = Object.keys(SETTINGS_GROUP_PANES).find(key => SETTINGS_GROUP_PANES[key].includes(pane?.dataset.settingPane));
      if (!group) continue;
      const button = document.createElement('button');
      button.type = 'button';
      const title = document.createElement('span');
      title.textContent = row.querySelector('strong, span')?.textContent || row.textContent.trim().slice(0, 32);
      const context = document.createElement('small');
      context.textContent = pane.querySelector('.settings-pane-head h2')?.textContent || '';
      button.append(title, context);
      button.addEventListener('click', () => {
        showSettingsGroup(group, row);
        input.value = '';
        results.hidden = true;
      });
      results.append(button);
    }
    results.hidden = false;
  };
  input.addEventListener('input', update);
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { input.value = ''; results.hidden = true; }
    if (event.key === 'Enter') results.querySelector('button')?.click();
  });
  document.addEventListener('click', event => {
    if (event.target !== input && !results.contains(event.target)) results.hidden = true;
  });
}
const submitCloud = payload => isPlatform() ? platformApi.submit(payload) : invoke('cloud_h3_submit',{payload});
const queryCloud = row => row.provider === 'platform' ? platformApi.task(row.id) : invoke('cloud_h3_task',{taskId:row.id});
const cancelCloud = row => row.provider === 'platform' ? platformApi.cancel(row.id) : invoke('cloud_h3_cancel',{taskId:row.id});
const mediaUrl = work => work.localPath ? localWorkUrl(work) : (work.externalUrl || (work.output?.startsWith('https://') ? work.output : `${API}${work.output || ''}`));
let localResolutionOptions = null;
function renderEngineControls() {
  const cloud = isCloud();
  const select = qs('.resolution-select');
  if (!localResolutionOptions) localResolutionOptions = [...select.options].map(option => ({value:option.value,label:option.textContent}));
  const previous = select.value;
  const options = cloud ? cloudOptions() : (runtime.config?.resolutions || localResolutionOptions.map(item => item.value)).map(value => ({value,label:localResolutionOptions.find(item => item.value === value)?.label || value}));
  select.replaceChildren(...options.map(item => new Option(item.label, item.value)));
  select.value = options.some(item => item.value === previous) ? previous : cloud ? mapLocalResolution(previous) : (runtime.config?.defaults?.resolution || localResolutionOptions[0].value);
  select.disabled = false;
  const duration = qs('.duration-input');
  duration.min = cloud ? '4' : '1'; duration.max = cloud ? '30' : '15'; duration.step = cloud ? '1' : '0.5';
  if (cloud && (!Number.isInteger(Number(duration.value)) || Number(duration.value) < 4 || Number(duration.value) > 30)) duration.value = String(CLOUD_SPEC.duration);
  duration.disabled = false;
  qs('.steps-input').disabled = cloud || Boolean(runtime.config?.acceleration_mode?.startsWith('turbo'));
  qs('.seed-input').disabled = cloud;
  for (const selector of ['.performance-reference-model','.performance-acceleration','.performance-default-steps',
    '.performance-vram-reserve','.performance-free-cache','.performance-nsfw','.performance-nsfw-profile']) {
    qs(selector).disabled = cloud || isNode();
  }
  qs('.preflight-state').hidden = !cloud;
  qs('.preflight-state').textContent = cloud ? '优云 H3 可选择分辨率、画幅和 4–30 秒时长；积分消耗随规格变化' : '';
  updateSpecs();
}
const H3_TIPS = [
  "H3 多参考素材会按 Picture / Video 编号理解，提示词中写明主体、动作和镜头关系更稳定。",
  "首尾帧模式适合控制开头和结尾姿态，中间动作建议描述为连续变化，避免互相冲突。",
  "Turbo 8-step 会固定采样步数；想要更细腻的运动细节，可在设置里切回非 Turbo 方案。",
  "参考素材越清晰，主体一致性越好；同一人物建议保留服装、发型和场景线索。",
  "长镜头更容易累积漂移，提示词可以按先后顺序写动作节奏，帮助 H3 维持方向感。",
  "Seed 留空会随机探索；喜欢某次构图时，复制 Seed 可以复用同一随机起点继续微调。"
];
const runtime = {
  page: "overview",
  worksOffset: 0,
  worksTargetOffset: 0,
  worksTotal: 0,
  worksSize: [12, 24, 48, 100, 200].includes(Number(localStorage.getItem("minimax-works-size"))) ? Number(localStorage.getItem("minimax-works-size")) : 24,
  worksType: 'all',
  worksViewType: 'all',
  worksQuery: "",
  worksOrder: "desc",
  worksSignature: "",
  worksLoadedAt: 0,
  worksFailed: false,
  worksViewSize: 24,
  worksViewQuery: "",
  worksViewOrder: "desc",
  tasksLoadedAt: 0,
  taskItems: [],
  activeSignature: "",
  mode: "references",
  files: [],
  currentJob: localStorage.getItem("minimax-current-job") || "",
  selectedWork: null,
  playerMedia: null,
  previewWork: null,
  mediaViews: new WeakMap(),
  previewJobId: "",
  taskSignature: "",
  tasksLoading: false,
  worksLoading: false,
  overviewRefreshInFlight: false,
  serviceCommandRunning: false,
  serviceCommandError: "",
  overviewGeneration: 0,
  serviceStatus: null,
  apiStatus: null,
  resources: null,
  settings: null,
  submitting: false,
  config: null,
  configLoading: null,
  draftRestored: false,
  activeJobIds: new Set(),
  platformPricing: null,
  platformPage: 'overview',
};

function syncPlatformShell() {
  const frame = qs('.platform-shell');
  const active = isPlatform() && ['overview','settings'].includes(runtime.page);
  root.classList.toggle('platform-shell-active', active);
  root.classList.toggle('platform-recharge-active', active && runtime.platformPage === 'recharge');
  if (active && !frame.hasAttribute('src')) frame.src = frame.dataset.src;
  frame.hidden = !active;
  const switcher = qs('.platform-run-switch');
  switcher.hidden = !['overview','workbench','clone'].includes(runtime.page) || (active && runtime.platformPage !== 'overview');
  qs('.cloud-points').hidden = switcher.hidden || !isCloud();
  qsa('[data-run-mode]').forEach(button => {
    const selected = button.dataset.runMode === (isPlatform() ? 'cloud' : isNode() ? 'node' : 'local');
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}

qs('.platform-shell').addEventListener('load', () => {
  qs('.platform-shell').contentWindow?.postMessage({type:'platform-theme',theme:localStorage.getItem('minimax-theme') || 'night'}, '*');
  if (isPlatform() && ['overview','settings'].includes(runtime.page)) {
    qs('.platform-shell').contentWindow?.postMessage({type:'platform-navigate',page:runtime.page}, '*');
  }
});

let runModeChanging = false;
async function setRunMode(mode, verifiedAddress) {
  if (mode === 'node' && !verifiedAddress) { openComputeNodeDialog(); return; }
  if (runModeChanging) throw new Error('正在切换运行模式，请稍候');
  runModeChanging = true;
  qsa('[data-run-mode]').forEach(button => { button.disabled = true; });
  try { await activateRunMode(mode, verifiedAddress); }
  finally { runModeChanging = false; qsa('[data-run-mode]').forEach(button => { button.disabled = false; }); }
}
async function activateRunMode(mode, verifiedAddress) {
  const previousMode = {nodeEnabled, api: API};
  if (mode === 'node') {
    nodeEnabled = true;
    API = verifiedAddress;
  } else {
    nodeEnabled = false;
    API = LOCAL_API;
  }
  const engine = mode === 'cloud' ? 'platform_h3' : mode === 'personal' ? 'compshare_h3' : 'local';
  try {
    const settings = await invoke('set_generation_engine', {engine,nodeEnabled,nodeAddress:mode === 'node' ? API : null});
    localStorage.setItem(NODE_MODE_KEY, String(nodeEnabled));
    if (mode === 'node') localStorage.setItem(NODE_STORAGE_KEY, API);
    renderPerformanceSettings(settings);
  } catch (error) {
    nodeEnabled = previousMode.nodeEnabled;
    API = previousMode.api;
    localStorage.setItem(NODE_MODE_KEY, String(nodeEnabled));
    if (runtime.settings) renderPerformanceSettings(runtime.settings);
    throw error;
  }
  if (mode === 'node') qs('.compute-node-dialog').close();
  if (isPlatform()) await loadPlatformPricing();
  runtime.overviewGeneration += 1;
  worksRequest.cancel();
  runtime.serviceStatus = null; runtime.apiStatus = null; runtime.worksLoadedAt = 0; runtime.tasksLoadedAt = 0;
  runtime.taskSignature = ''; runtime.worksSignature = ''; runtime.activeSignature = ''; runtime.taskItems = [];
  runtime.currentJob = ''; localStorage.removeItem('minimax-current-job');
  const frame = qs('.clone-center-frame');
  frame?.removeAttribute('src');
  if (frame) frame.dataset.src = runtime.serviceStatus?.running || isNode() ? `${API}/clone-center/index.html` : 'clone-center/index.html';
  await loadConfig().catch(error => { runtime.config = null; notifyError(error); });
  showPage('overview');
  syncPlatformShell();
  await refreshOverview().catch(notifyError);
  await loadTasks().catch(notifyError);
  if (isCloud()) await refreshCloudBalance();
  if (isPlatform()) qs('.platform-shell').contentWindow?.postMessage({type:'platform-sync'}, '*');
}

async function loadPlatformPricing() {
  try { runtime.platformPricing = await platformApi.raw('/pricing'); }
  catch { runtime.platformPricing = null; }
  updateSpecs();
}

qsa('[data-run-mode]').forEach(button => button.addEventListener('click', () => settingsStartup.then(()=>setRunMode(button.dataset.runMode)).catch(notifyError)));
window.addEventListener('message', event => {
  if (event.source !== qs('.platform-shell')?.contentWindow) return;
  if (event.data?.type === 'platform-page') { runtime.platformPage = event.data.page; syncPlatformShell(); }
  if (event.data?.type === 'platform-open' && ['workbench','clone','works'].includes(event.data.page)) showPage(event.data.page);
  if (event.data?.type === 'platform-open-task' && isPlatform() && typeof event.data.jobId === 'string') {
    openPlatformRecentTask(event.data.jobId).catch(notifyError);
  }
  if (event.data?.type === 'platform-balance' && Number.isFinite(Number(event.data.available))) updateCloudPointBadges(`积分 ${Number(event.data.available).toLocaleString()}`);
  if (event.data?.type === 'platform-logout') { updateCloudPointBadges('请登录查看积分'); runtime.taskSignature = ''; loadTasks().catch(() => {}); }
  if (event.data?.type === 'platform-theme-change' && ['night','ivory','pure'].includes(event.data.theme)) setTheme(event.data.theme);
});

const worksRequest = new LatestRequest();
const thumbnails = new ThumbnailLoader(4);

function showPage(name) {
  if (window.MinimaxBrowser?.canManage === false && name === 'settings') name = 'overview';
  if (!["overview", "workbench", "image", "clone", "works", "settings"].includes(name)) name = "overview";
  if (name !== 'image') {
    root.classList.remove('image-viewer-open');
    qs('.image-studio-frame')?.contentWindow?.postMessage({type:'image-studio-viewer-close'}, '*');
  }
  const changed = runtime.page !== name;
  runtime.page = name;
  if (name === 'image') connectImageStudio().catch(notifyError);
  qs(".app").classList.toggle("show-mode-titlebar", ['overview','workbench','clone'].includes(name) || (isPlatform() && name === 'settings'));
  const cloneFrame = qs('.clone-center-frame');
  if (name === 'clone' && cloneFrame && !cloneFrame.hasAttribute('src')) connectCloneFrame().catch(notifyError);
  else if (name === 'clone') sendCloneState();
  if (name !== 'clone') cloneFrame?.contentWindow?.postMessage({type:'clone-hidden'}, '*');
  if (changed) {
    qs(".preview-media")?.pause();
    closePlayer();
    if (name !== "works") { worksRequest.cancel(); runtime.worksLoading = false; renderPagination(); }
  }
  qsa(".page").forEach((page) => page.classList.toggle("active", page.dataset.page === name));
  qsa(".dock button[data-target]").forEach((button) => {
    const active = button.dataset.target === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  localStorage.setItem("minimax-page", name);
  if (name === 'settings') upscaleSettingsUI.load();
  if (name === "works") loadWorks({background:Boolean(runtime.worksSignature)}).catch(() => {});
  if (name === "workbench" || name === "overview") { runtime.taskSignature = ""; loadTasks().catch(() => {}); }
  if (name === "workbench") requestAnimationFrame(fitCenterPreview);
  syncPlatformShell();
  if (isPlatform() && ['overview','settings'].includes(name)) {
    qs('.platform-shell').contentWindow?.postMessage({type:'platform-navigate',page:name}, '*');
  }
}

function setTheme(name) {
  root.classList.toggle("theme-ivory", name === "ivory");
  root.classList.toggle("theme-pure", name === "pure");
  qsa(".theme-choice button").forEach((button) => button.classList.toggle("active", button.dataset.themeName === name));
  localStorage.setItem("minimax-theme", name);
  qs('.platform-shell').contentWindow?.postMessage({type:'platform-theme',theme:name}, '*');
  sendImageStudioTheme();
}

function sendImageStudioTheme() {
  const frame = qs('.image-studio-frame');
  if (frame?.hasAttribute('src')) frame.contentWindow?.postMessage({type:'image-studio-theme',theme:localStorage.getItem('minimax-theme') || 'night',apiBase:API,serviceCheck:true}, new URL(frame.src).origin);
  if (runtime.imageEditId && runtime.imageEditSent !== runtime.imageEditId && frame?.hasAttribute('src')) {
    runtime.imageEditSent = runtime.imageEditId;
    frame.contentWindow?.postMessage({type:'image-studio-edit',imageId:runtime.imageEditId}, new URL(frame.src).origin);
  }
}
async function connectImageStudio() {
  const frame = qs('.image-studio-frame');
  const url = window.MinimaxBrowser ? '/image-studio/?embedded=1' : 'image-studio/index.html?embedded=1';
  if (frame.getAttribute('src') !== url) { frame.addEventListener('load',sendImageStudioTheme,{once:true}); frame.src=url; }
  else sendImageStudioTheme();
}
window.addEventListener('message',async event=>{
  const frames=[qs('.clone-center-frame'),qs('.image-studio-frame')];
  if(!frames.some(frame=>frame?.hasAttribute('src')&&event.source===frame.contentWindow&&event.origin===new URL(frame.src).origin)||window.MinimaxBrowser||event.data?.type!=='node-task-submitted')return;
  try{const base=event.data.apiBase;if(!remoteArchive.sources.has(base)&&(!isNode()||base!==API))return;await remoteArchive.watch(base);await remoteArchive.remember(base,event.data.kind,event.data.task);renderCachedTasks();}catch(error){notifyError(error);}
});
window.addEventListener('message', async event => {
  const frame = qs('.image-studio-frame');
  if (!frame?.hasAttribute('src') || event.source !== frame.contentWindow || event.origin !== new URL(frame.src).origin) return;
  if (event.data?.type === 'image-studio-ready') { root.classList.remove('image-viewer-open'); sendImageStudioTheme(); return; }
  if (event.data?.type === 'image-studio-edit-received') { runtime.imageEditId = null; return; }
  if (event.data?.type === 'image-studio-viewer-state') {
    if (event.data.open) globalThis.WorkbenchMotion?.settle(qs('[data-page="image"]'));
    root.classList.toggle('image-viewer-open', event.data.open === true && runtime.page === 'image');
    return;
  }
  if (event.data?.type !== 'image-studio-video') return;
  try {
    if (event.data.imageId) {
      if (!/^[a-f0-9]{32}$/.test(event.data.imageId)) throw new Error('来源图片 ID 无效');
      const response = await fetch(`${API}/api/image-studio/images/${event.data.imageId}/file`);
      if (!response.ok) throw new Error('来源图片不可用');
      const file = new File([await response.blob()], `qwen-${event.data.imageId}.png`, {type:'image/png'});
      if (assetPicker.references.filter(item => !item.video && !item.audio).length >= 9) throw new Error('视频参考图片已满 9 张，请先移除部分参考图');
      assetPicker.addFiles([file],{mode:'references'});
      assetPicker.setMode('references');
      updateCompiledState(); saveDraft();
      qs('.workbench-subtitle').textContent = '已添加图片创作参考图，可继续添加素材并描述视频内容';
    }
    showPage('workbench');
  } catch (error) { notifyError(error); }
});

async function connectCloneFrame() {
  if (runtime.cloneConnecting) return;
  runtime.cloneConnecting = true;
  const frame=qs('.clone-center-frame');
  const hint=qs('.clone-service-hint');
  const retry=qs('.clone-service-retry');
  frame.hidden=false; hint.hidden=true; retry.hidden=true;
  frame.addEventListener('load', sendCloneState, {once:true});
  const cloneUrl = window.MinimaxBrowser ? '/clone-center/index.html?embedded=1' : 'clone-center/index.html';
  // 使用安装版界面，但在脚本初始化前指定节点，避免首次配置请求落到本机。
  frame.dataset.src = `${cloneUrl}${cloneUrl.includes('?') ? '&' : '?'}apiBase=${encodeURIComponent(API)}`;
  if (frame.getAttribute('src') !== frame.dataset.src) frame.src=frame.dataset.src;
  try {
    await nativeCloneReady;
    sendCloneState();
  } finally {
    runtime.cloneConnecting=false;
  }
}
async function generationServiceAvailable() {
  try {
    const service = isNode()
      ? await probeNode(API).then(() => ({running:true}))
      : await invoke('service_status');
    runtime.serviceStatus = service;
    syncLocalEndpoint(service);
    if (service?.running) return true;
  } catch {}
  window.alert(isNode()
    ? '算力节点后端服务未连接，请先启动节点后端服务并检查连接后再点击生成。'
    : '后端服务未启动，请在概览页点击“启动服务”后再点击生成。');
  return false;
}
window.addEventListener('message', async event => {
  const frames = [qs('.clone-center-frame'), qs('.image-studio-frame')];
  const frame = frames.find(item => item?.hasAttribute('src') && event.source === item.contentWindow && event.origin === new URL(item.src).origin);
  if (!frame || event.data?.type !== 'generation-service-check') return;
  const allowed = await generationServiceAvailable();
  event.source.postMessage({type:'generation-service-result',nonce:event.data.nonce,allowed,apiBase:API}, event.origin);
});
window.addEventListener('workbench-service-changed', () => refreshOverview());
const runningUpscales = new Set();
function sendCloneState() {
  qs('.clone-center-frame')?.contentWindow?.postMessage({type:'cloud-h3-state',engine:runtime.settings?.generation_engine || 'local',
    materialInput:'v1',backgroundSubmission:'v1',serviceCheck:true,
    nativeMaterials:nativeCloneAvailable && isCloud() ? 'v1' : null,
    assistantRevision:JSON.stringify(enabledPromptProviders().map(provider => [provider.id,provider.name,provider.baseUrl,provider.defaultModel,provider.apiType])),
    referenceType:runtime.settings?.cloud_clone_reference_type || 'source',apiBase:API,
    upscales:upscaleRecords().map(record => ({sourceId:record.sourceId,
      status:record.status === 'running' && !runningUpscales.has(record.sourceId) ? 'failed' : record.status,
      output:record.output,
      url:record.status === 'done' ? (record.nodeBase && record.output ? `${record.nodeBase}${record.output}` : record.path ? localWorkUrl({localPath:record.path,output:record.output}) : '') : '',
      error:record.status === 'running' && !runningUpscales.has(record.sourceId) ? '上次超分未完成，可重试' : record.error || ''})),
    jobs:[...visiblePendingTasks().filter(row => row.clone),...savedCloudTasks().filter(row => row.clone)].map(row => ({id:row.id,kind:'generate',cloud:true,created_at:row.created_at,
      started_at:row.started_at,running_at:row.running_at,completed_at:row.completed_at,elapsed_seconds:row.elapsed_seconds,
      estimated_remaining_seconds:row.estimated_remaining_seconds,eta_updated_at:row.eta_updated_at,
      phase:row.phase,pendingSubmission:row.pendingSubmission,resolution:row.resolution,ratio:row.ratio,duration:row.duration,status:row.status === 'done' && row.output ? 'done' : ['failed','error'].includes(row.status) ? 'error' : cloudTaskNeedsRefresh(row) && row.status !== 'queued' ? 'running' : row.status,
      percent:row.status === 'done' ? 100 : 0,message:row.message || row.error || '',request:row.cloneRequest,
      result:row.output ? {video:row.localPath ? localWorkUrl(row) : row.output,
        original_video:row.localPath ? localWorkUrl(row) : row.output,
        upscaled_video:upscaleRecord(row.id)?.status === 'done' ? localWorkUrl({localPath:upscaleRecord(row.id).path}) : ''} : null}))}, '*');
}
async function submitCloneCloud(request, snapshot, context) {
  const base = snapshot.apiBase;
  if (request.input_mode === 'materials' && (!Array.isArray(request.reference_images) || request.reference_images.length > 9 ||
      new Set(request.reference_images).size !== request.reference_images.length || typeof request.prompt !== 'string' || !request.prompt.trim()))
    throw new Error('请检查提示词与参考素材，图片最多 9 张且不能重复');
  if (request.reference_mode === 'region_video') throw new Error('演员区域替换当前仅支持本地引擎');
  request = {...request, upscale:false};
  if (request.input_mode === 'materials') delete request.people;
  const lightweight = request.reference_mode === 'prompt_video';
  const referenceType = lightweight ? 'source' : request.reference_type || snapshot.referenceType;
  request.reference_type = referenceType;
  if (!lightweight && request.generation_goal === 'replace' && referenceType !== 'depth')
    throw new Error('人物替换请使用深度参考；原片语义参考可能保留源人物');
  // 云端仅提供语义参考；不能沿用本地 ControlNet 的能力声明。
  request.input_contract = {
    identity:'assigned_pictures', motion:'semantic_reference',
    source_rgb_to_generator:referenceType === 'source', fidelity:'approximate',
  };
  if (lightweight) Object.assign(request.input_contract, {
    motion:'prompt_video_reference', auxiliary_models:false, unselected_shots:'semantic_preservation',
  });
  if (request.input_mode === 'materials') request.input_contract.identity = 'user_assigned_references';
  const files = snapshot.nativeReferences ? [...snapshot.nativeReferences] : [];
  const imageIds = request.input_mode === 'materials' ? request.reference_images || [] : (request.people || []).flatMap(person => person.images || []);
  if (!snapshot.nativeReferences) {
  for (const id of imageIds) {
    const response = await fetch(`${base}/api/clone/assets/${encodeURIComponent(id)}/media`);
    if (!response.ok) throw new Error(`参考图 ${id} 读取失败`);
    const blob = await response.blob();
    files.push({file:new File([blob], `${id}.${blob.type === 'image/jpeg' ? 'jpg' : 'png'}`, {type:blob.type || 'image/png'}),video:false,audio:false});
  }
  }
  let prompt;
  if (snapshot.nativeReferences) {
    if (referenceType !== 'source' || request.input_mode !== 'materials') throw new Error('桌面云端素材仅支持原视频素材克隆');
    prompt = request.prompt.trim();
  } else if (referenceType === 'depth') {
    const plan = request.cloud_plan;
    if (plan?.schema !== 'clone.cloud-depth/v2' || plan.reference_type !== 'depth' ||
        plan.depth_id !== (request.depth_id || null) || (plan.reference_mode || 'extracted_depth') !== (request.reference_mode || 'extracted_depth') ||
        (plan.source_id ?? null) !== (request.source_id ?? null) || (plan.start ?? null) !== (request.start ?? null) || (plan.end ?? null) !== (request.end ?? null) ||
        plan.background_image_id !== request.background_image_id ||
        plan.scene_text !== request.scene_text || plan.source_prompt !== (request.manual_prompt ? request.prompt : '') ||
        JSON.stringify(plan.supplement || null) !== JSON.stringify(request.supplement || null) ||
        JSON.stringify(plan.people_image_ids) !== JSON.stringify(imageIds)) {
      throw new Error('Qwen引用规划与当前素材不一致，请重新规划');
    }
    const scene = await fetch(`${base}/api/clone/assets/${encodeURIComponent(request.background_image_id)}/media`);
    if (!scene.ok) throw new Error('无法读取背景场景图');
    const sceneBlob = await scene.blob();
    files.push({file:new File([sceneBlob], 'cloud-scene.png', {type:sceneBlob.type || 'image/png'}),video:false,audio:false});
    const response = request.reference_mode === 'depth_video'
      ? await fetch(`${base}/api/clone/assets/${encodeURIComponent(request.source_id)}/clip?start=${encodeURIComponent(request.start)}&end=${encodeURIComponent(request.end)}&cloud=true`)
      : await fetch(`${base}/api/clone/tasks/${encodeURIComponent(request.depth_id)}/cloud-depth`);
    if (!response.ok) throw new Error((await response.text()) || '深度视频读取失败');
    files.push({file:new File([await response.blob()], 'clone-depth.mp4', {type:'video/mp4'}),video:true,audio:false});
    prompt = plan.prompt;
  } else {
    const response = ['source_video','prompt_video'].includes(request.reference_mode)
      ? await fetch(`${base}/api/clone/assets/${encodeURIComponent(request.source_id)}/clip?start=${encodeURIComponent(request.start)}&end=${encodeURIComponent(request.end)}&cloud=true`)
      : await fetch(`${base}/api/clone/tasks/${encodeURIComponent(request.depth_id)}/source?cloud=true`);
    if (!response.ok) throw new Error((await response.text()) || '无法读取云端参考视频');
    files.push({file:new File([await response.blob()], 'clone-reference.mp4', {type:'video/mp4'}),video:true,audio:false});
    prompt = request.input_mode === 'materials' || request.manual_prompt ? request.prompt.trim() : lightweight
      ? lightweightSourcePrompt(request.people || [], request.duration, request.output_duration, request.supplement)
      : cloudSourcePrompt(request.people || [], request.duration, request.output_duration);
    if (request.supplement?.text && (!lightweight || request.manual_prompt)) prompt += `\n\n本次补充要求（${request.supplement.target?.kind === 'subject' ? `人物 ${request.supplement.target.subject_index}` : '全体'}）：${request.supplement.text}`;
  }
  const duration = request.output_duration;
  const resolution = `${request.cloud_resolution}|${request.cloud_ratio}`;
  if (request.input_mode === 'materials') validateReferences(prompt, 'references', files);
  const payload = await buildCloudPayload({prompt,resolution,duration,mode:'references',references:files});
  payload.use_context_ir = true;
  await context.submitting();
  const result = await submitSnapshotCloud(payload, snapshot, {requestId:context.requestId,clone:true,cloneRequest:request,mode:'references'});
  if (!result.task_id) throw new Error('优云智算未返回任务 ID');
  request.prompt = prompt;
  saveCloudTask({id:result.task_id,requestId:context.requestId,localReceipt:true,personalScope:result.personal_scope||snapshot.personalScope,cloud:true,provider:snapshot.engine === 'platform_h3' ? 'platform' : 'personal',ownerId:snapshot.ownerId,clone:true,cloneRequest:request,mode:'references',prompt,
    status:'queued',output:'',resolution:payload.resolution,ratio:payload.ratio,duration:payload.duration,created_at:Date.now()/1000});
  await flushCloudTasks(); sendCloneState(); refreshCloudBalance().catch(() => {});
  return savedCloudTasks().find(row => row.id === result.task_id);
}
window.addEventListener('message', async event => {
  if (event.source !== qs('.clone-center-frame')?.contentWindow) return;
  if (event.data?.type === 'clone-material-clip') {
    const {nonce,request} = event.data;
    try {
      if (!nativeCloneAvailable || !isCloud() || !isNativeMaterial(request.source_id)) throw new Error('原参考视频不可用');
      const files = await nativeCloneReferences({...request,input_mode:'materials',reference_mode:'prompt_video',reference_images:[]},API);
      event.source.postMessage({type:'clone-material-prompt-result',nonce,result:{file:files[0].file}},event.origin);
    } catch (error) { event.source.postMessage({type:'clone-material-prompt-result',nonce,error:String(error.message || error)},event.origin); }
    return;
  }
  if (event.data?.type === 'clone-material-prompt') {
    const {nonce,request} = event.data;
    try {
      if (!nativeCloneAvailable || !isCloud()) throw new Error('当前模式不支持桌面云端素材');
      const references = await nativeCloneReferences(request, API);
      const result = await compileNativeClone(request, references, structuredClone(enabledPromptProviders()));
      event.source.postMessage({type:'clone-material-prompt-result',nonce,result},event.origin);
    } catch (error) { event.source.postMessage({type:'clone-material-prompt-result',nonce,error:String(error.message || error)},event.origin); }
    return;
  }
  if (event.data?.type === 'clone-result-playback-available') {
    runtime.cloneResultPlaybackAvailable = Boolean(event.data.available);
    return;
  }
  if (event.data?.type === 'clone-ready') { runtime.cloneResultPlaybackAvailable = false; sendCloneState(); updateCloudPointBadges(); return; }
  if (event.data?.type === 'clone-cloud-cancel') {
    try {
      if (event.data.taskId.startsWith('prepare-')) await submissionQueue.cancel(event.data.taskId);
      else await cancelCloud(savedCloudTasks().find(row=>row.id===event.data.taskId)||{id:event.data.taskId,provider:isPlatform()?'platform':'personal'});
      sendCloneState(); await loadTasks();
    }
    catch (error) { notifyError(error); }
    return;
  }
  if (event.data?.type === 'clone-upscale') {
    const {nonce,taskId,cloud} = event.data;
    try {
      let job;
      if (cloud) {
        job = savedCloudTasks().find(row => row.id === taskId && row.clone);
      } else {
        job = await api(`/api/clone/tasks/${encodeURIComponent(taskId)}`);
        if (job.kind !== 'generate' || job.status !== 'done') throw new Error('原片尚未生成，无法超分');
        job = {id:job.id,output:job.result?.original_video || job.result?.video,cloud:false};
      }
      if (!job?.output || (cloud && job.status !== 'done')) throw new Error('原片尚未生成，无法超分');
      event.source.postMessage({type:'clone-upscale-result',nonce}, '*');
      upscaleWork(job).catch(notifyError);
    } catch (error) { event.source.postMessage({type:'clone-upscale-result',nonce,error:String(error)}, '*'); }
    return;
  }
  if (event.data?.type === 'clone-export-compare') {
    const {nonce,taskId,cloud,variant} = event.data;
    try {
      const job = cloud
        ? savedCloudTasks().find(row => row.id === taskId && row.clone)
        : await api(`/api/clone/tasks/${encodeURIComponent(taskId)}`);
      const request = (cloud ? job?.cloneRequest : job?.request) || {};
      const extractedDepth = (request.reference_mode || 'extracted_depth') === 'extracted_depth';
      const depthId = extractedDepth ? request.depth_id : null;
      const sourceId = extractedDepth ? null : request.source_id;
      const upscale = upscaleRecord(taskId);
      const savedUpscale = upscale?.status === 'done' ? (upscale.path || upscale.output) : '';
      const upscaledVideo = cloud ? savedUpscale : (job?.result?.upscaled_video || savedUpscale);
      const originalVideo = cloud ? (job?.localPath || job?.output) : (job?.result?.original_video || job?.result?.video);
      const resultVideo = variant === 'original' ? originalVideo : (upscaledVideo || originalVideo);
      if (!job || job.status !== 'done' || !(depthId || sourceId) || !resultVideo) throw new Error('原视频或克隆视频不可用，无法导出对比');
      let sourceDataUrl = null;
      if (isNativeMaterial(sourceId)) {
        const references = await nativeCloneReferences({...request,reference_images:[]},API);
        sourceDataUrl = await fileDataUrl(references[0].file);
      }
      const result = await invoke('export_clone_comparison', {depthId,sourceId,variant,...(sourceDataUrl ? {sourceDataUrl} : {}),
        start:extractedDepth ? null : request.start,end:extractedDepth ? null : request.end,taskId,resultVideo});
      event.source.postMessage({type:'clone-export-compare-result',nonce,result}, '*');
    } catch (error) { event.source.postMessage({type:'clone-export-compare-result',nonce,error:String(error)}, '*'); }
    return;
  }
  if (event.data?.type !== 'clone-cloud-submit') return;
  const {nonce,request} = event.data;
  try {
    if (!isCloud()) throw new Error('当前生成引擎不是优云智算 H3');
    const pending = await submissionQueue.add({cloneRequest:request,apiBase:API,engine:runtime.settings.generation_engine,
      providers:structuredClone(enabledPromptProviders()),ownerId:platformApi.userId(),personalScope:sessionStorage.getItem('minimax-personal-cloud-scope')||'',referenceType:request.reference_type || runtime.settings?.cloud_clone_reference_type || 'source'},
      {clone:true,cloud:true,cloneRequest:request,mode:'references',prompt:request.prompt,resolution:request.cloud_resolution,ratio:request.cloud_ratio,duration:request.output_duration});
    event.source.postMessage({type:'clone-cloud-submit-result',nonce,taskId:pending.id}, '*');
  } catch (error) { event.source.postMessage({type:'clone-cloud-submit-result',nonce,error:String(error)}, '*'); }
});
function bindInterface() {
  qs('.clone-service-retry')?.addEventListener('click', () => connectCloneFrame().catch(notifyError));
  qs('.open-clone-settings')?.addEventListener('click', () => {
    const frame=qs('.clone-center-frame');const loaded=frame.hasAttribute('src');
    if (!loaded) frame.addEventListener('load',()=>frame.contentWindow.postMessage({type:'clone-settings'},'*'),{once:true});
    showPage('clone');if(loaded)frame.contentWindow.postMessage({type:'clone-settings'},'*');
  });
  qsa(".dock button[data-target]").forEach((button) => button.addEventListener("click", () => showPage(button.dataset.target)));
  qsa(".goto-workbench").forEach((button) => button.addEventListener("click", () => showPage("workbench")));
  qsa(".goto-works").forEach((button) => button.addEventListener("click", () => showPage("works")));
  qsa(".goto-settings").forEach((button) => button.addEventListener("click", () => showPage("settings")));
  qsa('[data-setting]').forEach(button => button.addEventListener('click', () => showSettingsGroup(button.dataset.setting)));
  bindSettingsSearch();
  const themes = ["night", "ivory", "pure"];
  qs(".theme-switch")?.addEventListener("click", () => {
    const current = localStorage.getItem("minimax-theme") || "night";
    setTheme(themes[(themes.indexOf(current) + 1) % themes.length]);
  });
  qsa(".theme-choice button").forEach((button) => button.addEventListener("click", () => setTheme(button.dataset.themeName)));
  qs(".work-prompt").addEventListener("input", () => {
    qs(".prompt-count").textContent = `需求 ${Array.from(qs(".work-prompt").value.trim()).length} 字`;
    updateCompiledState();
  });
  qs(".task-menu-layer")?.addEventListener("click", (event) => { if (event.target === event.currentTarget) closeTaskMenu(); });
  document.addEventListener("pointerdown", (event) => {
    if (qs(".task-menu-layer")?.hidden || event.target.closest(".task-menu-panel, .task-menu-trigger")) return;
    closeTaskMenu();
  });
  showPage(localStorage.getItem("minimax-page") || "overview");
  setTheme(localStorage.getItem("minimax-theme") || "night");
}

let initialLocalEndpoint = null;
async function api(path, options = {}) {
  if (!isNode()) {
    initialLocalEndpoint ||= invoke('service_status').then(syncLocalEndpoint).catch(() => {});
    await initialLocalEndpoint;
  }
  const response = await fetch(`${API}${path}`, { signal: AbortSignal.timeout(options.method === "POST" ? 120000 : 15000), ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || `请求失败 (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function intercept(element, handler) {
  element?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    Promise.resolve().then(() => handler(event)).catch(error => notifyError(error));
  }, true);
}

function notifyError(error) {
  error = cloudErrorText(error);
  qs('.workbench-subtitle').textContent = `操作失败：${error.message || error}`;
  window.alert(`操作失败：${error.message || error}`);
}

function currentH3Tip(seed = "") {
  const textSeed = [...String(seed)].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const index = (Math.floor(Date.now() / 5200) + textSeed) % H3_TIPS.length;
  return H3_TIPS[index];
}

function formatElapsedSeconds(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "";
  const rounded = Math.round(value);
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return minutes > 0 ? `${minutes} 分 ${String(rest).padStart(2, "0")} 秒` : `${rest} 秒`;
}

function formatUsedSeconds(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const rest = String(total % 60).padStart(2, "0");
  return `${minutes}分${rest}秒`;
}

function setPreview(title, message, progress = 0, options = {}) {
  if (/失败|错误|无法/.test(title) && message) message=cloudErrorText(message);
  const media = qs(".preview-media");
  resetMediaView(qs(".preview"));
  if (media.getAttribute('src')) releaseMedia(media);
  media.hidden = true;
  runtime.previewWork = null;
  qs(".preview-copy").hidden = false;
  const preview = qs(".preview");
  const loading = Boolean(options.loading);
  runtime.previewEtaJob = loading ? options.job || null : null;
  globalThis.GenerationEta.render(qs('.generation-eta'), runtime.previewEtaJob);
  qs('.preview-copy').classList.toggle('has-generation-eta', !qs('.generation-eta').hidden);
  globalThis.GenerationEta.renderPipeline(qs('.preview-generation-stages'), options.job);
  preview.classList.toggle("is-loading", loading);
  preview.classList.toggle("is-cloud", Boolean(options.job?.cloud));
  const failed = /失败|错误|无法/.test(title) && !loading;
  preview.classList.toggle("is-failed", failed);
  qs(".preview-copy-error").hidden = !failed;
  qs(".preview-copy-error").textContent = "复制原因";
  preview.classList.remove("is-welcome");
  qs(".preview-copy").classList.toggle("is-loading", loading);
  preview.style.width = "";
  preview.style.aspectRatio = "";
  runtime.previewJobId = options.jobId || "";
  qsa(".task-card").forEach((card) => card.classList.toggle("selected", card.dataset.jobId === runtime.previewJobId));
  qs(".preview-title").textContent = title;
  qs(".preview-message").textContent = message || "";
  const tip = qs(".preview-tip");
  tip.hidden = !loading;
  tip.textContent = loading ? options.tip || currentH3Tip(`${title}${message || ""}`) : "";
  qs(".preview-progress i").style.width = `${Math.max(0, Math.min(100, progress))}%`;
  const timing = runtime.previewEtaJob?.sampling_timing;
  qs('.preview-sampling-label').textContent = timing?.stage === 'sample'
    ? `${Math.round(progress)}%`
    : timing?.stage === 'decode' || timing?.stage === 'save' ? '处理中' : '';
  if (timing && loading) {
    qs('.preview-title').textContent = timing.stage === 'decode' ? '正在解码音画' : timing.stage === 'save' ? '正在合成保存' : '视频生成';
  }
}

function fitCenterPreview() {
  const media = qs(".preview-media");
  if (media.hidden || !media.videoWidth || !media.videoHeight) return;
  const canvas = qs(".preview-wrap").getBoundingClientRect();
  const ratio = media.videoWidth / media.videoHeight;
  const maxWidth = Math.min(510, canvas.width * 0.84);
  const maxHeight = canvas.height * 0.8;
  const width = Math.min(maxWidth, maxHeight * ratio);
  const preview = qs(".preview");
  preview.style.width = `${Math.round(width)}px`;
  preview.style.aspectRatio = `${media.videoWidth} / ${media.videoHeight}`;
}

function playTaskInPreview(job) {
  if (!job?.output || job.output_available === false) {
    setPreview("视频文件已不存在", "该任务已完成，但历史输出文件已被移除，无法播放。任务参数和提示词仍会保留。");
    return;
  }
  const media = qs(".preview-media");
  const src = mediaUrl(job);
  runtime.previewJobId = job.id;
  runtime.previewWork = job;
  resetMediaView(qs(".preview"));
  qs(".preview").classList.remove("is-loading", "is-welcome");
  qs(".preview-copy").classList.remove("is-loading");
  qs(".preview-tip").hidden = true;
  qs(".preview-copy").hidden = true;
  media.hidden = false;
  if (media.getAttribute("src") !== src) media.src = src;
  if (!media.dataset.aspectRatioBound) {
    media.dataset.aspectRatioBound = "1";
    media.addEventListener("loadedmetadata", fitCenterPreview);
  }
  if (!media.dataset.playbackErrorBound) {
    media.dataset.playbackErrorBound = "1";
    media.addEventListener("error", () => {
      if (runtime.previewJobId === runtime.previewWork?.id) {
        setPreview("视频无法播放", "视频文件无法加载，可能已被移除或当前媒体格式无法解码。请刷新任务列表后重试。");
      }
    });
  }
  if (media.readyState >= 1) fitCenterPreview();
  qs(".preview-spec").textContent = `历史任务 · ${job.resolution || "规格未知"}`;
  qs(".preview-time").textContent = `${job.duration || "--"} 秒`;
  qsa(".task-card").forEach((card) => card.classList.toggle("selected", card.dataset.jobId === job.id));
  media.play().catch(() => {});
}

async function loadConfig() {
  if (runtime.configLoading) return runtime.configLoading;
  runtime.configLoading = applyConfig().finally(() => { runtime.configLoading = null; });
  return runtime.configLoading;
}

function resolutionOptionLabel(name) {
  const value = String(name);
  const square = value.match(/^Square (\d+)x(\d+)$/);
  if (square) return `Square · ${square[1]}×${square[2]}`;
  const match = value.match(/^(\S+MP)\s+(\S+)\s+-\s+(\d+)x(\d+)(?:\s+\((.+)\))?$/);
  if (!match) return value;
  return [match[1], match[2], `${match[3]}×${match[4]}`, match[5]].filter(Boolean).join(" · ");
}

async function applyConfig() {
  if (isCloud()) { renderEngineControls(); restoreDraft(); return; }
  const config = await api("/api/config");
  const select = qs(".resolution-select");
  const previous = select.value;
  select.innerHTML = "";
  config.resolutions.forEach((name) => select.add(new Option(resolutionOptionLabel(name), name)));
  select.value = config.resolutions.includes(previous) ? previous : config.defaults.resolution;
  if (!runtime.config) {
    qs(".duration-input").value = config.defaults.duration;
    qs(".steps-input").value = runtime.settings?.default_steps || config.defaults.steps;
  }
  if (config.acceleration_mode.startsWith("turbo")) qs(".steps-input").value = 8;
  qs(".steps-input").disabled = config.acceleration_mode.startsWith("turbo");
  runtime.config = config;
  restoreDraft();
  qs('.preflight-state').hidden = true;
  qs('.preflight-state').textContent = '';
  updateSpecs();
  renderEngineControls();
}
function displayVideoDuration(value) {
  if (value == null || value === '') return '-';
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0
    ? String(Math.trunc((seconds + Number.EPSILON) * 10) / 10)
    : (value || '-');
}

async function upscaleWork(job) {
  if (runningUpscales.has(job.id) || !job.output) return;
  runningUpscales.add(job.id);
  saveUpscaleRecord({sourceId:job.id,status:'running',createdAt:Date.now()});
  sendCloneState();
  runtime.taskSignature = '';
  loadTasks().catch(() => {});
  try {
    if (isNode() && !job.cloud) {
      const nodeBase = job.sourceBase || API;
      const sourceFilename = decodeURIComponent(String(job.output || '').split('/').pop());
      const request = async (path, options = {}) => {
        const response = await fetch(`${nodeBase}${path}`, {signal:AbortSignal.timeout(15000),...options});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `节点请求失败 (${response.status})`);
        return data;
      };
      const started = await request('/api/upscale', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:sourceFilename})});
      let result;
      do {
        await new Promise(resolve => setTimeout(resolve, 3000));
        result = await request(`/api/upscale/${encodeURIComponent(started.task_id)}`);
      } while (result.status === 'running');
      if (result.status !== 'done') throw new Error(result.error || '节点超分失败');
      saveUpscaleRecord({sourceId:job.id,status:'done',nodeBase,name:result.name,output:result.output,createdAt:Date.now()});
      runtime.worksSignature = '';
      if (runtime.page === 'works') loadWorks({force:true}).catch(() => {});
      sendCloneState();
      return;
    }
    const upscaleSettings = await invoke('get_upscale_settings', {path:null});
    if (upscaleSettings.settings.engine !== 'topaz' && !(await ensureAuxRuntime())) {
      saveUpscaleRecord({sourceId:job.id,status:'deferred',createdAt:Date.now()});
      return;
    }
    let sourceFilename = job.cloud ? job.filename : decodeURIComponent(job.output.split('/').pop());
    if (job.cloud && job.output.startsWith('https://')) {
      const localPath = await invoke('cloud_h3_download', {taskId:job.id,url:job.output,filename:job.filename || null});
      sourceFilename = localPath.split(/[\\/]/).pop();
      saveCloudTask({...job,localPath,filename:sourceFilename});
    } else if (job.cloud) {
      sourceFilename = job.filename || decodeURIComponent(job.output.split('/').pop());
    }
    const result = await invoke('upscale_video', {sourceFilename});
    saveUpscaleRecord({sourceId:job.id,status:'done',path:result.path,output:result.output,name:result.name,createdAt:Date.now()});
    runtime.worksSignature = '';
    if (runtime.page === 'works') loadWorks({force:true}).catch(() => {});
    sendCloneState();
  } catch (error) {
    saveUpscaleRecord({sourceId:job.id,status:'failed',error:String(error),createdAt:Date.now()});
    notifyError(error);
  } finally {
    runningUpscales.delete(job.id);
    sendCloneState();
    runtime.taskSignature = '';
    loadTasks().catch(() => {});
  }
}

let auxiliaryInstallPromise = null;
async function ensureAuxRuntime() {
  if (await invoke('aux_runtime_available')) return true;
  if (!window.confirm('此功能需要可选辅助运行时（下载约 4 GB，安装后约 8 GB，不包含本地 H3 权重）。现在从魔塔下载并安装吗？')) return false;
  if (!auxiliaryInstallPromise) auxiliaryInstallPromise = invoke('install_aux_runtime').finally(() => { auxiliaryInstallPromise = null; });
  qs('.workbench-subtitle').textContent = '正在从魔塔下载并安装辅助运行环境…';
  await auxiliaryInstallPromise;
  return true;
}

function updateSpecs() {
  resolutionControls.sync();
  const duration = Number(qs(".duration-input").value) || 1;
  qs(".preview-time").textContent = `00:${String(Math.round(duration)).padStart(2, "0")}`;
  const resolution = qs(".resolution-select").value;
  const dimensions = resolution.match(/(\d+)x(\d+)/);
  qs(".preview-spec").textContent = isCloud() ? `优云智算 H3 · ${resolution.replace('|', ' · ')} · ${duration} 秒` : dimensions ? `预览输出 · ${dimensions[1]} × ${dimensions[2]}` : resolution;
  const estimate = qs('.platform-cost-estimate');
  estimate.hidden = !isPlatform();
  if (isPlatform()) {
    const rate = runtime.platformPricing?.rates_per_second?.[resolution.split('|')[0]];
    estimate.textContent = Number.isInteger(rate) && Number.isInteger(duration) && duration >= 4 && duration <= 30
      ? `本次预计 ${Number(rate * duration).toLocaleString()} 积分 · ${rate} 积分/秒 × ${duration} 秒`
      : '请确认云端分辨率与 4–30 秒时长，或等待价格加载';
  }
}

let assetPicker;
function installAssetPicker() {
  assetPicker = new AssetPicker(root, API, mode => {
    runtime.mode = mode;
  }, updateCompiledState);
}

async function fileToAssistantImage(item, tag) {
  const bitmap = await createImageBitmap(item.file);
  try {
    const scale = Math.min(1, 4096 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return {tag, name:item.file.name, dataUrl:canvas.toDataURL('image/jpeg', 0.86)};
  } finally { bitmap.close(); }
}

async function assistantImages(mode, source = assetPicker) {
  if (mode === 'references') {
    let picture = 0;
    const items = source.references.filter(item => !item.video && !item.audio);
    return Promise.all(items.map(item => fileToAssistantImage(item, `<Picture ${++picture}>`)));
  }
  const items = [source.frames.first_frame, source.frames.last_frame].filter(Boolean);
  return Promise.all(items.map((item, index) => fileToAssistantImage(item, `<Picture ${index + 1}>`)));
}

const compileState = {fingerprint:'', plan:null, optimized:false};
function enabledPromptProviders() {
  return savedProviders().filter(provider => {
    if (!provider || provider.enabled === false || typeof provider.baseUrl !== 'string' || !provider.baseUrl.trim() || typeof provider.defaultModel !== 'string' || !provider.defaultModel.trim()) return false;
    if (window.MinimaxBrowser && provider.serverManaged === true) return true;
    if ((typeof provider.encryptedKey === 'string' && provider.encryptedKey.trim()) || (typeof provider.apiKey === 'string' && provider.apiKey.trim())) return true;
    try { return ['localhost','127.0.0.1','[::1]'].includes(new URL(provider.baseUrl).hostname); }
    catch { return false; }
  });
}
function compileFingerprint() {
  const mode = runtime.mode;
  const assets = mode === 'references' ? assetPicker.references : [assetPicker.frames.first_frame, assetPicker.frames.last_frame].filter(Boolean);
  return JSON.stringify({brief:qs('.work-prompt').value, mode,
    assets:assets.map(item => [item.id,item.file.name,item.file.size,item.file.lastModified]),
    duration:qs('.duration-input').value, resolution:qs('.resolution-select').value,
    engine:runtime.settings?.generation_engine,
    referenceModel:runtime.settings?.workbench_reference_model,
    assistants:enabledPromptProviders()
      .map(provider => [provider.id,provider.baseUrl,provider.apiType,provider.defaultModel,provider.encryptedKey])});
}
function updateCompiledState() {
  if (!assetPicker) return;
  if (compileState.fingerprint !== compileFingerprint()) compileState.plan = null;
}
function snapshotVideoDraft() {
  return {
    mode:runtime.mode, references:assetPicker.references.map(item => ({...item})), frames:{...assetPicker.frames},
    brief:qs('.work-prompt').value, fingerprint:compileFingerprint(),
    resolution:qs('.resolution-select').value, duration:qs('.duration-input').value,
    steps:qs('.steps-input').value, seed:qs('.seed-input').value.trim(),
    engine:runtime.settings?.generation_engine || 'local', apiBase:API, remoteNode:isNode(),
    ownerId:platformApi.userId(), personalScope:sessionStorage.getItem('minimax-personal-cloud-scope')||'', providers:structuredClone(enabledPromptProviders()),
    referenceModel:runtime.settings?.workbench_reference_model || 'fl2va',
    cached:compileState.optimized && compileState.fingerprint === compileFingerprint()
      ? {prompt:qs('.compiled-prompt').value, plan:compileState.plan} : null,
  };
}
async function compileVideoSnapshot(draft) {
  const {mode, references, frames, brief, duration, providers} = draft;
  const original = warning => {
    if (!brief.trim()) throw new Error('未配置 AI 助手，请填写生成提示词');
    if (Array.from(brief).length > 7000) throw new Error('提示词超过 7000 字符');
    validateReferences(brief, mode, references, frames);
    return {prompt:brief, plan:{}, optimized:false, warning};
  };
  if (!providers.length) return original('未启用 AI 助手，直接使用原始提示词');
  try {
    if (draft.cached?.prompt) return {...draft.cached, prompt:validateH3Prompt(draft.cached.prompt, mode, Number(duration), references, frames), optimized:true};
    const raw = brief.trim();
    if (isH3Prompt(raw)) return {prompt:validateH3Prompt(raw, mode, Number(duration), references, frames), plan:{}, optimized:true};
    const manifest = assetManifest(mode, references, frames);
    if (!raw && !manifest.some(item => item.kind === 'Picture' || item.kind === 'Video')) throw new Error('智能创作至少需要一张图片或视频');
    const normalized = normalizeUserReferences(raw, manifest);
    validateReferences(normalized.text, mode, references, frames);
    const images = await assistantImages(mode, draft);
    const video = await assistantVideoFrames(mode, draft);
    const durations = {...video.durations, ...await assistantAudioDurations(mode, draft)};
    const assets = `目标画幅与分辨率：${draft.resolution}\n` + manifest.map(item => `${item.tag} (${item.role || item.kind})：${item.name}${durations[item.id] ? `，${durations[item.id].toFixed(2)} 秒` : ''}`).join('\n') + (draft.cloneRules ? `\n克隆编辑约束（显式用户编辑优先）：\n${draft.cloneRules}` : '');
    const request = {prompt:normalized.text, mode, duration:Number(duration), assets, images,
      videoFrames:video.frames, manifest:manifest.map(item => ({...item, duration:durations[item.id] || null}))};
    const failures = [];
    for (const provider of providers) {
      try {
        const apiKey = provider.serverManaged ? '' : provider.encryptedKey ? await invoke('unprotect_assistant_key', {encrypted:provider.encryptedKey}) : (provider.apiKey || '');
        const result = provider.serverManaged
          ? await invoke('optimize_server_prompt', {request})
          : await invoke('optimize_assistant_prompt', {request:{baseUrl:provider.baseUrl, apiKey, model:provider.defaultModel, apiType:provider.apiType, ...request}});
        const prompt = typeof result === 'string' ? result : result.prompt;
        validateH3Prompt(prompt, mode, Number(duration), references, frames);
        return {prompt, plan:typeof result === 'string' ? {} : result.plan || {}, optimized:true};
      } catch (error) { failures.push(`${provider.name || provider.defaultModel}：${error.message || error}`); }
    }
    throw new Error(failures.join('\n'));
  } catch (error) { return original(`AI 助手暂不可用，已使用原始提示词：${error.message || error}`); }
}
async function submissionApi(base, path, options = {}) {
  const response = await fetch(`${base}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(cloudErrorText(data.detail || data,{status:response.status})),{raw:data});
  return data;
}
async function nativeCloneReferences(request, base) {
  if (request.input_mode !== 'materials' || request.reference_mode !== 'prompt_video') throw new Error('桌面云端素材需要使用原视频素材克隆');
  const references = [];
  for (const id of request.reference_images || []) {
    if (isNativeMaterial(id)) {
      const asset = await readNativeMaterial(id,'image');
      references.push({id,file:asset.file,video:false,audio:false});
    } else {
      const response = await fetch(`${base}/api/clone/assets/${encodeURIComponent(id)}/media`);
      if (!response.ok) throw new Error('原参考图不可用，请在云端模式重新添加');
      references.push({id,file:new File([await response.blob()],`${id}.png`,{type:'image/png'}),video:false,audio:false});
    }
  }
  let file;
  if (isNativeMaterial(request.source_id)) {
    const asset = await readNativeMaterial(request.source_id,'video');
    const result = await invoke('cloud_h3_clip_video',{dataUrl:await fileDataUrl(asset.file),start:request.start,end:request.end});
    const response = await fetch(result.data_url);
    file = new File([await response.blob()],asset.name,{type:'video/mp4'});
  } else {
    const response = await fetch(`${base}/api/clone/assets/${encodeURIComponent(request.source_id)}/clip?start=${request.start}&end=${request.end}&cloud=true`);
    if (!response.ok) throw new Error('原参考视频不可用，请在云端模式重新添加');
    file = new File([await response.blob()],request.source_name || 'reference.mp4',{type:'video/mp4'});
  }
  references.push({id:request.source_id,file,video:true,audio:false});
  return references;
}
async function compileNativeClone(request, references, providers) {
  validateReferences(request.prompt,'references',references);
  const manifest = assetManifest('references',references);
  for (const item of manifest) item.number = Number(item.tag.match(/\d+/)[0]);
  if (!providers.length) return materialTemplate(request,manifest);
  const rules = materialTemplate(request,manifest).prompt;
  const compiled = await compileVideoSnapshot({mode:'references',references,frames:{},brief:request.prompt,
    duration:request.output_duration,resolution:request.cloud_ratio,providers,cloneRules:rules});
  if (!compiled.optimized) throw new Error(compiled.warning || '克隆提示词优化失败，请检查 AI 助手或重试');
  const retention = compiled.prompt.split(/retention_analysis\s*:?/i).at(-1).split(/detailed_description\s*:?/i)[0];
  const contract = materialReplacement(request);
  if (contract && (/<Video\s+1>[^\n]*\bfully_preserved\b/i.test(retention)
      || contract.wholePerson && /only\s+(?:(?:her|his|the|their)\s+)?(?:facial identity|face|facial features)|only.{0,30}(?:identity and hairstyle|face and hair)/i.test(compiled.prompt)))
    throw new Error('克隆提示词优化缩小了人物替换范围，请检查 AI 助手或重试');
  return {...compiled,manifest,optimizer:{source:'assistant',name:'AI 助手'}};
}
function submitSnapshotCloud(payload, snapshot, context = {}) {
  if (snapshot.engine === 'platform_h3') {
    if (snapshot.ownerId !== platformApi.userId()) throw new Error('提交账号已变化，请使用原账号重新提交');
    return platformApi.submit(payload, context.requestId, context);
  }
  if(snapshot.ownerId!==platformApi.userId() || snapshot.personalScope!==(sessionStorage.getItem('minimax-personal-cloud-scope')||''))throw new Error('提交账号或优云 Key 已变化，请使用原身份重新提交');
  return invoke('cloud_h3_submit', {payload,context:{...context,ownerId:snapshot.ownerId,personalScope:snapshot.personalScope,requestId:context.requestId,prompt:payload.content?.find(item=>item.type==='text')?.text}});
}
async function executeSubmission(draft, context) {
  if (draft.cloneRequest) {
    const request = {...draft.cloneRequest};
    const usesNative = isNativeMaterial(request.source_id) || (request.reference_images || []).some(isNativeMaterial);
    const nativeReferences = usesNative ? await nativeCloneReferences(request, draft.apiBase) : null;
    request.reference_type ||= request.reference_mode === 'prompt_video' ? 'source' : draft.referenceType;
    if (request.prepare_prompt) {
      const compiled = nativeReferences ? await compileNativeClone(request, nativeReferences, draft.providers || [])
        : await submissionApi(draft.apiBase, '/api/clone/compile-prompt', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(request)});
      if (!['assistant','template'].includes(compiled.optimizer?.source) || compiled.optimizer?.warning || !compiled.prompt?.trim())
        throw new Error(compiled.optimizer?.warning || '提示词服务版本不兼容，请更新服务后重试');
      Object.assign(request, {prompt:compiled.prompt, optimizer:compiled.optimizer, material_manifest:compiled.manifest, prompt_plan:compiled.plan});
      delete request.prepare_prompt;
    }
    context.check();
    if (request.reference_type === 'depth') request.cloud_plan = await submissionApi(draft.apiBase, '/api/clone/cloud-plan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(request)});
    context.check();
    return submitCloneCloud(request, {...draft,nativeReferences}, context);
  }
  const compiled = await compileVideoSnapshot(draft);
  context.check();
  // 后台结果只更新仍与快照相同的草稿。
  if (draft.fingerprint === compileFingerprint()) {
    qs('.compiled-prompt').value = compiled.prompt;
    Object.assign(compileState, {fingerprint:draft.fingerprint, plan:compiled.plan, optimized:compiled.optimized});
    qs('.workbench-subtitle').textContent = compiled.warning || '提示词已准备，任务正在后台提交';
    saveDraft();
  }
  let job;
  if (draft.engine !== 'local') {
    const payload = await buildCloudPayload({...draft, prompt:compiled.prompt});
    await context.submitting();
    const response = await submitSnapshotCloud(payload, draft, {requestId:context.requestId,mode:draft.mode,userBrief:draft.brief,promptPlan:compiled.plan});
    if (!response.task_id) throw new Error('优云智算未返回任务 ID');
    job = {id:response.task_id, requestId:context.requestId, localReceipt:true, personalScope:response.personal_scope||draft.personalScope, cloud:true, provider:draft.engine === 'platform_h3' ? 'platform' : 'personal', ownerId:draft.ownerId,
      mode:draft.mode, prompt:compiled.prompt, userBrief:draft.brief, promptPlan:compiled.plan, status:'queued', output:'',
      resolution:payload.resolution, ratio:payload.ratio, duration:payload.duration, created_at:Date.now()/1000};
    saveCloudTask(job); await flushCloudTasks(); refreshCloudBalance().catch(() => {});
  } else {
    const body = new FormData();
    for (const [key, value] of Object.entries({mode:draft.mode, reference_model:draft.mode === 'references' ? draft.referenceModel : 'fl2va',
      prompt:compiled.prompt, user_brief:draft.brief, prompt_plan:JSON.stringify(compiled.plan), resolution:draft.resolution,
      duration:draft.duration, steps:draft.steps, seed:draft.seed})) body.append(key, value);
    appendAssetFiles(body, draft.mode, draft.references, draft.frames);
    if(draft.remoteNode)await remoteArchive.watch(draft.apiBase);
    await context.submitting();
    job = await submissionApi(draft.apiBase, '/api/generate-upload', {method:'POST', body});
    if(draft.remoteNode) {await remoteArchive.watch(draft.apiBase);job=await remoteArchive.remember(draft.apiBase,'video',job);}
  }
  return job;
}
const submissionQueue = new SubmissionQueue('desktop', executeSubmission, () => {
  runtime.taskSignature = ''; sendCloneState(); loadTasks().catch(() => {});
}, (pending, job) => {
  if (runtime.previewJobId === pending.id) {
    runtime.currentJob = job.id; runtime.previewJobId = job.id;
    localStorage.setItem('minimax-current-job', job.id);
    showTaskInPreview(job);
  }
  qs('.clone-center-frame')?.contentWindow?.postMessage({type:'clone-submission-complete', pendingId:pending.id, taskId:job.id}, '*');
});
submissionQueue.canRun=draft=>draft.engine==='platform_h3'
  ? Boolean(draft.ownerId && draft.ownerId===platformApi.userId())
  : draft.engine==='compshare_h3' ? Boolean(draft.personalScope && draft.personalScope===sessionStorage.getItem('minimax-personal-cloud-scope') && (draft.ownerId||'')===(platformApi.userId()||'')) : true;

async function submitGeneration() {
  if (runtime.submitting) return;
  if (runtime.mode === 'references' && !assetPicker.references.length) assetPicker.setMode('keyframes');
  for (const input of qsa('.duration-input, .steps-input, .seed-input')) if (!input.reportValidity()) return;
  const draft = snapshotVideoDraft();
  if (!draft.brief.trim() && !assetManifest(draft.mode, draft.references, draft.frames).length) return setPreview('无法提交', '请输入创作需求或上传图片、视频');
  if (draft.seed && (!/^-?\d+$/.test(draft.seed) || BigInt(draft.seed) > 9223372036854775807n)) return setPreview('无法提交', 'Seed 请输入不大于 9223372036854775807 的整数，留空或负数为随机');
  runtime.submitting = true;
  const button = qs('.generate'); button.disabled = true;
  try {
    if (draft.engine === 'local' && !(await generationServiceAvailable())) return;
    // 服务检查可能发现实际监听地址，仍保留检查前的全部创作参数。
    draft.apiBase = API;
    const pending = await submissionQueue.add(draft, {mode:draft.mode, prompt:draft.brief, user_brief:draft.brief,
      resolution:draft.engine !== 'local' ? draft.resolution.split('|')[0] : draft.resolution,
      ratio:draft.resolution.split('|')[1] || '', duration:Number(draft.duration), steps:Number(draft.steps), cloud:draft.engine !== 'local'});
    runtime.currentJob = pending.id;
    showTaskInPreview({...pending, pendingSubmission:true});
    qs('.workbench-subtitle').textContent = '任务已加入队列，可继续编辑并添加任务';
  } catch (error) { setPreview('提交失败', error.message || String(error)); }
  finally { runtime.submitting = false; button.disabled = false; }
}

function taskVideoName(job) {
  const candidates = [job.filename, job.localPath?.split(/[\\/]/).pop(), String(job.output || '').split('/').pop(), job.id];
  for (const candidate of candidates) {
    let name = String(candidate || '').replace(/^file:/, '').split(/[?#]/)[0];
    try { name = decodeURIComponent(name); } catch { /* 保留原文件名 */ }
    if (/\.(?:mp4|mov|webm|mkv)$/i.test(name)) return name;
  }
  return '';
}


let taskStateReady = false;
let taskHistoryGeneration = 0;
const resultSaves = new ResultSaveQueue(2);
const remoteArchive = new RemoteTaskArchive(invoke, async (base,path) => {
  const response = await fetch(base+path,{signal:AbortSignal.timeout(12000)});
  if(!response.ok) throw new Error(`节点请求失败 HTTP ${response.status}`);
  return response.json();
}, () => {
  if(!taskStateReady)return;
  runtime.taskSignature='';renderCachedTasks();
  if(runtime.page==='works')loadWorks({force:true,background:true}).catch(()=>{});
}, resultSaves);
window.addEventListener('task-persistence-error', event => {qs('.workbench-subtitle').textContent=`任务记录保存失败：${event.detail}。请暂时保持工作台运行。`;});
function cachedTaskItems(){
  const rows=[...visiblePendingTasks().filter(row=>!row.clone&&(!isPlatform()||row.cloud)),...savedCloudTasks(true),...(!isPlatform()?[...remoteArchive.rows().filter(row=>row.kind!=='image'),...(runtime.visibleLocalTasks||[])]:[])];
  return [...new Map(rows.map(row=>[row.archiveKey||row.id,row])).values()].sort((a,b)=>(b.created_at||0)-(a.created_at||0)).slice(0,100);
}
function renderCachedTasks(){renderTaskItems(cachedTaskItems());}
function scheduleCloudSave(row){
  if(!cloudTaskVisible(row)||row.status!=='done'||!row.output||row.localPath)return;
  resultSaves.add(`cloud:${row.provider}:${row.id}`,async()=>{
    try {
      const path=await invoke('cloud_h3_download',{taskId:row.id,url:row.output,filename:row.filename||null});
      const latest=savedCloudTasks(true).find(item=>item.id===row.id)||row;
      saveCloudTask({...latest,localPath:path,filename:path.split(/[\\/]/).pop(),archiveError:'',message:'已自动保存到本机'});
      await flushCloudTasks();runtime.taskSignature='';renderCachedTasks();sendCloneState();
      if(runtime.page==='works')loadWorks({force:true,background:true}).catch(()=>{});
    }catch(error){saveCloudTask({...row,archiveError:`成片已生成，自动保存暂未完成：${cloudErrorText(error)}`,message:`成片已生成，自动保存暂未完成：${cloudErrorText(error)}`});throw error;}
  });
}
async function refreshPersonalIdentity(){
  if(window.MinimaxBrowser)return;
  const result=await invoke('cloud_h3_identity').catch(()=>({scope:''}));
  sessionStorage.setItem('minimax-personal-cloud-scope',result.scope||'');
}
function visiblePendingTasks(){return submissionQueue.rows().filter(row=>!row.cloud || cloudTaskVisible(row));}
function filterVisibleWorks(works){
  const known=new Map();
  for(const task of allCloudTasks())if(task.filename){known.set(task.filename,task);known.set(task.filename.replace(/\.mp4$/i,'-2x.mp4'),task);}
  return works.filter(work=>{const task=known.get(work.filename);return task?cloudTaskVisible(task):workVisible(work);});
}
window.addEventListener('platform-identity-changed',()=>{
  worksRequest.cancel();runtime.worksLoading=false;runtime.worksSignature='';runtime.taskSignature='';
  runtime.currentJob='';runtime.previewJobId='';runtime.previewWork=null;runtime.selectedWork=null;
  closePlayer();closeTaskMenu();qs('.prompt-modal').classList.remove('open');qs('.prompt-dialog p').textContent='';
  setPreview('Minimax H3 导演工作台','请选择当前账号的任务');
  const grid=qs('.works-grid');thumbnails.release(grid);grid.replaceChildren();runtime.worksTotal=0;
  qs('.works-status').textContent='正在读取当前账号的作品';
  // 清除旧账户在其他区域的预览、提示词和统计，异步旧请求也由身份检查阻止回填。
  clearSelectedWork();renderPagination();
  qs('.clone-center-frame')?.contentWindow?.postMessage({type:'cloud-h3-account-changed'},'*');
  sendCloneState();renderCachedTasks();submissionQueue.drain();
  loadTasks().catch(()=>{});
  if(runtime.page==='works')loadWorks({force:true}).catch(()=>{});
});

async function loadTasks() {
  renderCachedTasks();
  if (!taskStateReady) return;
  if (runtime.tasksLoading) return;
  runtime.tasksLoading = true;
  const historyGeneration = taskHistoryGeneration;
  const source = API, engine = runtime.settings?.generation_engine;
  let data;
  try {
    const ownerId=platformApi.userId();
    if (ownerId) {
      const serverJobs = await platformApi.request('/jobs').catch(() => []);
      for (const job of (platformApi.userId()===ownerId ? serverJobs : [])) {
        if (savedCloudTasks(true).some(row => row.id === job.id)) continue;
        saveCloudTask(normalizeCloudTask({task:{...job,content:{url:job.result_url||''}}},
          {id:job.id,cloud:true,provider:'platform',ownerId}));
      }
    }
    await refreshPersonalIdentity();
    const rows = savedCloudTasks(true);
    const remoteActive = rows.filter(row => cloudTaskNeedsRefresh(row) ||
      (row.cloneRequest?.upscale && row.status === 'done' && !upscaleRecord(row.id)));
    await Promise.all(remoteActive.map(async row => {
      try {
        const result = await queryCloud(row);
        const next = normalizeCloudTask(result, row);
        scheduleCloudSave(next);
        saveCloudTask(next);renderCachedTasks();
        if (cloudTaskVisible(next) && next.cloneRequest?.upscale && next.status === 'done' && next.output && !upscaleRecord(next.id)) {
          queueMicrotask(() => upscaleWork(next).catch(notifyError));
        }
      } catch (error) { saveCloudTask({...row,message:cloudErrorText(error),query_error_raw:String(error)}); }
    }));
    sendCloneState();
    let localItems = null;
    if (isNode() || runtime.serviceStatus?.running) {
      try {
        const activeData = await api("/api/jobs");
        const activeItems = activeData.items || [];
        const activeSignature = JSON.stringify(activeItems.map(job => [job.id,job.status]));
        if (activeSignature !== runtime.activeSignature || Date.now() - runtime.tasksLoadedAt > 30000) {
          const history = await api("/api/job-history?limit=100&order=desc");
          runtime.taskItems = history.items || [];
          runtime.tasksLoadedAt = Date.now();
          runtime.activeSignature = activeSignature;
        }
        const activeIds = new Set(activeItems.map(job => job.id));
        localItems = [...activeItems,...runtime.taskItems.filter(job => !activeIds.has(job.id) && !['queued','running'].includes(job.status))];
      } catch { runtime.activeSignature = ''; }
    }
    if (!localItems && !isNode()) {
      try {
        const offline = await invoke('list_local_works', {offset:0,limit:100,order:'desc',query:''});
        localItems = (offline.items || []).filter(work => !work.cloud && !work.remoteNode && !work.isUpscaleVariant && !work.filename?.endsWith('-2x.mp4'))
          .map(work => ({...work,status:'done',output_available:true}));
      } catch { localItems = []; }
    }
    if (!localItems) localItems = [];
    if (historyGeneration !== taskHistoryGeneration || source !== API || engine !== runtime.settings?.generation_engine) return;
    runtime.visibleLocalTasks = localItems.filter(row=>!isNode() || !remoteArchive.rows(true).some(saved=>saved.id===row.id&&saved.sourceBase===API));
    data = {items:cachedTaskItems()};
  } finally {
    runtime.tasksLoading = false;
  }
  renderTaskItems(data.items || []);
}

function renderTaskItems(items) {
  updateCurrentTask(items);
  const completedActive = items.some(job => runtime.activeJobIds.has(job.id) && (job.status === 'done' || job.status === 'succeeded'));
  if (completedActive) {
    runtime.worksLoadedAt = 0;
    if (runtime.page === 'works') loadWorks({force:true,background:true}).catch(() => {});
  }
  runtime.activeJobIds = new Set(items.filter((job) => job.cloud ? cloudTaskNeedsRefresh(job) : ["queued", "running"].includes(job.status)).map((job) => job.id));

  const signature = JSON.stringify(items.map((job) => [job.id, job.status, job.output, job.localPath, job.archiveError, job.output_available, job.mode, job.resolution, job.duration, job.steps, job.seed, job.elapsed_seconds, job.message, job.resolution_adjustment, job.prompt, upscaleRecord(job.id)]));
  const active = items.filter((job) => job.cloud ? !['done','failed','error','canceled','cancelled'].includes(job.status) : ["queued", "running"].includes(job.status)).length;
  if (!["overview", "workbench"].includes(runtime.page)) return;
  qs(".task-count").textContent = active ? String(active) : "";
  qs(".task-summary span").textContent = `最近 100 条 · ${active} 个活动任务`;
  if (signature === runtime.taskSignature) return;
  runtime.taskSignature = signature;
  const list = qs(".task-list");
  const previous = new Map([...list.children].map((card) => [card.dataset.jobId, card]));
  const cards = [];
  for (const job of (runtime.page === "workbench" ? items : [])) {
    const cardSignature = JSON.stringify([job.id, job.status, job.output, job.localPath, job.archiveError, job.output_available, job.mode, job.resolution, job.duration, job.steps, job.seed, job.elapsed_seconds, job.message, job.resolution_adjustment, job.prompt, upscaleRecord(job.id)]);
    const old = previous.get(job.id);
    if (old?.dataset.signature === cardSignature) { cards.push(old); previous.delete(job.id); continue; }
    const card = document.createElement("article");
    const outputAvailable = Boolean(job.output) && job.output_available !== false;
    const selectable = job.pendingSubmission || outputAvailable || (job.cloud && ['failed','error'].includes(job.status)) || (job.cloud ? cloudTaskNeedsRefresh(job) : ["queued", "running"].includes(job.status)) || (job.status === "done" && Boolean(job.output));
    card.className = `task-card${job.status === "canceled" ? " is-cancelled" : ""}${selectable ? " can-preview" : ""}`;
    card.dataset.jobId = job.id;
    card.dataset.signature = cardSignature;
    if (selectable) {
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `在中心区域查看任务 ${job.id.replace(/^prepare-/, '').slice(0, 8)}`);
      card.classList.toggle("selected", runtime.previewJobId === job.id);
      card.addEventListener("click", (event) => {
        if (!event.target.closest("button")) {
          showTaskInPreview(job);
        }
      });
      card.addEventListener("keydown", (event) => {
        if (event.target !== card || !["Enter", " "].includes(event.key)) return;
        event.preventDefault();
        showTaskInPreview(job);
      });
    }
    const thumbnail = document.createElement("div");
    thumbnail.className = "task-thumbnail";
    if (outputAvailable) {
      thumbnail.append(createThumbnail(job));
    } else if (job.status === "done" && job.output) {
      thumbnail.textContent = "文件缺失";
    } else {
      thumbnail.textContent = job.status === "running" ? "生成中" : job.status === "queued" ? "排队" : "--";
    }
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    const videoName = taskVideoName(job);
    title.textContent = videoName || `任务 ${job.id.replace(/^prepare-/, '').slice(0, 8)}`;
    const meta = document.createElement("div");
    meta.className = "task-meta";
    const elapsed = formatElapsedSeconds(job.elapsed_seconds);
    meta.title = ["failed","error"].includes(job.status) ? "点击任务查看失败原因" : job.resolution_adjustment || "";
    meta.textContent = job.cloud ? `${taskStatusLabel(job)} · 优云智算 H3 · ${job.resolution} · ${job.ratio} · ${displayVideoDuration(job.duration)}秒${['failed','error'].includes(job.status) ? ' · 点击查看失败原因' : job.message ? ` · ${job.message}` : ''}` : `${taskStatusLabel(job)} · ${job.mode === "references" ? "多参考" : "首尾帧"} · ${job.resolution || "-"} · ${displayVideoDuration(job.duration)}秒 · ${job.steps || "-"} Steps${job.resolution_adjustment ? " · 显存保护已降档" : ""}${elapsed ? ` · 耗时 ${elapsed}` : ""}`;
    if(job.remoteNode)meta.textContent += ` · 远程节点 · ${job.localPath?'已自动保存本机':job.archiveError?'保存待重试':job.status==='done'?'正在自动保存':'任务已保存'}${job.archiveError?` · ${job.archiveError}`:''}`;
    copy.append(title, meta);
    if (job.status === 'done' && outputAvailable) {
      const variants = document.createElement('div');
      variants.className = 'task-variants';
      const original = document.createElement('button');
      original.type = 'button'; original.textContent = '原片';
      original.addEventListener('click', event => { event.stopPropagation(); playTaskInPreview(job); });
      variants.append(original);
      const record = upscaleRecord(job.id);
      if (record?.status === 'done') {
        const upscaled = document.createElement('button');
        upscaled.type = 'button'; upscaled.textContent = '2× 超分';
        upscaled.addEventListener('click', event => { event.stopPropagation(); playTaskInPreview({...job,output:record.output,localPath:record.path,cloud:false,resolution:`${job.resolution} · 2×`}); });
        variants.append(upscaled);
      } else if (record?.status === 'running') {
        const pending = document.createElement('span'); pending.textContent = '超分中…'; variants.append(pending);
      } else {
        const create = document.createElement('button');
        create.type = 'button'; create.textContent = '生成 2× 超分';
        create.addEventListener('click', event => { event.stopPropagation(); upscaleWork(job).catch(notifyError); });
        variants.append(create);
      }
      copy.append(variants);
    }
    const action = document.createElement("button");
    action.className = "task-menu-trigger";
    action.textContent = "⋯";
    action.title = "任务菜单";
    action.setAttribute("aria-label", `打开任务 ${job.id.replace(/^prepare-/, '').slice(0, 8)} 的菜单`);
    action.setAttribute("aria-haspopup", "menu");
    action.setAttribute("aria-expanded", "false");
    action.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openTaskMenu(job, action);
    });
    card.append(thumbnail, copy, action);
    cards.push(card);
  }
  if (runtime.page === "workbench") {
    for (const card of previous.values()) { thumbnails.release(card); card.remove(); }
    // insertBefore 只移动顺序有变化的卡片，保留焦点和已加载缩略图。
    cards.forEach((card, index) => { if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null); });
  }
  const activity = qs(".activity");
  qs('.activity-empty').hidden = items.length > 0;
  qs('.activity-empty').textContent = '暂无任务';
  thumbnails.release(activity);
  qsa(".activity-row").forEach((row) => row.remove());
  for (const job of items.slice(0, 3)) {
    const row = document.createElement("div");
    row.className = "activity-row";
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `查看任务 ${taskVideoName(job) || job.id.replace(/^prepare-/, '').slice(0, 8)}${job.status === 'done' ? ' 并播放作品' : ' 的状态'}`);
    row.addEventListener("click", () => openRecentTask(job).catch(notifyError));
    row.addEventListener("keydown", event => {
      if (event.target !== row || !["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      openRecentTask(job).catch(notifyError);
    });
    const outputAvailable = Boolean(job.output) && job.output_available !== false;
    const missingOutput = job.status === "done" && job.output && !outputAvailable;
    const status = missingOutput ? "文件缺失" : job.status === "done" ? "已完成" : job.status === "running" ? "生成中" : job.status === "queued" ? "排队中" : job.status === 'canceled' ? '已取消' : "失败";
    const thumbnail = document.createElement("div");
    thumbnail.className = "activity-thumb";
    if (outputAvailable) thumbnail.append(createThumbnail(job));
    else {
      const label = document.createElement("span");
      label.className = "activity-thumb-label";
      label.textContent = missingOutput ? "文件缺失" : job.status === "running" ? "生成中" : "任务";
      thumbnail.append(label);
    }
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    const videoName = taskVideoName(job);
    title.textContent = videoName || `任务 ${job.id.replace(/^prepare-/, '').slice(0, 8)}`;
    const meta = document.createElement("small");
    meta.textContent = `${job.resolution || "-"} · ${displayVideoDuration(job.duration)} 秒 · ${job.steps || "-"} steps`;
    copy.append(title, meta);
    const badge = document.createElement("span");
    badge.className = `badge${missingOutput ? " is-missing" : ""}`;
    badge.textContent = status;
    row.append(thumbnail, copy, badge);
    activity.append(row);
  }
}

async function loadTodayCompleted() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const data = await api(`/api/job-stats?since=${start.getTime() / 1000}`);
  qsa(".metric strong")[2].textContent = String(data.completed);
}

let currentTaskTimer = null;
function renderCurrentTaskTimer() {
  if (!currentTaskTimer) return;
  const seconds = Math.max(0, Math.floor(currentTaskTimer.seconds + (Date.now() - currentTaskTimer.sampledAt) / 1000));
  const hours = String(Math.floor(seconds / 3600)).padStart(2, '0');
  const minutes = String(Math.floor(seconds / 60) % 60).padStart(2, '0');
  const remainder = String(seconds % 60).padStart(2, '0');
  qs('.queue-main strong').textContent = `运行中 · ${hours}:${minutes}:${remainder}`;
}
function updateCurrentTask(items) {
  const active = items.find(job => job.status === 'running') || items.find(job => job.status === 'queued');
  currentTaskTimer = active?.status === 'running' ? {
    id: active.id,
    seconds: active.run_seconds != null ? Number(active.run_seconds) : active.running_at
      ? Math.max(0, Date.now() / 1000 - Number(active.running_at))
      : currentTaskTimer?.id === active.id ? currentTaskTimer.seconds + (Date.now() - currentTaskTimer.sampledAt) / 1000 : 0,
    sampledAt: Date.now(),
  } : null;
  const detail = active ? `${active.message || ''}${active.status === 'queued' ? ` · 前方 ${active.jobs_ahead ?? 0} 个任务` : ''}` : '暂无活动任务';
  qs('.queue-main div span').textContent = detail;
  qs('.queue-main > span').textContent = `队列 ${items.filter(job => job.status === 'queued').length}`;
  renderCurrentTaskTimer();
  const job = items.find(job => job.id === runtime.previewJobId);
  if (!job || runtime.page !== 'workbench' || runtime.submitting) return;
  if (job.output && job.output_available !== false && !qs('.preview-media').hidden) return;
  showTaskInPreview(job);
}

function showTaskInPreview(job) {
  if (job.status === 'done' && job.output && job.output_available !== false) {
    playTaskInPreview(job);
  } else if (job.status === 'done' && job.output) {
    setPreview('视频文件已不存在', '该任务已完成，但历史输出文件已被移除，无法播放。任务参数和提示词仍会保留。');
  } else {
    const titles = {queued: '任务排队中', running: '正在生成', failed: '生成失败', canceled: '任务已取消', error: '生成失败'};
    const loading = job.cloud ? !['failed','error','canceled'].includes(job.status) && cloudTaskNeedsRefresh(job) : ["queued", "running"].includes(job.status);
    setPreview(
      job.phase === 'prompt' && job.status === 'running' ? '准备提示词' : job.cloud && job.status === 'done' && !job.output ? '正在获取视频地址' : titles[job.status] || job.status,
      job.error || `${job.message || ''}${job.run_seconds != null ? ` · 已用${formatUsedSeconds(job.run_seconds)}` : ''}`,
      job.sampling_timing?.total_steps > 0 ? 100 * job.sampling_timing.completed_steps / job.sampling_timing.total_steps : 0,
      { loading, jobId: job.id, job }
    );
  }
}

const DRAFT_KEY = 'minimax-workbench-draft';
function clearCreationInput() {
  qs('.work-prompt').value = '';
  qs('.compiled-prompt').value = '';
  Object.assign(compileState, {fingerprint:'', plan:null, optimized:false});
  assetPicker.clear();
  qs('.work-prompt').dispatchEvent(new Event('input', {bubbles:true}));
  // 即使服务尚未连接，也覆盖旧草稿，避免稍后配置返回又恢复旧输入。
  runtime.draftRestored = true;
  saveDraft();
  qs('.input-save-state').textContent = '输入已清空';
  qs('.work-prompt').focus();
}
qs('.clear-creation-input').addEventListener('click', clearCreationInput);
function saveDraft() {
  if (!runtime.draftRestored) return;
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({prompt: qs('.work-prompt').value, compiledPrompt:qs('.compiled-prompt').value, mode: runtime.mode,
      resolution: qs('.resolution-select').value, duration: qs('.duration-input').value,
      steps: qs('.steps-input').value, seed: qs('.seed-input').value}));
    qs('.input-save-state').textContent = '文本参数已保存';
    qs('.input-save-state').title = '参考文件仅在本次窗口内保留，重新打开后需重新选择';
  } catch { qs('.input-save-state').textContent = '草稿保存失败'; }
}
function restoreDraft() {
  if (runtime.draftRestored) return;
  runtime.draftRestored = true;
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
    if (!draft) return;
    assetPicker.setMode(draft.mode);
    qs('.work-prompt').value = draft.prompt || '';
    qs('.compiled-prompt').value = draft.compiledPrompt || '';
    for (const key of ['resolution', 'duration', 'steps', 'seed']) {
      const input = qs(key === 'resolution' ? '.resolution-select' : `.${key}-input`);
      if (draft[key] != null && !input.disabled && (key !== 'resolution' || [...input.options].some(option => option.value === draft[key]))) input.value = draft[key];
    }
    qs('.work-prompt').dispatchEvent(new Event('input'));
    qs('.input-save-state').textContent = '草稿已恢复';
    qs('.workbench-subtitle').textContent = '已恢复文本和参数，参考文件需重新选择';
  } catch { qs('.input-save-state').textContent = '草稿读取失败'; }
}

function createThumbnail(work) {
  if (isImageWork(work)) {
    const image = document.createElement('img');
    image.alt = work.filename || '图片作品'; image.decoding = 'async';
    image.dataset.src = mediaUrl(work); thumbnails.observe(image); return image;
  }
  if (work.cloud && !work.localPath) {
    const video = document.createElement('video');
    video.src = work.output; video.muted = true; video.playsInline = true; video.preload = 'metadata';
    return video;
  }
  if (work.externalUrl && !work.localPath) {
    const video = document.createElement("video");
    video.src = work.externalUrl;
    video.muted = true; video.playsInline = true; video.preload = "metadata";
    return video;
  }
  if (work.localPath) {
    const video = document.createElement("video");
    video.src = localWorkUrl(work);
    video.muted = true; video.playsInline = true; video.preload = "metadata";
    return video;
  }
  const image = document.createElement("img");
  image.alt = work.filename || `任务 ${String(work.id).slice(0, 8)}`;
  image.decoding = "async";
  const filename = work.filename || decodeURIComponent(work.output.split("/").at(-1));
  image.dataset.src = isImageWork(work) ? `${API}${work.output}` : `${API}/api/work-thumbnail/${encodeURIComponent(filename)}?v=${encodeURIComponent(work.thumbnail_version || work.completed_at || work.created_at || 0)}`;
  thumbnails.observe(image);
  return image;
}

function localWorkUrl(work) {
  const convertFileSrc = window.__TAURI__?.core?.convertFileSrc;
  if (work.localPath && typeof convertFileSrc === "function") return convertFileSrc(work.localPath);
  return `${API}${work.output || ""}`;
}

function releaseMedia(media) {
  if (!(media instanceof HTMLVideoElement)) return;
  media.pause();
  media.removeAttribute("src");
  media.load();
}

function isImageWork(work) {
  return work.media_type === 'image' || /\.(?:avif|gif|jpe?g|png|webp)$/i.test(work.filename || work.output || "");
}

function createWorkMedia(work) {
  const element = document.createElement(isImageWork(work) ? "img" : "video");
  element.src = mediaUrl(work);
  element.setAttribute("aria-label", work.filename || "作品预览");
  if (element instanceof HTMLVideoElement) {
    element.preload = "metadata";
    element.muted = true;
    element.playsInline = true;
  } else {
    element.alt = work.filename || "作品预览";
    element.loading = "lazy";
  }
  return element;
}

function formatMediaTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function updatePlayerState() {
  const media = runtime.playerMedia;
  const toggle = qs(".player-toggle");
  const stageToggle = qs(".player-stage-toggle");
  const progress = qs(".player-progress");
  const time = qs(".player-time");
  if (!(media instanceof HTMLVideoElement)) {
    qs(".player-controls").classList.add("image-mode");
    toggle.hidden = true;
    stageToggle.hidden = true;
    progress.hidden = true;
    time.textContent = "图片";
    return;
  }
  qs(".player-controls").classList.remove("image-mode");
  toggle.hidden = false;
  stageToggle.hidden = false;
  progress.hidden = false;
  const paused = media.paused;
  toggle.textContent = paused ? "▶" : "Ⅱ";
  toggle.setAttribute("aria-label", paused ? "播放" : "暂停");
  stageToggle.textContent = paused ? "▶" : "Ⅱ";
  const duration = Number.isFinite(media.duration) ? media.duration : 0;
  progress.value = duration ? String((media.currentTime / duration) * 100) : "0";
  time.textContent = `${formatMediaTime(media.currentTime)} / ${formatMediaTime(duration)}`;
}

function fitPlayerToMedia() {
  const media = runtime.playerMedia;
  if (!media) return;
  const sourceWidth = media instanceof HTMLVideoElement ? media.videoWidth : media.naturalWidth;
  const sourceHeight = media instanceof HTMLVideoElement ? media.videoHeight : media.naturalHeight;
  if (!sourceWidth || !sourceHeight) return;
  const ratio = sourceWidth / sourceHeight;
  const maxWidth = Math.min(1080, window.innerWidth * 0.92);
  const maxHeight = Math.max(240, window.innerHeight - 150);
  const width = Math.min(maxWidth, maxHeight * ratio);
  qs(".fullscreen-player").style.width = `${Math.round(width)}px`;
  qs(".fullscreen-stage").style.aspectRatio = `${sourceWidth} / ${sourceHeight}`;
}

async function togglePlayerPlayback() {
  const media = runtime.playerMedia;
  if (!(media instanceof HTMLVideoElement)) return;
  if (media.paused) await media.play();
  else media.pause();
  updatePlayerState();
}

function openPlayer(work = runtime.selectedWork) {
  if (!work) return;
  const modal = qs(".player-modal");
  const stage = qs(".fullscreen-stage");
  releaseMedia(runtime.playerMedia);
  const media = createWorkMedia(work);
  resetMediaView(stage);
  stage.replaceChildren(media, qs(".player-stage-toggle"));
  runtime.playerMedia = media;
  qs(".player-title").textContent = work.filename || `作品 ${String(work.id).slice(0, 8)}`;
  modal.classList.add("open");
  if (media instanceof HTMLVideoElement) {
    media.muted = false;
    media.addEventListener("loadedmetadata", () => {
      fitPlayerToMedia();
      updatePlayerState();
    });
    media.addEventListener("timeupdate", updatePlayerState);
    media.addEventListener("play", updatePlayerState);
    media.addEventListener("pause", updatePlayerState);
    media.addEventListener("ended", updatePlayerState);
  } else {
    media.addEventListener("load", fitPlayerToMedia);
  }
  if ((media instanceof HTMLVideoElement && media.readyState >= 1) || (media instanceof HTMLImageElement && media.complete)) fitPlayerToMedia();
  updatePlayerState();
}

function closePlayer() {
  const media = runtime.playerMedia;
  releaseMedia(media);
  runtime.playerMedia = null;
  qs(".player-modal").classList.remove("open");
  const stageToggle = qs(".player-stage-toggle");
  const stage = qs(".fullscreen-stage");
  resetMediaView(stage);
  stage.style.aspectRatio = "";
  qs(".fullscreen-player").style.width = "";
  stage.replaceChildren(stageToggle);
}

function selectWork(work, card) {
  const unchanged = runtime.selectedWork?.id === work.id;
  runtime.selectedWork = work;
  qsa(".work-card").forEach((item) => item.classList.toggle("selected", item === card));
  const preview = qs(".large-preview");
  if (!unchanged) { thumbnails.release(preview); preview.replaceChildren(createThumbnail(work)); }
  qs(".inspector-title").textContent = work.filename || `作品 ${String(work.id).slice(0, 8)}`;
  qs(".inspector-subtitle").textContent = isImageWork(work) ? "点击画面可全屏查看图片" : "点击画面可全屏播放";
  qs('[data-work-detail="id"]').textContent = work.id || "--";
  qs('[data-work-detail="spec"]').textContent = isImageWork(work) ? `${work.resolution || '--'} · 图片` : `${work.resolution || "--"} · ${work.duration || "--"} 秒`;
  qs('[data-work-detail="mode"]').textContent = work.mode || "--";
  qs('[data-work-detail="elapsed"]').textContent = formatElapsedSeconds(work.elapsed_seconds) || "--";
}

function resetMediaView(viewport) {
  if (!viewport) return;
  const media = viewport.querySelector("video, img");
  if (media) media.style.transform = "";
  runtime.mediaViews.delete(viewport);
  viewport.classList.remove("is-zoomed", "is-panning");
}

function applyMediaView(viewport) {
  const media = viewport.querySelector("video:not([hidden]), img:not([hidden])");
  const view = runtime.mediaViews.get(viewport);
  if (!media || !view) return;
  media.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  viewport.classList.toggle("is-zoomed", view.scale > 1 || view.x !== 0 || view.y !== 0);
}

function bindMediaViewport(viewport) {
  runtime.mediaViews.set(viewport, { scale: 1, x: 0, y: 0 });
  viewport.addEventListener("wheel", (event) => {
    const media = viewport.querySelector("video:not([hidden]), img:not([hidden])");
    if (!media) return;
    event.preventDefault();
    const view = runtime.mediaViews.get(viewport) || { scale: 1, x: 0, y: 0 };
    const nextScale = Math.max(1, Math.min(5, view.scale * (event.deltaY < 0 ? 1.15 : 1 / 1.15)));
    const bounds = viewport.getBoundingClientRect();
    const px = event.clientX - bounds.left - bounds.width / 2;
    const py = event.clientY - bounds.top - bounds.height / 2;
    view.x = px - ((px - view.x) * nextScale) / view.scale;
    view.y = py - ((py - view.y) * nextScale) / view.scale;
    view.scale = nextScale;
    if (nextScale === 1) view.x = view.y = 0;
    runtime.mediaViews.set(viewport, view);
    applyMediaView(viewport);
  }, { passive: false });
  viewport.addEventListener("mousedown", (event) => {
    if (event.button !== 1 || !viewport.querySelector("video:not([hidden]), img:not([hidden])")) return;
    event.preventDefault();
    const view = runtime.mediaViews.get(viewport) || { scale: 1, x: 0, y: 0 };
    const origin = { clientX: event.clientX, clientY: event.clientY, x: view.x, y: view.y };
    viewport.classList.add("is-panning");
    const move = (moveEvent) => {
      view.x = origin.x + moveEvent.clientX - origin.clientX;
      view.y = origin.y + moveEvent.clientY - origin.clientY;
      runtime.mediaViews.set(viewport, view);
      applyMediaView(viewport);
    };
    const stop = () => {
      viewport.classList.remove("is-panning");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", stop);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop, { once: true });
  });
  viewport.addEventListener("auxclick", (event) => { if (event.button === 1) event.preventDefault(); });
}

function activePlaybackVideo() {
  if (qs(".player-modal").classList.contains("open") && runtime.playerMedia instanceof HTMLVideoElement) return runtime.playerMedia;
  const preview = qs(".preview-media");
  return runtime.page === "workbench" && !preview.hidden ? preview : null;
}

async function toggleActivePlayback() {
  const media = activePlaybackVideo();
  if (!media) return;
  if (media.paused) await media.play();
  else media.pause();
  if (media === runtime.playerMedia) updatePlayerState();
}

async function editWork(work) {
  if (!work) return;
  if (isImageWork(work)) {
    if (!await generationServiceAvailable()) return;
    runtime.imageEditId = work.id;
    runtime.imageEditSent = null;
    showPage('image');
    return;
  }
  if (work.pendingSubmission) {
    const draft = submissionQueue.snapshot(work.id);
    if (!draft) throw new Error('任务快照已不存在');
    [...assetPicker.references, ...Object.values(assetPicker.frames)].forEach(item => assetPicker.dispose(item));
    assetPicker.references = []; assetPicker.frames = {first_frame:null,last_frame:null}; assetPicker.previousEntries = [];
    assetPicker.setMode(draft.mode);
    if (draft.mode === 'references') assetPicker.addFiles(draft.references.map(item => item.file));
    else for (const [role, item] of Object.entries(draft.frames)) if (item) assetPicker.addFiles([item.file], {mode:draft.mode,role});
    qs('.work-prompt').value = draft.brief; qs('.compiled-prompt').value = '';
    qs('.resolution-select').value = isCloud() && draft.engine === 'local' ? mapLocalResolution(draft.resolution) : draft.resolution;
    qs('.duration-input').value = draft.duration; qs('.steps-input').value = draft.steps; qs('.seed-input').value = draft.seed;
    Object.assign(compileState, {fingerprint:'',plan:null,optimized:false});
    qs('.work-prompt').dispatchEvent(new Event('input'));
    showPage('workbench'); updateSpecs(); saveDraft();
    qs('.workbench-subtitle').textContent = '已恢复任务需求和参考素材，可编辑后重新加入队列';
    return;
  }
  if (work.cloud) {
    qs('.work-prompt').value = work.userBrief || work.prompt || '';
    qs('.compiled-prompt').value = work.userBrief ? work.prompt || '' : '';
    compileState.fingerprint = ''; compileState.plan = work.promptPlan || null;
    qs('.resolution-select').value = `${work.resolution}|${work.ratio || '16:9'}`;
    qs('.duration-input').value = work.duration || 5;
    qs('.work-prompt').dispatchEvent(new Event('input'));
    updateCompiledState();
    showPage('workbench'); updateSpecs();
    qs('.workbench-subtitle').textContent = '云端任务参数已载入；参考素材需重新选择';
    return;
  }
  await loadConfig();
  await assetPicker.restoreWork(work);
  qs(".work-prompt").value = work.user_brief || work.prompt || "";
  qs('.compiled-prompt').value = work.user_brief ? work.prompt || '' : '';
  compileState.fingerprint = ''; compileState.plan = work.prompt_plan || null;
  if (isCloud()) qs('.resolution-select').value = mapLocalResolution(work.resolution);
  else if (runtime.config?.resolutions.includes(work.resolution)) qs('.resolution-select').value = work.resolution;
  if (work.duration != null) qs(".duration-input").value = work.duration;
  if (work.steps != null && !qs('.steps-input').disabled) qs(".steps-input").value = work.steps;
  qs(".seed-input").value = work.seed ?? "";
  qs('.work-prompt').dispatchEvent(new Event('input'));
  updateCompiledState();
  showPage("workbench");
  updateSpecs();
}

async function editSelectedWork() {
  await editWork(runtime.selectedWork);
}

function taskStatusLabel(job) {
  if (job.phase === 'prompt' && job.status === 'running') return '准备提示词';
  if (job.status === "done" && job.output && job.output_available === false) return "文件缺失";
  return ({ queued: "排队中", running: "生成中", done: "已完成", failed: "失败", error: "失败", canceled: "已取消", cancelled: "已取消" })[job.status] || job.status || "未知";
}

function closeTaskMenu() {
  const layer = qs(".task-menu-layer");
  if (!layer) return;
  layer.hidden = true;
  layer.innerHTML = "";
  qsa(".task-menu-trigger[aria-expanded='true']").forEach((button) => button.setAttribute("aria-expanded", "false"));
}

function positionTaskMenu(trigger) {
  const panel = qs(".task-menu-panel");
  if (!panel) return;
  const triggerRect = trigger.getBoundingClientRect();
  const panelRect = panel.getBoundingClientRect();
  const gap = 5;
  const left = Math.max(8, Math.min(window.innerWidth - panelRect.width - 8, triggerRect.right - panelRect.width));
  let top = triggerRect.bottom + gap;
  if (top + panelRect.height > window.innerHeight - 8) top = Math.max(8, triggerRect.top - panelRect.height - gap);
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
}

async function deleteTaskVideo(job) {
  if (!job?.output || job.output_available === false) return;
  if(job.remoteNode){if(!window.confirm('删除这条任务已保存到本机的作品？节点原文件将保留。'))return;await deleteLibraryWork(job);runtime.taskSignature='';renderCachedTasks();return;}
  if (job.cloud) {
    if (!window.confirm('删除这条云端任务的本地原片、超分视频和作品记录？')) return;
    if (job.filename) await invoke('delete_unified_work', {filename:job.filename});
    const variant = upscaleRecord(job.id);
    if (variant?.name) await invoke('delete_unified_work', {filename:variant.name});
    removeUpscaleRecord(job.id);
    removeCloudTask(job.id);
    runtime.taskSignature = '';
    await loadTasks();
    return;
  }
  if (!window.confirm("确定删除这个视频吗？视频文件和对应作品记录都会被移除。")) return;
  await api(`/api/works/${encodeURIComponent(job.id)}`, { method: "DELETE" });
  if (runtime.previewJobId === job.id) {
    setPreview("Minimax H3 导演工作台", "开始创造你的想法");
    qs(".preview").classList.add("is-welcome");
  }
  runtime.tasksLoadedAt = 0;
  runtime.taskSignature = "";
  await loadTasks();
}

function openTaskMenu(job, trigger) {
  const layer = qs(".task-menu-layer");
  if (!layer) return;
  const wasOpen = trigger.getAttribute("aria-expanded") === "true";
  closeTaskMenu();
  if (wasOpen) return;
  const outputAvailable = Boolean(job.output) && job.output_available !== false;
  const active = ["queued", "running"].includes(job.status);
  const actions = [
    { label: "重新编辑", run: () => editWork(job) },
    { label: "复制提示词", run: async (button) => { await navigator.clipboard.writeText(job.prompt || ""); button.textContent = "已复制"; } },
    { label: "下载视频", disabled: !outputAvailable, run: () => downloadWork(job) },
    { label: "删除视频", disabled: !outputAvailable, danger: true, run: () => deleteTaskVideo(job) },
  ];
  if (active) {
    actions.push({
      label: "取消任务",
      danger: true,
      run: async () => {
        if (job.pendingSubmission) await submissionQueue.cancel(job.id);
        else if (job.cloud) await cancelCloud(job);
        else if(job.remoteNode)await submissionApi(job.sourceBase,`/api/${job.kind==='clone'?'clone/tasks':job.kind==='image-job'?'image-studio/jobs':'jobs'}/${encodeURIComponent(job.id)}`,{method:'DELETE'});
        else await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: "DELETE" });
        if (runtime.currentJob === job.id) {
          runtime.currentJob = "";
          localStorage.removeItem("minimax-current-job");
        }
        runtime.tasksLoadedAt = 0;
        runtime.taskSignature = "";
        await loadTasks();
      },
    });
  }
  const panel = document.createElement("div");
  panel.className = "task-menu-panel";
  panel.setAttribute("role", "menu");
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.disabled = Boolean(action.disabled);
    button.classList.toggle("danger", Boolean(action.danger));
    button.setAttribute("role", "menuitem");
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (button.disabled) return;
      button.disabled = true;
      try { await action.run(button); }
      catch (error) { notifyError(error); }
      finally { if (button.isConnected) button.disabled = false; }
      if (action.label !== "复制提示词") closeTaskMenu();
    });
    panel.append(button);
  }
  layer.replaceChildren(panel);
  layer.hidden = false;
  trigger.setAttribute("aria-expanded", "true");
  positionTaskMenu(trigger);
  panel.querySelector("button:not(:disabled)")?.focus();
}

function bindWorkViewer() {
  const playerModal = qs(".player-modal");
  const promptModal = qs(".prompt-modal");
  const toggle = () => { void togglePlayerPlayback().catch(() => {}); };

  qs(".large-preview").addEventListener("click", () => openPlayer());
  qs(".player-toggle").addEventListener("click", toggle);
  qs(".player-stage-toggle").addEventListener("click", toggle);
  bindMediaViewport(qs(".fullscreen-stage"));
  bindMediaViewport(qs(".preview"));
  qs(".player-reset-view").addEventListener("click", () => resetMediaView(qs(".fullscreen-stage")));
  qs(".player-download").addEventListener("click", () => {
    if (runtime.selectedWork) downloadWork(runtime.selectedWork).catch(notifyError);
  });
  qs(".player-progress").addEventListener("input", (event) => {
    const media = runtime.playerMedia;
    if (!(media instanceof HTMLVideoElement) || !Number.isFinite(media.duration)) return;
    media.currentTime = media.duration * (Number(event.currentTarget.value) / 100);
    updatePlayerState();
  });
  qs('[aria-label="关闭播放器"]').addEventListener("click", closePlayer);
  qs('[aria-label="关闭提示词"]').addEventListener("click", () => promptModal.classList.remove("open"));
  playerModal.addEventListener("click", (event) => { if (event.target === playerModal) closePlayer(); });
  promptModal.addEventListener("click", (event) => { if (event.target === promptModal) promptModal.classList.remove("open"); });

  qs('[data-inspector-action="download"]').addEventListener("click", () => {
    if (runtime.selectedWork) downloadWork(runtime.selectedWork).catch(notifyError);
  });
  qs('[data-inspector-action="prompt"]').addEventListener("click", () => {
    if (!runtime.selectedWork) return;
    qs(".prompt-dialog p").textContent = runtime.selectedWork.prompt || "此作品没有可用提示词。";
    promptModal.classList.add("open");
  });
  intercept(qs('[data-inspector-action="edit"]'), editSelectedWork);
  qs('[data-inspector-action="delete"]').addEventListener("click", async () => {
    const work = runtime.selectedWork;
    if (!work || !window.confirm("确定删除这个作品吗？")) return;
    if (work.isUpscaleVariant) { await invoke('delete_unified_work', {filename:work.filename}); removeUpscaleRecord(work.sourceId); }
    else if (work.cloud) { await invoke('delete_unified_work', {filename:work.filename}); removeCloudTask(work.id); }
    else await deleteLibraryWork(work);
    clearSelectedWork();
    await loadWorks({ force: true });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeTaskMenu();
      if (playerModal.classList.contains("open")) closePlayer();
      if (promptModal.classList.contains("open")) promptModal.classList.remove("open");
    }
    const target = event.target;
    const editing = target instanceof HTMLElement && (target.matches("input, textarea, select") || target.isContentEditable);
    if (event.code === "Space" && !event.repeat && !editing && runtime.page === "clone" && runtime.cloneResultPlaybackAvailable) {
      event.preventDefault();
      event.stopImmediatePropagation();
      qs('.clone-center-frame')?.contentWindow?.postMessage({type:'clone-toggle-result-playback'}, '*');
      return;
    }
    if (event.code === "Space" && !event.repeat && !editing && activePlaybackVideo()) {
      event.preventDefault();
      event.stopPropagation();
      void toggleActivePlayback().catch(() => {});
    }
  }, true);
  window.addEventListener("resize", () => {
    closeTaskMenu();
    if (playerModal.classList.contains("open")) fitPlayerToMedia();
    fitCenterPreview();
  });
}

function clearSelectedWork() {
  runtime.selectedWork = null;
  const preview = qs(".large-preview");
  thumbnails.release(preview);
  preview.textContent = "选择作品查看详情";
  qs(".inspector-title").textContent = "未选择作品";
  for (const field of qsa("[data-work-detail]")) field.textContent = "--";
}

function renderPagination() {
  const page = Math.floor(runtime.worksOffset / runtime.worksViewSize) + 1;
  const pages = pageCount(runtime.worksTotal, runtime.worksViewSize);
  qs(".works-page-info").textContent = `第 ${page} / ${pages} 页 · 共 ${runtime.worksTotal} 个作品`;
  for (const button of qsa("[data-works-page]")) {
    const backwards = ["first", "previous"].includes(button.dataset.worksPage);
    button.disabled = runtime.worksLoading || runtime.worksFailed || (backwards ? page <= 1 : page >= pages);
  }
  qs(".refresh-works").disabled = runtime.worksLoading;
  qs(".works-grid").setAttribute("aria-busy", String(runtime.worksLoading));
}

function updateWorksSearchClearVisibility() {
  qs(".works-search-clear").hidden = !qs(".works-search").value;
}

function bindWorksPagination() {
  for (const button of qsa('[data-works-type]')) button.addEventListener('click', () => {
    runtime.worksType = button.dataset.worksType;
    qsa('[data-works-type]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    loadWorks({offset:0}).catch(() => {});
  });
  qs(".works-start-service").addEventListener("click", async () => {
    await runServiceCommand("start_service", "启动中");
    if (runtime.serviceStatus?.running && !runtime.worksLoading) loadWorks().catch(() => {});
  });
  qs(".works-service-overview").addEventListener("click", () => showPage("overview"));
  qs(".works-page-size").value = String(runtime.worksSize);
  qs(".works-page-size").addEventListener("change", (event) => {
    runtime.worksSize = Number(event.target.value);
    localStorage.setItem("minimax-works-size", String(runtime.worksSize));
    loadWorks({ offset: 0 }).catch(() => {});
  });
  qs(".works-order").addEventListener("change", (event) => {
    runtime.worksOrder = event.target.value;
    loadWorks({ offset: 0 }).catch(() => {});
  });
  let searchTimer;
  const searchInput = qs(".works-search");
  searchInput.addEventListener("input", (event) => {
    updateWorksSearchClearVisibility();
    clearTimeout(searchTimer);
    worksRequest.cancel();
    runtime.worksLoading = false;
    runtime.worksQuery = event.target.value.trim();
    searchTimer = setTimeout(() => loadWorks({ offset: 0 }).catch(() => {}), 250);
  });
  qs(".works-search-clear").addEventListener("click", () => {
    clearTimeout(searchTimer);
    worksRequest.cancel();
    runtime.worksLoading = false;
    searchInput.value = "";
    runtime.worksQuery = "";
    updateWorksSearchClearVisibility();
    searchInput.focus();
    loadWorks({ offset: 0 }).catch(() => {});
  });
  updateWorksSearchClearVisibility();
  qs(".refresh-works").addEventListener("click", () => loadWorks({ force: true }).catch(() => {}));
  for (const button of qsa("[data-works-page]")) button.addEventListener("click", () => {
    const offsets = { first: 0, previous: Math.max(0, runtime.worksOffset - runtime.worksSize), next: runtime.worksOffset + runtime.worksSize, last: lastOffset(runtime.worksTotal, runtime.worksSize) };
    loadWorks({ offset: offsets[button.dataset.worksPage] }).catch(() => {});
  });
}

function enrichUnifiedWorks(works) {
  const cloudByFile = new Map(savedCloudTasks().filter(task => task.filename).map(task => [task.filename, task]));
  const variantsByFile = new Map(upscaleRecords().filter(record => record.status === 'done' && record.name && (isNode() ? record.nodeBase === API : !record.nodeBase)).map(record => [record.name, record]));
  return works.map(work => {
    const cloud = cloudByFile.get(work.filename);
    if (cloud) return {...work,id:cloud.id,cloud:true,localPath:cloud.localPath || work.localPath,
      prompt:cloud.prompt,userBrief:cloud.userBrief,promptPlan:cloud.promptPlan,
      resolution:cloud.resolution,duration:cloud.duration,mode:cloud.mode,created_at:cloud.created_at};
    const variant = variantsByFile.get(work.filename);
    if (variant) {
      const source = savedCloudTasks().find(task => task.id === variant.sourceId) || runtime.taskItems.find(task => task.id === variant.sourceId);
      return {...work,id:`${variant.sourceId}:2x`,sourceId:variant.sourceId,isUpscaleVariant:true,localPath:variant.path || work.localPath,
        cloud:Boolean(source?.cloud),prompt:source?.prompt || work.prompt || '',duration:source?.duration || work.duration,
        resolution:`${source?.resolution || work.resolution || ''} · 2×`};
    }
    return work;
  });
}

async function openRecentTask(job) {
  if (job.status !== 'done' || (job.output_available === false && !job.localPath) || !(job.output || job.localPath)) {
    showPage('workbench');
    showTaskInPreview(job);
    return;
  }
  let workTask = job;
  if (job.cloud && !job.localPath && /^https?:\/\//.test(job.output || '')) {
    const localPath = await invoke('cloud_h3_download', {taskId:job.id,url:job.output,filename:job.filename || null});
    workTask = {...job,localPath,filename:localPath.split(/[\\/]/).pop()};
    saveCloudTask(workTask);
  }
  const filename = taskVideoName(workTask);
  if (!filename) throw new Error('此任务没有可定位的作品文件');
  runtime.worksQuery = filename;
  runtime.worksType = 'video';
  qsa('[data-works-type]').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.worksType === 'video')));
  runtime.worksTargetOffset = 0;
  qs('.works-search').value = filename;
  updateWorksSearchClearVisibility();
  showPage('works');
  const works = await loadWorks({offset:0,force:true});
  if (runtime.page !== 'works' || !works) return;
  const work = works.find(item => item.id === workTask.id && item.filename === filename)
    || works.find(item => item.filename === filename);
  if (!work) {
    qs('.works-status').textContent = `未找到任务对应的视频：${filename}`;
    return;
  }
  const card = [...qsa('.work-card')].find(item => item.dataset.workId === work.id);
  if (!card) return;
  selectWork(work, card);
  card.scrollIntoView({block:'nearest'});
  openPlayer(work);
  if (runtime.playerMedia instanceof HTMLVideoElement) {
    await runtime.playerMedia.play().catch(() => {});
    updatePlayerState();
  }
}

async function openPlatformRecentTask(jobId) {
  const ownerId=platformApi.userId();
  if(!ownerId)throw new Error('请先登录云端账号');
  let previous=savedCloudTasks(true).find(task=>task.provider==='platform'&&task.id===jobId);
  if(!previous){
    const rows=await platformApi.request('/jobs');
    if(platformApi.userId()!==ownerId)return;
    const raw=rows.find(row=>row.id===jobId);
    if(!raw)throw new Error('当前账号没有该任务');
    previous=normalizeCloudTask({...raw,content:{url:raw.result_url||''}},{id:jobId,cloud:true,provider:'platform',ownerId});
  }
  const result=await platformApi.task(jobId);
  if(platformApi.userId()!==ownerId)return;
  const job=normalizeCloudTask(result,previous);saveCloudTask(job);await openRecentTask(job);
}

async function fileToAssistantVideoFrames(item, tag) {
  const url = URL.createObjectURL(item.file);
  const video = document.createElement('video');
  video.preload = 'auto'; video.muted = true; video.playsInline = true;
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(`视频 ${item.file.name} 解码超时`)), 12000);
      video.onloadedmetadata = () => { clearTimeout(timeout); resolve(); };
      video.onerror = () => { clearTimeout(timeout); reject(new Error(`无法解析视频 ${item.file.name}`)); };
      video.src = url;
    });
    if (!Number.isFinite(video.duration) || video.duration <= 0 || !video.videoWidth) throw new Error(`视频 ${item.file.name} 缺少有效画面`);
    if (video.readyState < 2) await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(`视频 ${item.file.name} 读取首帧超时`)), 10000);
      video.onloadeddata = () => { clearTimeout(timeout); resolve(); };
      video.onerror = () => { clearTimeout(timeout); reject(new Error(`视频 ${item.file.name} 读取首帧失败`)); };
    });
    const fractions = video.duration > 6 ? [0, 0.25, 0.5, 0.75, 1] : [0, 0.5, 1];
    const times = [...new Set(fractions.map(fraction => Number(Math.min(Math.max(0, video.duration - 0.1), video.duration * fraction).toFixed(2))))];
    const scale = Math.min(1, 1024 / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    const frames = [];
    for (const time of times) {
      if (Math.abs(video.currentTime - time) > 0.025 || !video.readyState) {
        await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(new Error(`视频 ${item.file.name} 定位画面超时`)), 10000);
          video.onseeked = () => { clearTimeout(timeout); resolve(); };
          video.onerror = () => { clearTimeout(timeout); reject(new Error(`视频 ${item.file.name} 定位画面失败`)); };
          video.currentTime = time;
        });
      }
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      frames.push({tag, name:item.file.name, timeSeconds:time, dataUrl:canvas.toDataURL('image/jpeg', 0.8)});
    }
    return {frames, duration:video.duration};
  } finally {
    video.removeAttribute('src'); video.load(); URL.revokeObjectURL(url);
  }
}

async function assistantVideoFrames(mode, source = assetPicker) {
  if (mode !== 'references') return {frames:[], durations:{}};
  const manifest = assetManifest(mode, source.references, source.frames);
  const videos = source.references.filter(item => item.video);
  const results = await Promise.all(videos.map(item => fileToAssistantVideoFrames(item, manifest.find(entry => entry.id === item.id).tag)));
  return {frames:results.flatMap(result => result.frames), durations:Object.fromEntries(videos.map((item, index) => [item.id, results[index].duration]))};
}

async function assistantAudioDurations(mode, source = assetPicker) {
  if (mode !== 'references') return {};
  const entries = await Promise.all(source.references.filter(item => item.audio).map(async item => {
    const url = URL.createObjectURL(item.file);
    const audio = document.createElement('audio');
    try {
      const duration = await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('音频元数据超时')), 5000);
        audio.onloadedmetadata = () => { clearTimeout(timeout); resolve(audio.duration); };
        audio.onerror = () => { clearTimeout(timeout); reject(new Error('音频元数据不可读')); };
        audio.src = url;
      });
      return [item.id, Number.isFinite(duration) ? duration : null];
    } catch { return [item.id, null]; }
    finally { audio.removeAttribute('src'); audio.load(); URL.revokeObjectURL(url); }
  }));
  return Object.fromEntries(entries);
}

async function loadWorks({ offset = runtime.worksTargetOffset, force = false, background = false } = {}) {
  if (background && (runtime.worksLoading || runtime.page !== "works")) return;
  const request = worksRequest.begin();
  const grid = qs(".works-grid");
  const status = qs('.works-status');
  runtime.worksLoading = true;
  runtime.worksTargetOffset = offset;
  runtime.worksFailed = false;
  if (!background && !runtime.worksSignature) status.textContent = "正在读取作品…";
  renderPagination();
  const size = runtime.worksSize;
  const query = runtime.worksQuery;
  const order = runtime.worksOrder;
  const mediaType = runtime.worksType;
  try {
    const params = new URLSearchParams({ offset, limit: size, order, query, refresh: String(force), media_type:mediaType });
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(15000)]);
    const service = await invoke("service_status").catch(() => runtime.serviceStatus);
    const offline = !window.MinimaxBrowser && (isPlatform() || isNode() || service?.running === false);
    let data = offline
      ? await invoke("list_local_works", { offset, limit: size, order, query, refresh: force, mediaType })
      : await api(`/api/works/page?${params}`, { signal });
    if (!request.current()) return;
    if (offset > lastOffset(data.total, size)) {
      offset = lastOffset(data.total, size);
      params.set("offset", offset);
      data = offline
        ? await invoke("list_local_works", { offset, limit: size, order, query, refresh: force, mediaType })
        : await api(`/api/works/page?${params}`, { signal });
      if (!request.current()) return;
    }
    const changedPage = runtime.worksOffset !== offset || size !== runtime.worksViewSize || query !== runtime.worksViewQuery || order !== runtime.worksViewOrder || mediaType !== runtime.worksViewType;
    runtime.worksViewType = mediaType;
    runtime.worksOffset = offset;
    runtime.worksTargetOffset = offset;
    runtime.worksViewSize = size;
    runtime.worksViewQuery = query;
    runtime.worksViewOrder = order;
    runtime.worksTotal = data.total;
    runtime.worksLoadedAt = Date.now();
    qs(".works-error-actions").hidden = true;
    status.textContent = offline
      ? (query ? `离线浏览：找到 ${data.total} 个作品` : `离线浏览：${data.total} 个作品`)
      : (query ? `找到 ${data.total} 个作品` : `${data.total} 个作品`);
    data.items = enrichUnifiedWorks(filterVisibleWorks(data.items || []));
    const signature = JSON.stringify([offset, size, query, order, mediaType, data.items]);
    if (signature === runtime.worksSignature && !force) return data.items;
    runtime.worksSignature = signature;
    thumbnails.release(grid);
    const fragment = document.createDocumentFragment();
    for (const work of data.items || []) {
      const card = document.createElement("article");
      card.className = "work-card";
      card.dataset.workId = work.id;
      card.tabIndex = 0;
      card.setAttribute("aria-label", `查看 ${work.filename || work.id}`);
      const media = document.createElement("div");
      media.className = "work-image media-target";
      media.append(createThumbnail(work));
      card.addEventListener("click", () => selectWork(work, card));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.target === card) { selectWork(work, card); openPlayer(work); }
      });
      media.addEventListener("dblclick", () => { selectWork(work, card); openPlayer(work); });
      const info = document.createElement("div");
      info.className = "work-info";
      const title = document.createElement("strong");
      title.textContent = work.filename || `作品 ${work.id.slice(0, 8)}`;
      title.title = title.textContent;
      const meta = document.createElement("small");
      const elapsed = formatElapsedSeconds(work.elapsed_seconds);
      meta.textContent = isImageWork(work) ? `${work.resolution || '规格未知'} · 图片` : `${work.resolution || "规格未知"} · ${work.duration || "--"} 秒 · 视频${elapsed ? ` · 生成耗时 ${elapsed}` : ""}`;
      info.append(title, meta);
      const actions = document.createElement("div");
      actions.className = "work-actions";
      const workActions = [[isImageWork(work) ? '打开图片路径' : '打开视频路径', 'open-path'], ['下载', 'download'], ...(!isImageWork(work) ? [['2× 超分','upscale']] : []), ['复制提示词','copy'], ['重新编辑','edit'], ['删除','delete']];
      for (const [label, action] of workActions) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        if (action === 'upscale' && (work.isUpscaleVariant || String(work.filename || work.output).toLowerCase().endsWith('-2x.mp4'))) button.disabled = true;
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          try {
            if (action === "open-path") {
              if (isNode() && !work.cloud && !work.localPath) await downloadWork(work);
              else await invoke('open_work_video_path', { sourceFilename: work.filename });
            }
            if (action === "download") await downloadWork(work);
            if (action === 'upscale') await upscaleWork(work);
            if (action === "copy") { await navigator.clipboard.writeText(work.prompt || ""); button.textContent = "已复制"; }
            if (action === "edit") { selectWork(work, card); await editSelectedWork(); }
            if (action === "delete" && window.confirm("确定删除这个作品吗？")) {
              button.disabled = true;
              if (work.isUpscaleVariant) {
                if (isNode() && !work.cloud) await api(`/api/works/${encodeURIComponent(`file:${work.filename}`)}`, {method:'DELETE'});
                else await invoke('delete_unified_work', {filename:work.filename});
                removeUpscaleRecord(work.sourceId);
              }
              else if (work.cloud) { await invoke('delete_unified_work', {filename:work.filename}); removeCloudTask(work.id); }
              else await deleteLibraryWork(work);
              if (runtime.selectedWork?.id === work.id) clearSelectedWork();
              await loadWorks({ force: true });
            }
          } catch (error) {
            status.textContent = `操作失败：${error.message || error}`;
            if (action === "open-path") notifyError(error);
          }
          finally { button.disabled = false; }
        });
        actions.append(button);
      }
      card.append(media, info, actions);
      fragment.append(card);
    }
    if (!data.items?.length) {
      const empty = document.createElement("p");
      empty.className = "subtitle works-empty";
      empty.textContent = query ? "没有匹配的作品，请调整搜索条件。" : "还没有作品，完成生成后会显示在这里。";
      fragment.append(empty);
    }
    grid.replaceChildren(fragment);
    if (runtime.selectedWork) {
      const selected = data.items.find((work) => work.id === runtime.selectedWork.id);
      const selectedCard = [...grid.children].find((card) => card.dataset.workId === runtime.selectedWork.id);
      if (selected && selectedCard) selectWork(selected, selectedCard);
      else clearSelectedWork();
    }
    if (changedPage) qs('[data-page="works"]').scrollTop = 0;
    return data.items;
  } catch (error) {
    if (!request.current()) return;
    runtime.worksFailed = true;
    let service = runtime.serviceStatus;
    if (!error.status && !isNode()) service = await invoke("service_status").catch(() => service);
    if (!request.current()) return;
    status.textContent = isNode() ? `节点作品读取失败：${error.message || error}` : worksFailure(error, service);
    qs(".works-error-actions").hidden = false;
    qs(".works-start-service").hidden = isNode() || Boolean(error.status) || service?.running !== false;
    if (!runtime.worksSignature) qs(".works-empty").textContent = "作品将在服务连接后显示。";
    throw error;
  } finally {
    if (request.current()) { runtime.worksLoading = false; renderPagination(); }
  }
}

function syncLocalEndpoint(service) {
  if (isNode() || !service?.web_url) return;
  LOCAL_API = service.web_url;
  API = LOCAL_API;
}
function renderOverview(service, apiStatus = null, resources = null) {
    syncLocalEndpoint(service);
    const wasRunning = Boolean(runtime.serviceStatus?.running);
    runtime.serviceStatus = service;
    runtime.apiStatus = apiStatus;
    sendCloneState();
    sendImageStudioTheme();
    runtime.resources = resources;
    const running = Boolean(service?.running);
    const dependencyPreparing = apiStatus?.backend_start?.state === 'preparing_dependencies';
    const dependencyFailed = apiStatus?.backend_start?.state === 'failed';
    const ready = Boolean(running && apiStatus?.ready && apiStatus?.h3_nodes?.ok !== false);
    const auxiliaryReady = running && isCloud();
    const onDemand = running && !apiStatus?.ready && runtime.settings?.prestart_comfyui === false;
    qs(".start-service").textContent = isNode() ? '节点管理' : running ? "运行中" : "启动服务";
    qs(".start-service").disabled = isNode() || runtime.serviceCommandRunning || running;
    qs(".stop-service").disabled = isNode() || runtime.serviceCommandRunning || !(running || service?.backend_running);
    qs(".restart-service").disabled = isNode() || runtime.serviceCommandRunning || !running;
    for (const selector of ['.settings-stop-service','.settings-restart-service','.settings-open-canvas']) {
      qs(selector).disabled = isNode();
    }
    for (const button of qsa('.service-actions button')) {
      if (!button.textContent.includes('缓存')) button.disabled = isNode();
    }
    qs('.quick-open-output span').textContent = isNode() ? '查看节点作品' : '打开生成目录';
    qs('.storage-open-output').textContent = isNode() ? '查看节点作品' : '打开';
    qs('.settings-service-address').textContent = API;
    qs(".service-panel .badge").textContent = dependencyPreparing ? '正在补齐依赖' : auxiliaryReady ? '辅助功能可用' : ready ? "运行正常" : onDemand ? 'Web 已就绪' : running ? "后端未就绪" : "已停止";
    qs(".settings-service-state").textContent = dependencyPreparing ? '正在补齐生成依赖' : auxiliaryReady ? '辅助功能可用' : ready ? "服务正常" : onDemand ? '模型按需启动' : running ? "后端未就绪" : "已停止";
    qs(".about-service-state").textContent = running ? "已连接" : "未连接";
    qs(".about-backend-state").textContent = ready ? "正常" : apiStatus?.h3_nodes?.ok === false && apiStatus?.ready ? '节点缺失' : onDemand ? '尚未启动' : running ? "未就绪" : "未连接";
    qs(".mini-status").setAttribute("aria-label", ready ? "服务运行正常" : running ? "服务启动中" : "服务已停止");
    qs(".mini-status").title = ready ? "服务运行正常" : running ? "服务启动中" : "服务已停止";
    qs(".hero-status strong").textContent = isNode() ? (running ? ready ? '节点 H3 已就绪' : '节点已连接，模型按需启动' : '算力节点未连接') : auxiliaryReady || ready ? 'minimaxH3服务已就绪' : onDemand ? 'Web 已就绪，生成时启动模型' : running ? "MiniMax H3 未就绪" : "MiniMax H3 已停止";
    if (dependencyPreparing) qs('.hero-status strong').textContent = '第一步：补齐本地生成依赖';
    qs(".hero-status p").textContent = runtime.serviceCommandError || (dependencyPreparing || dependencyFailed
      ? apiStatus.backend_start.error || apiStatus.backend_start.message
      : service?.message) || "服务未运行";
    const metrics = qsa(".metric strong");
    const gpu = resources?.gpu?.[0];
    metrics[0].textContent = gpu ? `${gpu.memory_used_gb} / ${gpu.memory_total_gb} GB` : "-";
    metrics[1].textContent = resources?.memory ? `${resources.memory.used_gb} / ${resources.memory.total_gb} GB` : "-";
    metrics[3].textContent = `${apiStatus?.works_count ?? 0}`;
    const runningJobs = Number(apiStatus?.queue?.running_count || 0) + savedCloudTasks().filter(job => job.status === 'running').length;
    const queued = Number(apiStatus?.queue?.queued_count || 0) + savedCloudTasks().filter(job => job.status === 'queued').length;
    qs(".queue .badge").textContent = runningJobs ? "生成中" : queued ? "排队中" : "空闲";
    qs(".queue-main strong").textContent = runningJobs ? "运行中" : "空闲";
    if (runningJobs) renderCurrentTaskTimer();
    qs(".queue-main > span").textContent = `队列 ${queued}`;
    if (running && !wasRunning) {
      runtime.tasksLoadedAt = 0;
      Promise.allSettled([runtime.page === "works" ? loadWorks() : Promise.resolve(), loadTasks(), loadTodayCompleted(), loadConfig()]);
    }
}

async function refreshOverview() {
  if (runtime.overviewRefreshInFlight || runtime.serviceCommandRunning) return;
  const generation = runtime.overviewGeneration;
  runtime.overviewRefreshInFlight = true;
  try {
    const service = isNode()
      ? await probeNode(API).then(() => ({running:true,web_url:API,message:`已连接算力节点 ${API}`,managed:false})).catch(error => ({running:false,web_url:API,message:String(error),managed:false}))
      : await invoke("service_status");
    syncLocalEndpoint(service);
    let apiStatus = null;
    let resources = runtime.resources;
    if (service.running) {
      [apiStatus, resources] = await Promise.all([
        api("/api/status"),
        runtime.page === "overview" ? api("/api/resources", { signal: AbortSignal.timeout(5000) }).catch(() => runtime.resources) : Promise.resolve(resources),
      ]);
    } else {
      const works = isNode() ? null : await invoke('list_local_works', {offset:0,limit:1,order:'desc',query:''}).catch(() => null);
      apiStatus = {works_count:works?.total || 0,queue:{}};
    }
    if (generation === runtime.overviewGeneration && !runtime.serviceCommandRunning) {
      renderOverview(service, apiStatus, resources);
    }
  } catch (error) {
    qs('.mini-status').title = `服务状态读取失败 · ${error.message || error}`;
  } finally {
    runtime.overviewRefreshInFlight = false;
  }
}

async function runServiceCommand(command, pendingLabel) {
  if (isNode()) throw new Error('当前使用远端算力节点，请在节点主机管理服务');
  if (runtime.serviceCommandRunning) return;
  if (['start_service','start_aux_service','restart_service'].includes(command)) {
    try { if (!(await ensureAuxRuntime())) return; }
    catch (error) { notifyError(error); return; }
  }
  runtime.serviceCommandRunning = true;
  runtime.serviceCommandError = "";
  runtime.overviewGeneration += 1;
  const controls = qsa(".service-control-actions button");
  const worksStart = qs(".works-start-service");
  worksStart.disabled = true;
  worksStart.textContent = "启动中…";
  controls.forEach((button) => { button.disabled = true; });
  if (pendingLabel) qs(".start-service").textContent = pendingLabel;
  try {
    const result = await invoke(command);
    renderOverview(result.status);
  } catch (error) {
    runtime.serviceCommandError = String(error);
    qs(".hero-status p").textContent = String(error);
    if (runtime.page === "works") qs('.works-status').textContent = `服务操作失败：${error}`;
  } finally {
    runtime.serviceCommandRunning = false;
    worksStart.disabled = false;
    worksStart.textContent = "启动服务";
    await refreshOverview();
    if (runtime.serviceStatus) renderOverview(runtime.serviceStatus, runtime.apiStatus, runtime.resources);
  }
}

function bindDesktopCommands() {
  const serviceButtons = qsa(".service-panel button");
  intercept(qs(".start-service"), () => runServiceCommand("start_service", "启动中"));
  intercept(qs(".stop-service"), () => runServiceCommand("stop_service"));
  intercept(qs(".restart-service"), () => runServiceCommand("restart_service"));
  intercept(serviceButtons.find((button) => button.textContent.includes("画布")), () => invoke("open_canvas"));
  intercept(qs(".open-web-workbench"), () => invoke("open_workbench"));
  intercept(serviceButtons.find((button) => button.textContent.includes("日志")), () => invoke("open_service_log"));
  intercept(serviceButtons.find((button) => button.textContent.includes("缓存")), () => (isNode() ? api('/api/comfy/free',{method:'POST'}) : invoke("free_backend_cache")).then(refreshOverview));
}

function setToggleState(toggle, enabled) {
  toggle.classList.toggle("off", !enabled);
  toggle.setAttribute("aria-pressed", String(enabled));
}

function renderPerformanceSettings(settings) {
  runtime.settings = settings;
  localStorage.setItem('minimax-active-generation-engine', settings.generation_engine);
  updateCompiledState();
  sendCloneState();
  qs('.performance-engine').value = isNode() ? 'node' : settings.generation_engine;
  qs('.performance-cloud-clone-reference').value = settings.cloud_clone_reference_type || 'source';
  qs('.performance-reference-model').value = settings.workbench_reference_model || 'fl2va';
  qs(".performance-acceleration").value = settings.acceleration_mode;
  qs(".performance-default-steps").value = String(settings.default_steps);
  qs(".performance-vram-reserve").value = String(settings.vram_reserve_gb);
  qs(".performance-nsfw-profile").value = settings.nsfw_profile;
  setToggleState(qs(".performance-free-cache"), settings.free_gpu_cache_after_task);
  setToggleState(qs(".performance-nsfw"), settings.nsfw_enabled);
  qs('.performance-nsfw-profile').closest('.setting').hidden = !settings.nsfw_enabled;
  const turbo = (runtime.config?.acceleration_mode || settings.acceleration_mode).startsWith("turbo");
  qs(".steps-input").disabled = turbo;
  if (turbo || !runtime.config) qs(".steps-input").value = turbo ? 8 : settings.default_steps;
  qs(".performance-settings-state").textContent = isNode() ? '节点模式 · 性能配置请在节点主机调整' : isCloud() ? '云端模式 · 本地推理参数已停用' : "已保存";
  qs(".engine-settings-state").textContent = isNode() ? '算力节点' : isPlatform() ? '平台云端' : isCloud() ? '个人优云' : '本地';
  renderSettingsMode();
  renderEngineControls();
  for (const selector of ['.performance-reference-model','.performance-acceleration','.performance-default-steps',
    '.performance-vram-reserve','.performance-nsfw-profile','.performance-free-cache','.performance-nsfw']) {
    qs(selector).disabled = isNode();
  }
  updateCloudPointBadges();
  renderGeneralSettings(settings);
  syncPlatformShell();
  if (isPlatform() && !runtime.platformPricing) loadPlatformPricing().catch(() => {});
}

function renderGeneralSettings(settings) {
  setToggleState(qs(".service-prestart-comfy"), settings.prestart_comfyui);
  setToggleState(qs(".window-f11"), settings.f11_fullscreen_enabled);
  setToggleState(qs(".appearance-reduce-motion"), settings.reduce_motion);
  qs(".window-close-behavior").value = settings.close_behavior;
  root.classList.toggle("reduce-motion", Boolean(settings.reduce_motion));
}

async function loadPerformanceSettings() {
  try {
    let settings = await invoke("get_panel_settings");
    if (!window.MinimaxBrowser) {
      if (settings.compute_node_enabled == null) {
        // 首次升级迁移旧缓存；以后以磁盘配置为准，离线也不改变用户选择。
        const enabled = nodeEnabled && settings.generation_engine === 'local';
        settings = await invoke('set_generation_engine', {engine:settings.generation_engine,nodeEnabled:enabled,nodeAddress:savedNodeAddress() || null});
      }
      nodeEnabled = (settings.compute_node_enabled ?? nodeEnabled) && settings.generation_engine === 'local';
      const address = settings.compute_node_address || savedNodeAddress();
      API = nodeEnabled ? normalizeNodeAddress(address) : LOCAL_API;
      localStorage.setItem(NODE_MODE_KEY, String(nodeEnabled));
      if (address) localStorage.setItem(NODE_STORAGE_KEY, normalizeNodeAddress(address));
      qs('.compute-node-address').value = address;
      qs('.compute-node-state').textContent = address ? `${nodeEnabled ? '使用中' : '已保存'} · ${address}` : '尚未配置';
    }
    renderPerformanceSettings(settings);
  } catch (error) {
    qs(".performance-settings-state").textContent = "读取失败";
  }
}

async function savePerformanceSetting(control, command, args) {
  control.disabled = true;
  const state = control.classList.contains('performance-cloud-clone-reference')
    ? qs('.engine-settings-state') : qs('.performance-settings-state');
  state.textContent = '保存中';
  try {
    renderPerformanceSettings(await invoke(command, args));
    if (control.classList.contains('performance-cloud-clone-reference')) state.textContent = '已保存';
    if (runtime.serviceStatus?.running && !isCloud()) {
      try {
        await loadConfig();
        state.textContent = runtime.config?.acceleration_mode
          ? (runtime.config.acceleration_mode === runtime.settings.acceleration_mode ? '已保存' : '已保存但未生效：服务配置被环境变量覆盖')
          : '已保存，暂无法读取服务实际配置';
      } catch {
        state.textContent = '已保存，暂无法读取服务实际配置';
      }
    }
  } catch (error) {
    await loadPerformanceSettings();
    state.textContent = `保存失败：${error.message || error}`;
  } finally {
    control.disabled = false;
  }
}

function bindPerformanceSettings() {
  const engine = qs('.performance-engine');
  engine.addEventListener('change', async () => {
    const selected = engine.value;
    const previous = nodeEnabled && runtime.settings?.generation_engine === 'local' ? 'node' : runtime.settings?.generation_engine;
    if (selected === 'node') {
      engine.value = previous;
      openComputeNodeDialog();
      return;
    }
    renderSettingsMode();
    engine.disabled = true;
    try {
      await setRunMode(selected === 'platform_h3' ? 'cloud' : selected === 'compshare_h3' ? 'personal' : 'local');
    } catch (error) {
      engine.value = previous;
      renderSettingsMode();
      qs('.engine-settings-state').textContent = String(error);
      qs('.performance-settings-state').textContent = String(error);
    } finally { engine.disabled = false; }
  });
  const acceleration = qs(".performance-acceleration");
  const referenceModel = qs(".performance-reference-model");
  const cloudCloneReference = qs(".performance-cloud-clone-reference");
  const steps = qs(".performance-default-steps");
  const vram = qs(".performance-vram-reserve");
  const freeCache = qs(".performance-free-cache");
  const nsfw = qs(".performance-nsfw");
  const profile = qs(".performance-nsfw-profile");
  acceleration.addEventListener("change", () => savePerformanceSetting(acceleration, "set_acceleration_mode", { mode: acceleration.value }));
  referenceModel.addEventListener("change", () => savePerformanceSetting(referenceModel, "set_workbench_reference_model", { model: referenceModel.value }));
  cloudCloneReference.addEventListener("change", () => savePerformanceSetting(cloudCloneReference, "set_cloud_clone_reference_type", { referenceType: cloudCloneReference.value }));
  steps.addEventListener("change", () => savePerformanceSetting(steps, "set_default_steps", { steps: Number(steps.value) }));
  vram.addEventListener("change", () => savePerformanceSetting(vram, "set_vram_reserve", { reserveGb: Number(vram.value) }));
  freeCache.addEventListener("click", () => savePerformanceSetting(freeCache, "set_free_gpu_cache_after_task", { enabled: freeCache.classList.contains("off") }));
  nsfw.addEventListener("click", () => savePerformanceSetting(nsfw, "set_nsfw_enabled", { enabled: nsfw.classList.contains("off") }));
  profile.addEventListener("change", () => savePerformanceSetting(profile, "set_nsfw_profile", { profile: profile.value }));
}

async function saveGeneralSetting(control, command, args) {
  control.disabled = true;
  try {
    renderPerformanceSettings(await invoke(command, args));
  } catch (error) {
    await loadPerformanceSettings();
    qs(".performance-settings-state").textContent = `保存失败：${error.message || error}`;
  } finally {
    control.disabled = false;
  }
}

async function loadStorageSettings() {
  try {
    renderInstallDirectory(await invoke("install_directory_status"));
  } catch (error) {
    qs(".install-directory-state").textContent = `识别失败：${error.message || error}`;
  }
  try {
    const [paths, cache, settings] = await Promise.all([invoke("storage_paths"), invoke("reference_image_cache_status"), invoke("get_panel_settings")]);
    qs(".storage-output-path").textContent = paths.output;
    qs(".storage-database-path").textContent = paths.database;
    renderDownloadSettings(settings);
    const stale = cache.buckets?.find((bucket) => bucket.id === "h3-refs-stale");
    const copy = qs(".storage-clear-refs").parentElement.querySelector("small");
    if (stale && copy) copy.textContent = `${stale.file_count} 个未引用文件，可释放 ${formatBytes(stale.size_bytes)}`;
    qs('[data-setting-pane="storage"] .settings-pane-head .subtitle').textContent = "已读取存储状态";
  } catch (error) {
    qs('[data-setting-pane="storage"] .settings-pane-head .subtitle').textContent = "读取失败";
  }
}

function renderInstallDirectory(status) {
  qs(".install-directory-input").value = status.path || "";
  qs(".install-directory-state").textContent = status.path
    ? status.source === "manual" ? "手动绑定" : "自动识别"
    : "未找到安装目录";
}

function renderDownloadSettings(settings) {
  runtime.settings = settings;
  qs(".storage-download-mode").value = settings.download_mode;
  const quick = settings.download_mode === "quick";
  qs(".storage-download-directory-setting").hidden = !quick;
  qs(".storage-download-directory").textContent = settings.download_directory || "尚未选择目录";
}

function showInstallCandidates(paths) {
  const select = qs(".install-directory-candidates");
  select.replaceChildren(...paths.map((path) => {
    const option = document.createElement("option");
    option.value = path;
    option.textContent = path;
    return option;
  }));
  select.hidden = paths.length < 2;
  if (paths.length > 1) qs(".install-directory-state").textContent = `找到 ${paths.length} 个安装目录`;
}

async function applyInstallDirectory(path) {
  const state = qs(".install-directory-state");
  state.textContent = "正在绑定";
  const previous = await invoke("install_directory_status");
  const status = await invoke("bind_install_directory", { path });
  showInstallCandidates([]);
  renderInstallDirectory(status);
  if (previous.path?.toLowerCase() !== status.path?.toLowerCase()) {
    runtime.taskSignature = "";
    runtime.worksSignature = "";
    runtime.worksLoadedAt = 0;
    runtime.tasksLoadedAt = 0;
    runtime.config = null;
    runtime.currentJob = "";
    runtime.selectedWork = null;
    runtime.activeJobIds.clear();
    localStorage.removeItem("minimax-current-job");
  }
  await Promise.all([loadStorageSettings(), loadPerformanceSettings(), refreshOverview()]);
}

async function inspectInstallDirectory(path) {
  const candidates = await invoke("inspect_install_directory", { path });
  if (!candidates.length) throw new Error("未找到有效的 MiniMax 安装目录");
  showInstallCandidates(candidates);
  if (candidates.length === 1) await applyInstallDirectory(candidates[0]);
}

function bindInstallDirectorySettings() {
  const input = qs(".install-directory-input");
  const select = qs(".install-directory-candidates");
  const state = qs(".install-directory-state");
  const pick = qs(".install-directory-pick");
  const apply = qs(".install-directory-apply");
  const detect = qs(".install-directory-detect");
  const run = async (button, action) => {
    button.disabled = true;
    try { await action(); }
    catch (error) { state.textContent = `绑定失败：${error.message || error}`; }
    finally { button.disabled = false; }
  };
  input.addEventListener("input", () => { select.hidden = true; });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); apply.click(); }
  });
  pick.addEventListener("click", () => run(pick, async () => {
    const path = await invoke("pick_install_directory");
    if (path) { input.value = path; await inspectInstallDirectory(path); }
  }));
  apply.addEventListener("click", () => run(apply, async () => {
    if (!select.hidden && select.value) await applyInstallDirectory(select.value);
    else await inspectInstallDirectory(input.value.trim());
  }));
  detect.addEventListener("click", () => run(detect, async () => {
    const candidates = await invoke("detect_install_directories");
    if (!candidates.length) throw new Error("未找到有效的 MiniMax 安装目录");
    showInstallCandidates(candidates);
    if (candidates.length === 1) await applyInstallDirectory(candidates[0]);
  }));
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

function bindRemainingSettings() {
  bindInstallDirectorySettings();
  const auxState = qs('.aux-runtime-state');
  window.__TAURI__.event?.listen('aux-runtime-progress', event => {
    if (event.payload.phase === 'manifest') { auxState.textContent = '正在获取下载清单'; return; }
    if (event.payload.phase === 'installing') { auxState.textContent = '正在安装'; return; }
    const {index, total, downloaded, size} = event.payload;
    auxState.textContent = `下载 ${index}/${total}：${Math.min(100, Math.floor(downloaded / size * 100))}%`;
  });
  const refreshAux = () => invoke('aux_runtime_available').then(ready => { auxState.textContent = ready ? '已安装' : '未安装'; });
  refreshAux().catch(() => { auxState.textContent = '检查失败'; });
  qs('.aux-runtime-install').addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true;
    try { if (await ensureAuxRuntime()) await refreshAux(); }
    catch (error) { auxState.textContent = String(error); }
    finally { button.disabled = false; }
  });
  const prestart = qs(".service-prestart-comfy");
  const closeBehavior = qs(".window-close-behavior");
  const f11 = qs(".window-f11");
  const reduceMotion = qs(".appearance-reduce-motion");
  prestart.addEventListener("click", () => saveGeneralSetting(prestart, "set_prestart_comfyui", { enabled: prestart.classList.contains("off") }));
  closeBehavior.addEventListener("change", () => saveGeneralSetting(closeBehavior, "set_close_behavior", { behavior: closeBehavior.value }));
  f11.addEventListener("click", () => saveGeneralSetting(f11, "set_f11_fullscreen_enabled", { enabled: f11.classList.contains("off") }));
  reduceMotion.addEventListener("click", () => saveGeneralSetting(reduceMotion, "set_reduce_motion", { enabled: reduceMotion.classList.contains("off") }));

  qs(".service-copy-address").addEventListener("click", () => navigator.clipboard.writeText(API));
  qs(".settings-stop-service").addEventListener("click", () => runServiceCommand("stop_service"));
  qs(".settings-restart-service").addEventListener("click", () => runServiceCommand("restart_service"));
  qs(".settings-diagnose-service").addEventListener("click", refreshOverview);
  qs(".settings-open-canvas").addEventListener("click", () => invoke("open_canvas").catch((error) => { qs(".settings-service-state").textContent = String(error); }));
  qs(".settings-view-addresses").addEventListener("click", async () => {
    const addresses = isNode() ? [API] : await invoke("copyable_addresses");
    window.alert(addresses.join("\n"));
  });

  qs(".storage-open-output").addEventListener("click", () => isNode() ? showPage('works') : invoke("open_generated_directory"));
  qs(".storage-download-mode").addEventListener("change", async (event) => {
    renderDownloadSettings(await invoke("set_download_mode", { mode: event.currentTarget.value }));
  });
  qs(".storage-pick-download-directory").addEventListener("click", async () => {
    renderDownloadSettings(await invoke("pick_download_directory"));
  });
  qs(".storage-open-data").addEventListener("click", () => invoke("open_data_directory"));
  qs(".storage-clear-refs").addEventListener("click", async () => {
    if (!window.confirm("仅删除没有被作品或任务引用的参考图片，是否继续？")) return;
    await invoke("clear_reference_image_cache");
    await loadStorageSettings();
  });
  qs(".storage-free-backend").addEventListener("click", () => (isNode() ? api('/api/comfy/free',{method:'POST'}) : invoke("free_backend_cache")).then(refreshOverview));
  qs(".about-open-log").addEventListener("click", () => invoke("open_service_log"));

  document.addEventListener("keydown", async (event) => {
    if (event.key !== "F11" || !runtime.settings?.f11_fullscreen_enabled) return;
    event.preventDefault();
    const appWindow = window.__TAURI__.window.getCurrentWindow();
    await appWindow.setFullscreen(!(await appWindow.isFullscreen()));
  });
}

const ASSISTANT_STORAGE_KEY = "minimax-assistant-provider";
function openComputeNodeDialog() {
  const dialog = qs('.compute-node-dialog');
  if (dialog.open) return;
  qs('.compute-node-dialog-address').value = savedNodeAddress();
  qs('.compute-node-dialog-status').textContent = '输入节点地址，确认后检查连接。';
  dialog.showModal();
  qs('.compute-node-dialog-address').focus();
}
function bindComputeNodeSettings() {
  const dialog = qs('.compute-node-dialog');
  const address = qs('.compute-node-dialog-address');
  const confirm = qs('.compute-node-dialog-confirm');
  const cancel = qs('.compute-node-dialog-cancel');
  cancel.addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => { if (confirm.disabled) event.preventDefault(); });
  qs('.compute-node-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (confirm.disabled) return;
    confirm.disabled = cancel.disabled = address.disabled = true;
    confirm.textContent = '检查中…';
    qs('.compute-node-dialog-status').textContent = '正在检查节点连接…';
    try {
      const {base} = await probeNode(address.value);
      await setRunMode('node', base);
      input.value = base;
      state.textContent = `使用中 · ${base}`;
      dialog.close();
    } catch (error) { qs('.compute-node-dialog-status').textContent = `连接失败：${error.message || error}`; }
    finally { confirm.disabled = cancel.disabled = address.disabled = false; confirm.textContent = '确定'; }
  });
  const input = qs('.compute-node-address');
  const state = qs('.compute-node-state');
  const connect = qs('.compute-node-connect');
  const test = qs('.compute-node-test');
  input.value = savedNodeAddress();
  state.textContent = input.value ? (nodeEnabled ? `使用中 · ${input.value}` : `已保存 · ${input.value}`) : '尚未配置';
  input.addEventListener('input', () => {
    try { state.textContent = `将连接 ${normalizeNodeAddress(input.value)}`; }
    catch { state.textContent = '输入 IP、IP:端口或完整地址'; }
  });
  const verify = async (activate) => {
    connect.disabled = test.disabled = true;
    state.textContent = '正在连接节点…';
    try {
      const {base} = await probeNode(input.value);
      state.textContent = `连接正常 · ${base}`;
      if (activate) {
        await setRunMode('node', base);
        input.value = base;
        state.textContent = `使用中 · ${base}`;
      }
    } catch (error) { state.textContent = `连接失败：${error.message || error}`; }
    finally { connect.disabled = test.disabled = false; }
  };
  connect.addEventListener('click', () => verify(true));
  test.addEventListener('click', () => verify(false));
  input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); verify(true); } });
}
function updateCloudPointBadges(label) {
  qsa('.cloud-points').forEach(badge => { badge.hidden = qs('.platform-run-switch').hidden || !isCloud(); if (label) { badge.textContent = label; badge.title = label; } });
  qs('.clone-center-frame')?.contentWindow?.postMessage({type:'cloud-h3-balance',visible:isCloud(),
    label:qs('.cloud-points')?.textContent || '优云智算积分 --'}, '*');
}
async function refreshCloudBalance(force = false) {
  if (!isCloud() && !force) return;
  updateCloudPointBadges();
  try {
    const points = isPlatform() ? await platformApi.balance() : await invoke('cloud_h3_balance');
    updateCloudPointBadges(`积分 ${Number(points.available_points).toLocaleString()}`);
    qs('.cloud-h3-state').textContent = `可用 ${points.available_points} · 预占 ${points.reserved_points}`;
  } catch (error) {
    updateCloudPointBadges('优云智算积分读取失败');
    qs('.cloud-h3-state').textContent = String(error);
  }
}

function bindCloudSettings() {
  const input = qs('.cloud-h3-key');
  const status = qs('.cloud-h3-state');
  invoke('cloud_h3_configured').then(configured => { status.textContent = configured ? '已配置，密钥已加密保存' : '尚未配置'; });
  qs('.cloud-h3-save').addEventListener('click', async () => {
    const button = qs('.cloud-h3-save'); button.disabled = true;
    try {
      const configured = await invoke('cloud_h3_save_key', {key:input.value});
      input.value = '';
      status.textContent = configured ? '已保存，可在生成引擎中选择优云智算 H3' : '密钥已清除';
      if (!configured && runtime.settings?.generation_engine === 'compshare_h3') renderPerformanceSettings(await invoke('set_generation_engine', {engine:'local'}));
      if (configured) await refreshCloudBalance(true);
    } catch (error) { status.textContent = String(error); }
    finally { button.disabled = false; }
  });
  qs('.cloud-h3-refresh').addEventListener('click', () => refreshCloudBalance(true));
}
const PROVIDERS_KEY = 'minimax-assistant-providers';
function savedProviders() {
  try {
    const list = JSON.parse(localStorage.getItem(PROVIDERS_KEY) || 'null');
    if (Array.isArray(list)) return list;
    const legacy = JSON.parse(localStorage.getItem(ASSISTANT_STORAGE_KEY) || 'null');
    return legacy ? [{...legacy,id:legacy.id || 'legacy'}] : [];
  } catch { return []; }
}
async function persistAssistantProviders(providers = savedProviders()) {
  if (window.MinimaxBrowser?.canManage === false) return providers;
  if (window.MinimaxBrowser && arguments.length === 0) return providers;
  if (window.MinimaxBrowser) return invoke('save_assistant_providers', {providers});
  let migrated = false;
  const safe = [];
  for (const provider of providers) {
    let encryptedKey = provider.encryptedKey || '';
    if (!encryptedKey && provider.apiKey) {
      encryptedKey = await invoke('protect_assistant_key', {key:provider.apiKey});
      migrated = true;
    }
    const {apiKey, ...rest} = provider;
    safe.push({...rest, encryptedKey});
  }
  if (migrated) {
    localStorage.setItem(PROVIDERS_KEY, JSON.stringify(safe));
    if (safe[0]) localStorage.setItem(ASSISTANT_STORAGE_KEY, JSON.stringify(safe[0]));
  }
  await invoke('save_assistant_providers', {providers:safe});
  return safe;
}
function assistantElements() {
  const card = qs(".provider-card");
  return {
    card,
    name: card?.querySelector(".provider-config-name"),
    baseUrl: card?.querySelector(".provider-base-url"),
    apiKey: card?.querySelector(".provider-api-key"),
    apiType: card?.querySelector(".api-type"),
    autoModels: card?.querySelector(".auto-models"),
    state: card?.querySelector(".provider-state"),
    preview: card?.querySelector(".url-preview"),
    models: card?.querySelector(".model-options"),
    defaultModel: card?.querySelector(".provider-default-model"),
    toggle: card?.querySelector(".toggle"),
    collapse: card?.querySelector(".provider-collapse"),
  };
}

function setAssistantCardCollapsed() {
  const els = assistantElements();
  els.card.classList.remove('collapsed');
  els.collapse.textContent = '关闭';
  els.collapse.setAttribute('aria-label', '关闭 API 配置');
}

function renderAssistantModels(modelIds, selected = "") {
  const els = assistantElements();
  els.models.innerHTML = "";
  els.defaultModel.innerHTML = "";
  if (!modelIds.length) {
    els.models.innerHTML = '<span class="subtitle">接口未返回可用模型</span>';
    els.defaultModel.add(new Option("未选择模型", ""));
    return;
  }
  for (const id of modelIds) {
    const label = document.createElement("label");
    label.className = "model-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.value = id;
    label.append(checkbox, document.createTextNode(id));
    els.models.append(label);
    els.defaultModel.add(new Option(id, id));
  }
  els.defaultModel.value = modelIds.includes(selected) ? selected : modelIds[0];
}

async function runAssistantRequest(command, button) {
  const els = assistantElements();
  const request = { baseUrl: els.baseUrl.value, apiKey: els.apiKey.value, providerId: els.card.dataset.providerId };
  button.disabled = true;
  const original = button.textContent;
  button.textContent = command === "fetch_assistant_models" ? "获取中..." : "测试中...";
  els.state.textContent = "正在连接接口";
  try {
    const result = await invoke(command, { request });
    if (els.baseUrl.value !== request.baseUrl || els.apiKey.value !== request.apiKey) throw new Error('请求期间接口配置已改变，请重新操作');
    els.baseUrl.value = result.normalizedBaseUrl;
    els.preview.textContent = `${result.normalizedBaseUrl}/models`;
    renderAssistantModels(result.models, els.defaultModel.value);
    els.state.textContent = result.models.length ? `已获取 ${result.models.length} 个模型` : result.message;
    qs(".assistant-global-state").textContent = "可用";
    return result;
  } catch (error) {
    els.state.textContent = String(error);
    qs(".assistant-global-state").textContent = "连接失败";
    throw error;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function bindAssistantSettings() {
  const els = assistantElements();
  if (!els.card) return;
  let activeId = '';
  const openChat = bindAssistantChat(root, invoke, savedProviders);
  const picker = qs('.provider-picker');
  const updateChoices = () => {
    const providers = savedProviders();
    picker.replaceChildren();
    if (!providers.length) {
      const empty = document.createElement('div');
      empty.className = 'provider-picker-empty';
      empty.textContent = '尚未添加 API 配置';
      picker.append(empty);
      return;
    }
    for (const item of providers) {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = `provider-choice-card${item.id === activeId ? ' active' : ''}${item.enabled === false ? ' disabled' : ''}`;
      card.setAttribute('role', 'listitem');
      card.setAttribute('aria-pressed', String(item.id === activeId));
      card.title = '编辑 API 配置';
      const name = document.createElement('span');
      name.className = 'provider-choice-name';
      name.textContent = item.name || '未命名接口';
      const status = document.createElement('span');
      status.className = 'provider-choice-status';
      status.textContent = item.enabled === false ? '已停用' : '已启用';
      const meta = document.createElement('span');
      meta.className = 'provider-choice-meta';
      meta.textContent = item.defaultModel || item.apiType || '尚未选择模型';
      card.append(name, status, meta);
      card.addEventListener('click', () => {
        displayProvider(item);
        updatePreview();
        qs('.provider-dialog').showModal();
      });
      card.addEventListener('dblclick', () => {
        displayProvider(item);
        updatePreview();
        setAssistantCardCollapsed(false);
        if (!qs('.provider-dialog').open) qs('.provider-dialog').showModal();
        els.name.focus();
      });
      card.addEventListener('keydown', event => {
        if (event.key === 'F2') {
          event.preventDefault();
          card.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
        }
      });
      picker.append(card);
    }
  };
  const displayProvider = saved => {
  activeId = saved?.id || crypto.randomUUID();
  els.card.dataset.providerId = saved?.id || '';
  els.apiKey.placeholder = window.MinimaxBrowser && saved?.hasKey ? '已保存，留空保留原 Key' : '请输入 API Key';
  els.apiKey.value = '';
  if (saved) {
    els.name.value = saved.name || "";
    els.baseUrl.value = saved.baseUrl || "";
    els.apiType.value = saved.apiType === 'OpenAI Responses API' ? saved.apiType : 'OpenAI Chat Completions API';
    qs('.provider-preset').value = saved.preset || '0';
    els.autoModels.checked = saved.autoModels !== false;
    els.toggle.classList.toggle("off", saved.enabled === false);
    els.providerEnabled = saved.enabled !== false;
    renderAssistantModels(saved.models || [], saved.defaultModel || "");
    if (!window.MinimaxBrowser && saved.encryptedKey) invoke('unprotect_assistant_key', {encrypted:saved.encryptedKey}).then(key => { if (activeId === saved.id && !els.apiKey.value) els.apiKey.value = key; }).catch(error => { els.state.textContent = String(error); });
    else els.apiKey.value = saved.apiKey || '';
    els.state.textContent = window.MinimaxBrowser ? "已加载服务器配置，已有 Key 不回传" : "已加载本机配置";
    qs(".provider-name").textContent = saved.name || "AI 接口";
    qs(".assistant-global-state").textContent = saved.enabled === false ? "已停用" : "已配置";
    setAssistantCardCollapsed(true);
  } else {
    els.name.value = ''; els.baseUrl.value = ''; els.apiType.value = 'OpenAI Chat Completions API'; qs('.provider-preset').value = '0'; els.autoModels.checked = true;
    els.toggle.classList.remove('off'); renderAssistantModels([]);
    els.state.textContent = '请填写并保存 API 配置'; qs('.provider-name').textContent = '新接口';
    setAssistantCardCollapsed(false);
  }
  updateChoices();
  };
  const presetSelect = qs('.provider-preset');
  ASSISTANT_PRESETS.forEach((preset,index) => presetSelect.add(new Option(preset.name,String(index))));
  presetSelect.addEventListener('change', () => {
    const preset = ASSISTANT_PRESETS[Number(presetSelect.value)];
    els.baseUrl.value = preset.url;
    els.name.value = preset.name;
    els.apiType.value = preset.apiType || 'OpenAI Chat Completions API';
    renderAssistantModels(preset.model ? [preset.model] : []);
    els.autoModels.checked = !preset.model;
    updatePreview();
  });
  displayProvider(savedProviders()[0]);
  persistAssistantProviders().catch(error => { els.state.textContent = `共享 AI 助手配置失败：${error}`; });
  const updatePreview = () => {
    try {
      const value = normalizedAssistantUrl(els.baseUrl.value);
      const endpoint = els.apiType.value === 'OpenAI Responses API' ? 'responses' : 'chat/completions';
      els.preview.textContent = value ? `${value}/${endpoint}` : '请输入服务地址';
    } catch (error) { els.preview.textContent = error.message; }
  };
  els.baseUrl.addEventListener('input', updatePreview);
  els.baseUrl.addEventListener('blur', () => {
    try { els.baseUrl.value = normalizedAssistantUrl(els.baseUrl.value); updatePreview(); }
    catch (error) { els.state.textContent = error.message; }
  });
  els.apiType.addEventListener('change', updatePreview);
  updatePreview();
  els.toggle.addEventListener("click", () => {
    els.providerEnabled = els.toggle.classList.contains("off");
    els.toggle.classList.toggle("off", !els.providerEnabled);
  });
  els.collapse.addEventListener('click', () => qs('.provider-dialog').close());
  qs(".add-provider").addEventListener("click", () => {
    displayProvider(null); updatePreview();
    qs('.provider-dialog').showModal();
    presetSelect.focus();
  });
  qs(".fetch-models").addEventListener("click", () => runAssistantRequest("fetch_assistant_models", qs(".fetch-models")).catch(() => {}));
  qs('.add-model').addEventListener('click', () => {
    const id = qs('.manual-model').value.trim();
    if (!id) { els.state.textContent = '请填写模型 ID'; return; }
    const ids = [...els.models.querySelectorAll('input:checked')].map(input => input.value);
    renderAssistantModels([...new Set([...ids,id])], els.defaultModel.value || id);
    els.autoModels.checked = false;
    qs('.manual-model').value = '';
    els.state.textContent = '已添加模型，请保存配置';
  });
  qs('.test-provider').textContent = '测试对话';
  qs('.test-provider').addEventListener('click', () => openChat({id:els.card.dataset.providerId,name:els.name.value,baseUrl:els.baseUrl.value,apiType:els.apiType.value,apiKey:els.apiKey.value,defaultModel:els.defaultModel.value}));
  qs(".save-provider").addEventListener("click", async () => {
    try {
      const savingId = activeId;
      if (!els.baseUrl.value.trim()) { els.state.textContent = "请填写服务地址"; return; }
      const hostname = new URL(normalizedAssistantUrl(els.baseUrl.value)).hostname;
      if (!els.apiKey.value.trim() && !(window.MinimaxBrowser && savedProviders().find(item=>item.id===activeId)?.hasKey) && !['localhost','127.0.0.1','[::1]'].includes(hostname)) {
        els.state.textContent = '请输入 API Key'; return;
      }
      if (qs(".auto-models").checked) {
        try { await runAssistantRequest("fetch_assistant_models", qs(".save-provider")); } catch { return; }
      }
      const models = [...els.models.querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value);
      if (!models.length) { els.state.textContent = '请获取并选中至少一个模型'; return; }
      const savedProvider = { preset: presetSelect.value, name: els.name.value.trim() || ASSISTANT_PRESETS[Number(presetSelect.value)].name, apiType: els.apiType.value, baseUrl: normalizedAssistantUrl(els.baseUrl.value), autoModels: els.autoModels.checked, enabled: !els.toggle.classList.contains('off'), models, defaultModel: models.includes(els.defaultModel.value) ? els.defaultModel.value : models[0], ...(window.MinimaxBrowser ? {apiKey:els.apiKey.value,serverManaged:true} : {encryptedKey: await invoke('protect_assistant_key', {key:els.apiKey.value})}) };
      if (activeId !== savingId) return;
      savedProvider.id = savingId;
      const list = savedProviders();
      const index = list.findIndex(item => item.id === activeId);
      if (index < 0) list.push(savedProvider); else list[index] = savedProvider;
      const persisted = await persistAssistantProviders(list);
      localStorage.setItem(PROVIDERS_KEY, JSON.stringify(persisted));
      const persistedProvider = persisted.find(item=>item.id===savingId);
      localStorage.setItem(ASSISTANT_STORAGE_KEY, JSON.stringify(persistedProvider));
      if (window.MinimaxBrowser) els.apiKey.value = '';
      updateChoices();
      els.name.value = savedProvider.name;
      els.baseUrl.value = savedProvider.baseUrl;
      qs(".provider-name").textContent = savedProvider.name || "AI 接口";
      els.state.textContent = "配置已保存";
      qs(".assistant-global-state").textContent = savedProvider.enabled ? "已配置" : "已停用";
      setAssistantCardCollapsed(true);
      qs('.provider-dialog').close();
    } catch (error) { els.state.textContent = String(error); }
  });
  qs(".delete-provider").addEventListener("click", async () => {
    try {
      const list = savedProviders().filter(item => item.id !== activeId);
      const persisted = await persistAssistantProviders(list);
      localStorage.setItem(PROVIDERS_KEY, JSON.stringify(persisted));
      localStorage.removeItem(ASSISTANT_STORAGE_KEY);
      els.name.value = ""; els.baseUrl.value = ""; els.apiKey.value = "";
      renderAssistantModels([]); updatePreview();
      els.state.textContent = "请填写并保存 API 配置";
      qs(".provider-name").textContent = "未配置接口";
      qs(".assistant-global-state").textContent = "未配置";
      setAssistantCardCollapsed(false);
      displayProvider(savedProviders()[0]); updatePreview();
      qs('.provider-dialog').close();
    } catch (error) { els.state.textContent = String(error); }
  });
}

qs(".duration-input").addEventListener("input", updateSpecs, true);
qs(".duration-input").addEventListener("input", updateCompiledState, true);
qs(".resolution-select").addEventListener("change", updateSpecs, true);
qs(".resolution-select").addEventListener("change", updateCompiledState, true);
intercept(qs(".generate"), submitGeneration);
intercept(qs(".refresh-tasks"), loadTasks);
intercept(qs(".clear-failed-tasks"), async () => {
  const button = qs(".clear-failed-tasks");
  const status = qs(".task-cleanup-status");
  button.disabled = true;
  status.hidden = false;
  status.textContent = "正在清理…";
  taskHistoryGeneration += 1;
  try {
    let backendError = '';
    let deletedIds = [];
    if (!isCloud()) {
      try { deletedIds = (await api("/api/job-history/failed", { method: "DELETE" })).deleted_ids || []; }
      catch (error) { backendError = `后端记录清理失败：${error.message || error}`; }
    }
    const removed = new Set(deletedIds.map(id=>`${API}|video|${id}`));
    const removedIds = new Set(deletedIds);
    if (!isPlatform()) {
      for (const key of await remoteArchive.clearAbnormal(API, deletedIds)) { removed.add(key); removedIds.add(key.split('|').at(-1)); }
    }
    for (const row of savedCloudTasks(true).filter(abnormalTask)) {
      saveCloudTask({...row,historyHidden:true});removed.add(`cloud:${row.id}`);removedIds.add(row.id);
    }
    await flushCloudTasks();
    const pendingIds = new Set(visiblePendingTasks().filter(row=>!row.clone&&(!isPlatform()||row.cloud)).map(row=>row.id));
    for (const id of await submissionQueue.clearAbnormal(row=>pendingIds.has(row.id) && abnormalTask(row))) { removed.add(id);removedIds.add(id); }
    runtime.visibleLocalTasks = (runtime.visibleLocalTasks || []).filter(row=>!deletedIds.includes(row.id));
    if (removedIds.has(runtime.currentJob)) {
      runtime.currentJob = "";
      localStorage.removeItem("minimax-current-job");
    }
    if (removedIds.has(runtime.previewJobId)) {
      setPreview("Minimax H3 导演工作台", "开始创造你的想法");
      qs(".preview").classList.add("is-welcome");
    }
    runtime.tasksLoadedAt = 0;
    runtime.taskSignature = "";
    status.textContent = (removed.size ? `已清理 ${removed.size} 条异常任务` : "没有需要清理的异常任务") + (backendError ? `；${backendError}` : '');
    await loadTasks();
  } catch (error) {
    status.textContent = `清理失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
});
installAssetPicker();
bindDesktopCommands();
bindWorksPagination();
bindInterface();
bindPerformanceSettings();
bindRemainingSettings();
bindAssistantSettings();
const qwenKeySettings = bindQwenKeySettings(root, {baseUrl:()=>LOCAL_API, startService:()=>runServiceCommand('start_service','启动中')});
qs('.task-rail-toggle').addEventListener('click', () => {
  const collapsed = qs('.workbench').classList.toggle('tasks-collapsed');
  const toggle = qs('.task-rail-toggle');
  toggle.setAttribute('aria-expanded', String(!collapsed));
  toggle.title = collapsed ? '展开生成任务' : '收起生成任务';
  qs('#videoTaskBody').inert = collapsed;
});
bindComputeNodeSettings();
bindCloudSettings();
bindWorkViewer();
qs('.preview-copy-error').addEventListener('click',async event=>{
  const button=event.currentTarget;
  try{await navigator.clipboard.writeText(qs('.preview-message').textContent);button.textContent='已复制';}
  catch{button.textContent='请手动选择文字复制';}
});
async function restoreSavedVideoState() {
  try {
    const saved = await invoke('restore_workbench_state');
    remoteArchive.restore(saved);
    for (const [kind,key,id] of [['cloud-tasks',window.MinimaxBrowser ? 'minimax-browser-cloud-h3-tasks' : 'minimax-cloud-h3-tasks','id'],['upscales','minimax-upscale-records','sourceId']]) {
      const disk = saved[kind === 'cloud-tasks' ? 'cloudTasks' : 'upscales'] || [];
      let local = [];
      try { local = JSON.parse(localStorage.getItem(key) || '[]'); } catch {}
      const rows = kind === 'cloud-tasks'
        ? mergeCloudTasks(disk, local)
        : [...new Map([...disk, ...local].filter(item => item?.[id]).map(item => [item[id],item])).values()];
      localStorage.setItem(key, JSON.stringify(rows));
      await invoke('persist_workbench_state', {kind,data:rows});
    }
  } catch (error) { qs('.workbench-subtitle').textContent = `任务状态恢复失败：${error}`; }
}

async function restoreBrowserImageReference() {
  if (!window.MinimaxBrowser) return;
  const imageId = new URLSearchParams(location.search).get('image_id');
  if (!imageId) return;
  if (!/^[a-f0-9]{32}$/.test(imageId)) throw new Error('来源图片 ID 无效');
  const response = await fetch(`${API}/api/image-studio/images/${imageId}/file`);
  if (!response.ok) throw new Error('来源图片不可用');
  assetPicker.addFiles([new File([await response.blob()],`qwen-${imageId}.png`,{type:'image/png'})],{mode:'references'});
  assetPicker.setMode('references');showPage('workbench');
  const url = new URL(location.href);url.searchParams.delete('image_id');history.replaceState(null,'',url);
}
const settingsStartup = restoreSavedVideoState().then(refreshPersonalIdentity).then(loadPerformanceSettings);
const taskStartup = settingsStartup.then(loadConfig).then(restoreBrowserImageReference)
  .catch(error => {qs('.preflight-state').textContent=cloudErrorText(error);})
  .then(async()=>{taskStateReady=true;renderCachedTasks();await submissionQueue.reconcile(savedCloudTasks(true));await submissionQueue.start();await refreshCloudBalance().catch(()=>{});});
const overviewPoller = createPoller(refreshOverview, () => runtime.page === "overview" ? 5000 : 15000);
const tasksPoller = createPoller(async()=>{
  if(!taskStateReady)return;
  if(isNode())await remoteArchive.watch(API).catch(notifyError);
  remoteArchive.sync().catch(()=>{});
  await loadTasks();
}, () => runtime.activeJobIds.size ? 3000 : 15000, () => true);
setInterval(() => { if (isCloud() && !document.hidden) refreshCloudBalance().catch(() => {}); }, 60000);
const worksPoller = createPoller(() => Date.now() - runtime.worksLoadedAt >= 5000 ? loadWorks({ background: true }) : Promise.resolve(), () => 5000, () => !document.hidden && runtime.page === "works");
let statsLoadedAt = 0;
const statsPoller = createPoller(async () => { if (Date.now() - statsLoadedAt < 60000) return; await loadTodayCompleted(); statsLoadedAt = Date.now(); }, () => 60000, () => !document.hidden && runtime.page === "overview" && Boolean(runtime.serviceStatus?.running));
const pollers = [overviewPoller, tasksPoller, worksPoller, statsPoller];
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { pollers.filter(poller=>poller!==tasksPoller).forEach((poller) => poller.pause()); tasksPoller.wake(); qs(".preview-media").pause(); closePlayer(); }
  else pollers.forEach((poller) => poller.wake());
});
qsa(".dock button[data-target], .goto-workbench, .goto-works, .goto-settings").forEach((button) => button.addEventListener("click", () => { worksPoller.wake(); statsPoller.wake(); }));
taskStartup.then(()=>pollers.forEach((poller) => poller.wake()));

qsa('[data-quick-mode]').forEach(button => button.addEventListener('click', () => assetPicker.setMode(button.dataset.quickMode)));
intercept(qs('.quick-open-output'), () => isNode() ? showPage('works') : invoke('open_generated_directory'));
intercept(qs('.quick-performance'), () => { showPage('settings'); showSettingsGroup('generation', qs('[data-setting-pane="performance"]')); });
for (const selector of ['.work-prompt', '.duration-input', '.steps-input', '.seed-input', '.resolution-select']) {
  qs(selector).addEventListener('input', saveDraft);
  qs(selector).addEventListener('change', saveDraft);
}
qsa('[data-work-mode]').forEach(button => button.addEventListener('click', saveDraft));
setInterval(() => {
  const preview = qs(".preview");
  const tip = qs(".preview-tip");
  if (document.hidden || runtime.page !== "workbench" || !preview.classList.contains("is-loading")) return;
  tip.hidden = false;
  tip.textContent = currentH3Tip(qs(".preview-title").textContent);
}, 5200);
setInterval(() => { if (!document.hidden && runtime.page === 'overview') renderCurrentTaskTimer(); }, 1000);
setInterval(() => {
  if (!document.hidden && runtime.page === 'workbench' && !qs('.preview-copy').hidden) {
    globalThis.GenerationEta.render(qs('.generation-eta'), runtime.previewEtaJob);
  }
}, 1000);
window.addEventListener('unhandledrejection', event => { event.preventDefault(); notifyError(event.reason); });
async function deleteLibraryWork(work) {
  if(work.remoteNode){await invoke('remote_archive_delete',{archiveKey:work.archiveKey});remoteArchive.removeLocal(work.archiveKey);return;}
  if (isImageWork(work)) {
    const service = await invoke('service_status');
    if (!isNode() && service?.running === false) await invoke('delete_local_image_work', {imageId:work.id});
    else await api(`/api/image-studio/images/${encodeURIComponent(work.id)}`, {method:'DELETE'});
  } else await api(`/api/works/${encodeURIComponent(work.id)}`, {method:'DELETE'});
}

async function downloadWork(work) {
  if(work.remoteNode && work.localPath){
    const result=await invoke('download_work',{sourceFilename:work.filename,suggestedFilename:work.filename});
    if(result.saved)window.alert(`作品已保存到：\n${result.path}`);return;
  }
  if (work.cloud) {
    if (work.isUpscaleVariant) {
      const result = await invoke('download_work', {sourceFilename:work.filename,suggestedFilename:work.filename});
      if (result.saved) window.alert(`作品已保存到：\n${result.path}`);
      return;
    }
    let sourceFilename = work.filename;
    if (work.output?.startsWith('https://')) {
      const localPath = await invoke('cloud_h3_download', {taskId:work.id,url:work.output,filename:work.filename || null});
      sourceFilename = localPath.split(/[\\/]/).pop();
      saveCloudTask({...work,localPath,filename:sourceFilename});
    }
    const result = await invoke('download_work', {sourceFilename,suggestedFilename:sourceFilename});
    if (result.saved) window.alert(`作品已保存到：\n${result.path}`);
    return;
  }
  if (runtime.settings?.download_mode === "quick" && !runtime.settings.download_directory) {
    renderDownloadSettings(await invoke("pick_download_directory"));
    if (!runtime.settings.download_directory) return;
  }
  const sourceFilename = isImageWork(work) ? work.filename : decodeURIComponent(String(work.output || "").split('/').pop());
  if (!sourceFilename) throw new Error("作品文件名无效");
  const result = isNode()
    ? await invoke('download_node_work', {baseUrl:API,output:isImageWork(work) ? work.output : `/outputs/${sourceFilename}`,suggestedFilename:work.filename || sourceFilename})
    : await invoke("download_work", {sourceFilename,suggestedFilename:work.filename || sourceFilename});
  if (result.saved) window.alert(`作品已保存到：\n${result.path}`);
}
