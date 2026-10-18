import { SubmissionQueue } from './submission-queue.js';
import { bindResolutionSelectors } from './resolution-selectors.js';
import { WorksBrowser, isImageWork } from './works-browser.js';
const statusText = document.querySelector("#statusText");
const jobText = document.querySelector("#jobText");
const promptInput = document.querySelector("#prompt");
const promptOptimizeState = document.querySelector("#promptOptimizeState");
const compiledPromptInput = document.querySelector("#compiledPrompt");
const resolutionInput = document.querySelector("#resolution");
const resolutionControls = bindResolutionSelectors(resolutionInput, document.querySelector('#aspect'), document.querySelector('#resolutionSize'));
const durationInput = document.querySelector("#duration");
const stepsInput = document.querySelector("#steps");
const seedInput = document.querySelector("#seed");
const firstFrameInput = document.querySelector("#firstFrame");
const lastFrameInput = document.querySelector("#lastFrame");
const firstUploadBox = document.querySelector("#firstUploadBox");
const lastUploadBox = document.querySelector("#lastUploadBox");
const firstRemove = document.querySelector("#firstRemove");
const lastRemove = document.querySelector("#lastRemove");
const firstPreview = document.querySelector("#firstPreview");
const lastPreview = document.querySelector("#lastPreview");
const modeTabs = document.querySelectorAll("[data-mode]");
const keyframePanel = document.querySelector("#keyframePanel");
const referencePanel = document.querySelector("#referencePanel");
const referenceGrid = document.querySelector("#referenceGrid");
const addReferenceButton = document.querySelector("#addReference");
const referenceFilesInput = document.querySelector("#referenceFiles");
const referenceVideoFilesInput = document.querySelector("#referenceVideoFiles");
const referenceAudioFilesInput = document.querySelector("#referenceAudioFiles");
const referenceHint = document.querySelector("#referenceHint");
const referenceVideoGrid = referenceGrid;
const addReferenceVideoButton = document.querySelector("#addReferenceVideo");
const referenceVideoHint = document.querySelector("#referenceVideoHint");
const addReferenceAudioButton = document.querySelector("#addReferenceAudio");
const referenceAudioHint = document.querySelector("#referenceAudioHint");
const referenceHoverPreview = document.querySelector("#referenceHoverPreview");
const generateButton = document.querySelector("#generate");
const refreshButton = document.querySelector("#refreshStatus");
const refreshWorksButton = document.querySelector("#refreshWorks");
const showAllVideosButton = document.querySelector("#showAllVideos");
const video = document.querySelector("#video");
const stage = document.querySelector(".stage");
const swipeFeedback = document.querySelector("#swipeFeedback");
const emptyState = document.querySelector("#emptyState");
const waitingState = document.querySelector("#waitingState");
const stageStatus = document.querySelector("#stageStatus");
const waitTipText = document.querySelector("#waitTipText");
const generationPhaseText = document.querySelector("#generationPhaseText");
const generationPhaseTime = document.querySelector("#generationPhaseTime");
const generationProgressTrack = document.querySelector("#generationProgressTrack");
const generationProgressBar = document.querySelector("#generationProgressBar");
const generationProgressLabel = document.querySelector("#generationProgressLabel");
const generationSteps = document.querySelector("#generationSteps");
const generationDetail = document.querySelector("#generationDetail");
const generationNext = document.querySelector("#generationNext");
const download = document.querySelector("#download");
const timerText = document.querySelector("#timerText");
const comfortText = document.querySelector("#comfortText");
const works = document.querySelector("#works");
const worksSummary = document.querySelector("#worksSummary");
const resourceUpdated = document.querySelector("#resourceUpdated");
const cpuUsage = document.querySelector("#cpuUsage");
const memoryUsage = document.querySelector("#memoryUsage");
const diskUsage = document.querySelector("#diskUsage");
const gpuUsage = document.querySelector("#gpuUsage");
const workMenuLayer = document.querySelector("#workMenuLayer");
const allVideosModal = document.querySelector("#allVideosModal");
const allVideosContent = document.querySelector("#allVideosContent");
const allVideosCount = document.querySelector("#allVideosCount");
const closeAllVideosButton = document.querySelector("#closeAllVideos");
const videoOrderButtons = document.querySelectorAll("[data-video-order]");
const mobileDockButtons = document.querySelectorAll("[data-mobile-target]");
const taskList = document.querySelector('#taskList');
const worksBrowser = new WorksBrowser({reload: options => loadWorks(options), edit: item => reeditWork(item.id)});
let taskRenderSignature = '';
let editorRevision = 0;
let taskRequestInFlight = false;
let latestServerTasks = [];
let latestTaskItems = [];

let workThumbnailObserver = null;
let allVideosObserver = null;
let activeTimer = null;
let waitHintTimer = null;
let waitHintIndex = 0;
let resourceTimer = null;
let resourceRequestInFlight = false;
let worksRequestInFlight = false;
let worksRenderSignature = "";
let historyWorks = [];
let activeWorkOutput = "";
let outputTouchStart = null;
let outputPointerId = null;
let swipeFeedbackTimer = null;
let latestSubmitToken = 0;
let followedJobId = "";
const pollingJobIds = new Set();
let activeMode = "references";
let allVideosOrder = "desc";
let allVideosItems = [];
let allVideosTotal = 0;
let allVideosNextOffset = 0;
let allVideosHasMore = true;
let allVideosLoading = false;
let allVideosLoadError = "";
let allVideosLoadToken = 0;
let outputVideoWidth = 0;
let outputVideoHeight = 0;
let referenceHoverTimer = null;
let draggedReferenceSlot = null;
let submitInFlight = false;
let compiledPromptCache = { fingerprint: "", prompt: "", plan: {}, optimizer: null };
let nextPromptFileId = 1;
const promptFileIds = new WeakMap();
const referenceSlots = [];
const referenceVideoSlots = [];
const referenceAudioSlots = [];
const generationStageMemory = new Map();
const RESOURCE_REFRESH_MS = 1000;
const ALL_VIDEOS_PAGE_SIZE = 100;
const GENERATION_STAGES = [
  { id: "queue", title: "进入队列", next: "准备模型与生成参数" },
  { id: "prompt", title: "准备提示词", next: "准备模型与生成参数" },
  { id: "prepare", title: "准备模型", next: "理解参考素材与提示词" },
  { id: "encode", title: "理解素材", next: "开始扩散采样生成视频" },
  { id: "sample", title: "生成视频", next: "解码生成的画面与音频" },
  { id: "decode", title: "解码音画", next: "合成并保存最终视频" },
  { id: "save", title: "合成保存", next: "完成后自动显示视频" },
];
function generationStageIndex(data, index) { return data.phase === 'prompt' ? 1 : index > 0 ? index + 1 : 0; }
function generationStage(data, index) { return GENERATION_STAGES[generationStageIndex(data, index)]; }
const WAIT_HINTS = [
  "正在等待 H3 接手任务，页面保持打开即可。",
  "低显存模式会分段加载模型，任务开始后会显示生成耗时。",
  "等待中的任务会自动开始，不需要重复点击生成。",
  "生成完成后会自动出现在输出面板和作品管理里。",
];
const VIDEO_DRAFT_KEY = 'minimax-web-video-draft-v1';
function saveVideoDraft() {
  try { localStorage.setItem(VIDEO_DRAFT_KEY, JSON.stringify({prompt:promptInput.value, mode:activeMode,
    resolution:resolutionInput.value, duration:durationInput.value, steps:stepsInput.value, seed:seedInput.value})); }
  catch { /* 浏览器存储不可用时继续创作。 */ }
}

async function loadConfig() {
  const revision = editorRevision;
  const res = await fetch("/api/config");
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || '配置读取失败');
  resolutionInput.innerHTML = "";
  data.resolutions.forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    resolutionInput.appendChild(option);
  });
  let draft = {};
  try { draft = JSON.parse(localStorage.getItem(VIDEO_DRAFT_KEY) || '{}') || {}; } catch { /* 忽略损坏草稿。 */ }
  resolutionInput.value = data.resolutions.includes(draft.resolution) ? draft.resolution : data.defaults.resolution;
  resolutionControls.sync();
  if (revision === editorRevision) {
    durationInput.value = draft.duration ?? data.defaults.duration;
    stepsInput.value = draft.steps ?? data.defaults.steps;
    seedInput.value = draft.seed ?? '';
    promptInput.value = draft.prompt ?? '';
    if (['references','keyframes'].includes(draft.mode)) setMode(draft.mode);
  }
}

async function refreshStatus() {
  statusText.textContent = "正在检测本地后端...";
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (data.ready && data.h3_nodes.ok) {
      statusText.textContent = "H3 后端已就绪";
    } else {
      statusText.textContent = `未就绪：${data.h3_nodes.missing.join("，")}`;
    }
  } catch (err) {
    statusText.textContent = `检测失败：${err.message}`;
  }
}

const submissionQueue = new SubmissionQueue('web-video', async (draft, context) => {
  const compiled = await compileCurrentPrompt(draft);
  context.check();
  const body = new FormData();
  for (const key of ['mode','resolution','duration','steps','seed']) body.append(key, draft[key]);
  body.append('prompt', compiled.prompt);
  body.append('user_brief', draft.brief);
  body.append('prompt_plan', JSON.stringify(compiled.plan || {}));
  for (const asset of draft.manifest) body.append(draft.mode === 'references'
    ? {Picture:'reference_images',Video:'reference_videos',Audio:'reference_audios'}[asset.kind] : asset.role, asset.file);
  await context.submitting();
  const response = await fetch('/api/generate-upload', {method:'POST', body});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '提交失败');
  return data;
}, () => {
  const job = submissionQueue.rows().find(row => row.id === followedJobId);
  if (job) {
    if (['queued','running'].includes(job.status)) showWaitingState(job);
    else showOutputMessage(job.error || job.message, '任务已保留，其他排队任务会继续执行。');
  }
  loadWorks({force:true});
}, (pending, job) => {
  if (followedJobId === pending.id) { followedJobId = job.id; showWaitingState(job); }
  startJobPolling(job.id);
});
async function startGenerate() {
  if (submitInFlight) return;
  if (activeMode === 'references' && !selectedReferenceAssets().length) setMode('keyframes');
  const validationError = validateBeforeSubmit();
  if (validationError) { jobText.textContent = validationError; return; }
  const draft = {brief:promptInput.value, mode:activeMode, resolution:resolutionInput.value,
    duration:durationInput.value, steps:stepsInput.value, seed:seedInput.value.trim(),
    manifest:promptAssetManifest(), fingerprint:promptCompileFingerprint()};
  submitInFlight = true; generateButton.disabled = true;
  try {
    const job = await submissionQueue.add(draft, {mode:draft.mode, prompt:draft.brief, user_brief:draft.brief,
      resolution:draft.resolution, duration:Number(draft.duration), steps:Number(draft.steps)});
    followedJobId = job.id;
    activeWorkOutput = ''; updateActiveWorkCard(); setMobileSection('output');
    stopTimer(false); showWaitingState(job);
    setPromptOptimizeState('任务已加入队列，可继续添加任务', 'success');
  } catch (error) { showOutputMessage(error.message, '任务未提交，请重试。'); }
  finally { submitInFlight = false; generateButton.disabled = false; }
}

function setPromptOptimizeState(message, state = "") {
  promptOptimizeState.textContent = message;
  if (state) promptOptimizeState.dataset.state = state;
  else promptOptimizeState.removeAttribute("data-state");
}

function selectedReferenceAssets() {
  return [
    ...referenceSlots.filter((slot) => slot.file).map((slot) => ({ kind: "Picture", file: slot.file })),
    ...referenceVideoSlots.filter((slot) => slot.file).map((slot) => ({ kind: "Video", file: slot.file })),
    ...referenceAudioSlots.filter((slot) => slot.file).map((slot) => ({ kind: "Audio", file: slot.file })),
  ];
}

function promptAssetManifest() {
  if (activeMode === "references") {
    const counters = { Picture: 0, Video: 0, Audio: 0 };
    return selectedReferenceAssets().map((asset) => {
      const number = ++counters[asset.kind];
      return { ...asset, id: `${asset.kind.toLowerCase()}-${number}`, number, tag: `<${asset.kind} ${number}>`, name: asset.file.name };
    });
  }
  const frames = [
    { role: "first_frame", file: firstFrameInput.files[0] },
    { role: "last_frame", file: lastFrameInput.files[0] },
  ].filter((item) => item.file);
  return frames.map((item, index) => ({ ...item, kind: "Picture", id: item.role, number: index + 1, tag: `<Picture ${index + 1}>`, name: item.file.name }));
}

function promptCompileFingerprint() {
  const files = promptAssetManifest().map((asset) => {
    if (!promptFileIds.has(asset.file)) promptFileIds.set(asset.file, nextPromptFileId++);
    return [asset.id, promptFileIds.get(asset.file), asset.file.name, asset.file.size, asset.file.lastModified];
  });
  return JSON.stringify({ prompt: promptInput.value, mode: activeMode, duration: durationInput.value, resolution: resolutionInput.value, files });
}

function isH3Prompt(text) {
  return /^(?:subject_definitions:|integrated_multimodal_description:|For the target video,|How the reference pictures align with the target video)/.test(String(text).trimStart());
}

function normalizePromptReferences(input, manifest) {
  const available = new Set(manifest.map((asset) => asset.tag));
  const resolve = (kind, number) => {
    const tag = `<${kind} ${Number(number)}>`;
    if (!available.has(tag)) throw new Error(`${tag} 没有对应的已上传素材，请检查编号或重新选择素材`);
    return tag;
  };
  return String(input)
    .replace(/<(Picture|Video|Audio)\s+(\d+)>/gi, (_, kind, number) => resolve(kind[0].toUpperCase() + kind.slice(1).toLowerCase(), number))
    .replace(/(?:参考)?(图片|图|视频|音频)\s*([1-9]\d*)/g, (_, word, number) => resolve(word === "图片" || word === "图" ? "Picture" : word === "视频" ? "Video" : "Audio", number));
}

function validateCompiledPrompt(text, mode, duration, manifest) {
  const prompt = String(text).trim();
  if (!prompt) throw new Error("H3 提示词不能为空");
  if (Array.from(prompt).length > 7000) throw new Error("H3 提示词超过 7000 字符");
  const available = new Set(manifest.map((asset) => asset.tag));
  for (const match of prompt.matchAll(/<(Picture|Video|Audio) (\d+)>/g)) {
    if (!available.has(match[0])) throw new Error(`${match[0]} 没有对应素材，请重新选择`);
  }
  for (const asset of manifest) {
    if (!prompt.includes(asset.tag)) throw new Error(`最终 H3 提示词缺少 ${asset.tag} 引用`);
  }
  const expected = mode === "references"
    ? ["subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"]
    : ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"];
  const expectedSet = new Set(expected);
  const headers = [...prompt.matchAll(/^([a-z_]+):/gm)].map((match) => match[1]).filter((name) => expectedSet.has(name));
  if (JSON.stringify(headers) !== JSON.stringify(expected)) throw new Error(`H3 字段应按顺序且各出现一次：${expected.join(" → ")}`);
  expected.forEach((name, index) => {
    const start = prompt.search(new RegExp(`^${name}:`, "m")) + name.length + 1;
    const end = index + 1 < expected.length ? prompt.search(new RegExp(`^${expected[index + 1]}:`, "m")) : prompt.length;
    if (!prompt.slice(start, end).trim()) throw new Error(`${name} 内容不能为空`);
  });
  if (mode !== "references") {
    const first = manifest.some((asset) => asset.role === "first_frame");
    const last = manifest.some((asset) => asset.role === "last_frame");
    if (first && !last && !prompt.startsWith("For the target video, at 0.00 seconds into the target video, <Picture 1>")) throw new Error("仅首帧模式缺少 H3 首帧对齐指令");
    if (last && (!prompt.startsWith("How the reference pictures align with the target video") || !prompt.includes(`${Number(duration).toFixed(2)}-second mark`))) throw new Error("尾帧模式缺少正确的 H3 对齐指令");
  }
  const timelineField = mode === "references" ? "detailed_description" : "integrated_multimodal_description";
  const timeline = prompt.split(new RegExp(`^${timelineField}:`, "m"))[1]?.split(/^overall_soundscape:/m)[0] || "";
  const shots = [...timeline.matchAll(/\[Shot (\d+)\](?: At (\d{2}):(\d{2})\.(\d{3}))?/g)];
  if (!shots.length || shots[0][1] !== "1" || shots[0][2]) throw new Error("首镜须为不带切换时间的 [Shot 1]");
  let previous = 0;
  for (let index = 1; index < shots.length; index += 1) {
    const shot = shots[index];
    const time = Number(shot[2]) * 60 + Number(shot[3]) + Number(shot[4]) / 1000;
    if (Number(shot[1]) !== index + 1 || !shot[2] || !Number.isFinite(time) || time <= previous || time >= Number(duration)) throw new Error(`镜头 ${index + 1} 的编号或切换时间无效`);
    previous = time;
  }
  if (mode === "references" && manifest.some((asset) => asset.kind !== "Audio") && !/<Subject \d+>/.test(prompt)) throw new Error("多参考 H3 提示词未归纳可见主体");
  return prompt;
}

async function fileToPromptImage(file, tag) {
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 4096 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return { tag, name: file.name, dataUrl: canvas.toDataURL("image/jpeg", 0.86) };
  } finally {
    bitmap.close();
  }
}

async function fileToPromptVideoFrames(file, tag) {
  const url = URL.createObjectURL(file);
  const source = document.createElement("video");
  source.preload = "auto";
  source.muted = true;
  source.playsInline = true;
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(`视频 ${file.name} 解码超时`)), 12000);
      source.onloadedmetadata = () => { clearTimeout(timeout); resolve(); };
      source.onerror = () => { clearTimeout(timeout); reject(new Error(`无法解析视频 ${file.name}`)); };
      source.src = url;
    });
    if (!Number.isFinite(source.duration) || source.duration <= 0 || !source.videoWidth) throw new Error(`视频 ${file.name} 缺少有效画面`);
    if (source.readyState < 2) await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(`视频 ${file.name} 读取首帧超时`)), 10000);
      source.onloadeddata = () => { clearTimeout(timeout); resolve(); };
      source.onerror = () => { clearTimeout(timeout); reject(new Error(`视频 ${file.name} 读取首帧失败`)); };
    });
    const fractions = source.duration > 6 ? [0, 0.25, 0.5, 0.75, 1] : [0, 0.5, 1];
    const times = [...new Set(fractions.map((fraction) => Number(Math.min(Math.max(0, source.duration - 0.1), source.duration * fraction).toFixed(2))))];
    const scale = Math.min(1, 1024 / Math.max(source.videoWidth, source.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(source.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(source.videoHeight * scale));
    const frames = [];
    for (const time of times) {
      if (Math.abs(source.currentTime - time) > 0.025 || !source.readyState) await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error(`视频 ${file.name} 定位画面超时`)), 10000);
        source.onseeked = () => { clearTimeout(timeout); resolve(); };
        source.onerror = () => { clearTimeout(timeout); reject(new Error(`视频 ${file.name} 定位画面失败`)); };
        source.currentTime = time;
      });
      canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
      frames.push({ tag, name: file.name, timeSeconds: time, dataUrl: canvas.toDataURL("image/jpeg", 0.8) });
    }
    return { frames, duration: source.duration };
  } finally {
    source.removeAttribute("src");
    source.load();
    URL.revokeObjectURL(url);
  }
}

async function mediaDuration(file, tag) {
  const url = URL.createObjectURL(file);
  const media = document.createElement(tag);
  try {
    return await new Promise((resolve) => {
      const timeout = setTimeout(() => resolve(null), 5000);
      media.onloadedmetadata = () => { clearTimeout(timeout); resolve(Number.isFinite(media.duration) ? media.duration : null); };
      media.onerror = () => { clearTimeout(timeout); resolve(null); };
      media.src = url;
    });
  } finally {
    media.removeAttribute("src");
    media.load();
    URL.revokeObjectURL(url);
  }
}

async function compileCurrentPrompt(draft) {
  const fingerprint = draft.fingerprint;
  const statusResponse = await fetch("/api/prompt-optimizer/status", { cache: "no-store" });
  const status = await statusResponse.json();
  if (!statusResponse.ok) throw new Error(status.detail || "读取 AI 助手配置失败");
  const raw = draft.brief.trim();
  const manifest = draft.manifest;
  if (!status.enabled) {
    if (!raw) throw new Error("未配置 AI 助手，请填写生成提示词");
    if (Array.from(draft.brief).length > 7000) throw new Error("提示词超过 7000 字符");
    normalizePromptReferences(raw, manifest);
    compiledPromptCache = { fingerprint, prompt: draft.brief, plan: {}, optimizer: { source: "passthrough", name: "原始提示词" } };
    if (fingerprint === promptCompileFingerprint()) compiledPromptInput.value = draft.brief;
    return { ...compiledPromptCache, reused: false };
  }
  if (compiledPromptCache.optimizer?.source !== "passthrough" && compiledPromptCache.fingerprint === fingerprint && compiledPromptCache.prompt) return { ...compiledPromptCache, reused: false };
  if (isH3Prompt(raw)) {
    const prompt = validateCompiledPrompt(raw, draft.mode, Number(draft.duration), manifest);
    compiledPromptCache = { fingerprint, prompt, plan: {}, optimizer: { source: "direct", name: "现有 H3 提示词" } };
    if (fingerprint === promptCompileFingerprint()) compiledPromptInput.value = prompt;
    return { ...compiledPromptCache, reused: true };
  }
  if (!raw && !manifest.some((asset) => asset.kind === "Picture" || asset.kind === "Video")) throw new Error("智能创作至少需要一张图片或视频");
  const normalizedPrompt = normalizePromptReferences(raw, manifest);
  const images = await Promise.all(manifest.filter((asset) => asset.kind === "Picture").map((asset) => fileToPromptImage(asset.file, asset.tag)));
  const videoAssets = manifest.filter((asset) => asset.kind === "Video");
  const videoResults = await Promise.all(videoAssets.map((asset) => fileToPromptVideoFrames(asset.file, asset.tag)));
  const durations = Object.fromEntries(videoAssets.map((asset, index) => [asset.id, videoResults[index].duration]));
  const audioAssets = manifest.filter((asset) => asset.kind === "Audio");
  const audioDurations = await Promise.all(audioAssets.map((asset) => mediaDuration(asset.file, "audio")));
  audioAssets.forEach((asset, index) => { durations[asset.id] = audioDurations[index]; });
  const payload = {
    prompt: normalizedPrompt,
    mode: draft.mode,
    duration: Number(draft.duration),
    assets: `目标画幅与分辨率：${draft.resolution}\n${manifest.map((asset) => `${asset.tag} (${asset.role || asset.kind})：${asset.name}${durations[asset.id] ? `，${durations[asset.id].toFixed(2)} 秒` : ""}`).join("\n")}`,
    images,
    videoFrames: videoResults.flatMap((result) => result.frames),
    manifest: manifest.map(({ file, ...asset }) => ({ ...asset, duration: durations[asset.id] || null })),
  };
  const response = await fetch("/api/optimize-prompt", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || "自动优化提示词失败");
  const prompt = result.optimizer?.source === "passthrough" ? result.prompt : validateCompiledPrompt(result.prompt, draft.mode, Number(draft.duration), manifest);
  compiledPromptCache = {
    fingerprint,
    prompt,
    plan: result.plan && typeof result.plan === "object" ? result.plan : {},
    optimizer: result.optimizer && typeof result.optimizer === "object" ? result.optimizer : null,
  };
  if (fingerprint === promptCompileFingerprint()) compiledPromptInput.value = prompt;
  return { ...compiledPromptCache, reused: false };
}

async function freeBackendCacheFromWorkbench() {
  jobText.textContent = "正在释放后端缓存...";
  try {
    const res = await fetch("/api/comfy/free", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "释放后端缓存失败");
    jobText.textContent = data.message || "已释放后端缓存";
    await refreshResources();
    return true;
  } catch (err) {
    jobText.textContent = `释放后端缓存失败：${err.message}`;
    showOutputMessage(`释放后端缓存失败：${err.message}`, "当前没有提交生成任务，请处理后再重试。");
    return false;
  }
}

function validateBeforeSubmit() {
  const hasVisualAsset = activeMode === "references"
    ? referenceSlots.some((slot) => slot.file) || referenceVideoSlots.some((slot) => slot.file)
    : Boolean(firstFrameInput.files[0] || lastFrameInput.files[0]);
  if (!promptInput.value.trim() && !hasVisualAsset) return "请输入创作需求，或至少上传一张图片/一个视频进行智能创作";
  if (activeMode === "references") {
    const selectedImageCount = referenceSlots.filter((slot) => slot.file).length;
    const selectedVideoCount = referenceVideoSlots.filter((slot) => slot.file).length;
    const selectedAudioCount = referenceAudioSlots.filter((slot) => slot.file).length;
    if (selectedImageCount + selectedVideoCount + selectedAudioCount === 0) {
      const hasKeyframeImage = Boolean(firstFrameInput.files[0] || lastFrameInput.files[0]);
      return hasKeyframeImage
        ? "多参考素材模式需要上传参考图片、视频或音频，首帧/尾帧图片只用于首尾帧模式"
        : "多参考素材模式至少需要上传 1 个参考图片、视频或音频";
    }
  }
  return "";
}

function startJobPolling(jobId) {
  if (!jobId || pollingJobIds.has(jobId)) return;
  pollingJobIds.add(jobId);
  pollJob(jobId);
}

async function pollJob(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    const data = await res.json();
    const isFollowed = jobId === followedJobId;
    const preserveWorkPlayback = isHistoryWorkPlaybackActive();
    if (isFollowed) {
      const backgroundPrefix = preserveWorkPlayback ? "后台任务 · " : "";
      jobText.textContent = `${backgroundPrefix}${formatJobStatus(data)}`;
      if (!preserveWorkPlayback) updateJobTimer(data);
    }

    if (data.status === "done") {
      pollingJobIds.delete(jobId);
      generationStageMemory.delete(jobId);
      if (isFollowed && !preserveWorkPlayback) {
        const src = `${data.output}?t=${Date.now()}`;
        video.src = src;
        download.href = src;
        download.download = "MiniMax_H3.mp4";
        video.hidden = false;
        download.hidden = true;
        emptyState.hidden = true;
        hideWaitingState();
        comfortText.textContent = `生成完成，耗时 ${formatDuration(Math.round(data.elapsed_seconds || 0))}。`;
        timerText.textContent = `生成耗时 ${formatDuration(Math.round(data.elapsed_seconds || 0))}`;
        stopTimer(false);
      }
      loadWorks({ force: true });
      return;
    }

    if (data.status === "error") {
      pollingJobIds.delete(jobId);
      generationStageMemory.delete(jobId);
      if (isFollowed && !preserveWorkPlayback) {
        showOutputMessage(data.error || "生成失败，请查看 logs/comfyui.log", "任务失败了，日志里会保留原因。");
        stopTimer(false);
      }
      loadWorks({ force: true });
      return;
    }

    if (isFollowed && !preserveWorkPlayback) showWaitingState(data);
    loadWorks();
    setTimeout(() => pollJob(jobId), 2000);
  } catch (err) {
    pollingJobIds.delete(jobId);
    if (jobId === followedJobId) {
      jobText.textContent = `轮询失败：${err.message}`;
      if (!isHistoryWorkPlaybackActive()) {
        hideWaitingState();
        stopTimer(false);
      }
    }
  }
}

function statusLabel(status) {
  return { queued: "等待生成中", running: "生成中", done: "已完成", error: "失败" }[status] || status;
}

function formatJobStatus(data) {
  const runText = formatSeconds(data.run_seconds);
  if (data.status === "running") {
    if (data.warning?.message) return `生成中 - 已运行 ${runText || "较长时间"}，${data.warning.message}`;
    return runText
      ? `生成中 - 已运行 ${runText}，${data.message}`
      : `${statusLabel(data.status)} - ${data.message}`;
  }
  if (data.status === "queued" && Number.isFinite(Number(data.jobs_ahead))) {
    const ahead = Number(data.jobs_ahead);
    return ahead > 0
      ? `等待生成中 - 前方还有 ${ahead} 个任务`
      : "等待生成中 - 即将开始";
  }
  return `${statusLabel(data.status)} - ${data.message}`;
}

function generationNodeInfo(node) {
  const number = Number(node);
  if ([101, 102].includes(number) || (number >= 201 && number <= 209) || (number >= 301 && number <= 313) || (number >= 401 && number <= 403)) {
    return { index: 2, detail: "正在读取并整理参考图片、视频或音频" };
  }
  if (number === 104) return { index: 2, detail: "正在理解参考素材并编码提示词关系" };
  if (number === 14) return { index: 3, detail: "扩散采样器正在逐步生成视频帧" };
  if ([10, 23].includes(number)) return { index: 4, detail: number === 10 ? "正在解码生成的视频画面" : "正在解码并对齐生成音频" };
  if ([91, 92].includes(number)) return { index: 5, detail: number === 91 ? "正在合成音画视频流" : "正在写入最终视频文件" };
  if ([6, 7, 8, 9, 11, 13, 15, 16, 17, 24, 501, 502, 503, 504].includes(number)) {
    return { index: 1, detail: "正在加载生成模型、VAE 与采样参数" };
  }
  return { index: 1, detail: `H3 正在处理后端节点 ${node}` };
}

function classifyGenerationStage(data) {
  if (data.phase === "prompt") return {index: 0, detail: "正在准备提示词，可继续添加任务"};
  if (data.status === "queued") {
    const ahead = Number(data.jobs_ahead) || 0;
    return { index: 0, detail: ahead > 0 ? `任务已安全排队，前方还有 ${ahead} 个任务` : "任务已进入队列，等待 GPU 执行资源" };
  }
  if (data.status === "done") return { index: 5, detail: "视频已合成并保存完成" };

  const message = String(data.message || "");
  let result = { index: 1, detail: message || "正在准备 H3 生成任务" };
  const node = message.match(/节点\s*(\d+)/)?.[1];
  if (node) result = generationNodeInfo(node);
  if (/补齐|下载|权重|准备|启动/.test(message)) result = { index: 1, detail: message };
  if (/参考画面编码|参考音频编码|文本与多模态编码|理解素材/.test(message)) result = { index: 2, detail: message };
  if (/当前阶段|采样/.test(message) && !/补齐|下载|权重/.test(message)) result = { index: 3, detail: message };
  if (/解码/.test(message)) result = { index: 4, detail: message };
  if (/合成|封装|写入|保存/.test(message)) result = { index: 5, detail: message };

  const key = data.id || followedJobId || "pending-submission";
  const previous = generationStageMemory.get(key) ?? 0;
  const measuredStage = {sample: 3, decode: 4, save: 5}[data.sampling_timing?.stage];
  result.index = measuredStage ?? Math.max(previous, result.index);
  generationStageMemory.set(key, result.index);
  return result;
}

function actualGenerationProgress(data, stageIndex) {
  const timing = data.sampling_timing;
  if (stageIndex === 3 && timing?.stage === 'sample' && timing.total_steps > 0) {
    return Math.round(100 * timing.completed_steps / timing.total_steps);
  }
  const value = Number(data.progress);
  const message = String(data.message || "");
  const appliesToStage = stageIndex === 3 || stageIndex === 1 && /补齐|下载|权重/.test(message);
  return appliesToStage && Number.isFinite(value) && value > 0 ? Math.max(0, Math.min(100, value)) : 0;
}

function renderGenerationProgress(data) {
  generationEtaJob = data;
  globalThis.GenerationEta.render(document.querySelector('#generationEta'), data);
  const state = classifyGenerationStage(data);
  const stage = generationStage(data, state.index);
  const runText = formatSeconds(data.run_seconds);
  const jobsAhead = Number(data.jobs_ahead) || 0;
  generationPhaseText.textContent = `步骤 ${generationStageIndex(data, state.index) + 1} / ${GENERATION_STAGES.length} · ${stage.title}`;
  generationPhaseTime.textContent = data.status === "queued"
    ? jobsAhead > 0 ? `前方 ${jobsAhead} 个` : "即将开始"
    : runText ? `已运行 ${runText}` : "刚刚开始";

  generationSteps.querySelectorAll("[data-progress-step]").forEach((element, index) => {
    element.classList.toggle("is-done", index < generationStageIndex(data, state.index));
    element.classList.toggle("is-active", index === generationStageIndex(data, state.index));
  });

  const progress = actualGenerationProgress(data, state.index);
  const hasActualProgress = progress > 0;
  generationProgressTrack.classList.toggle("is-indeterminate", !hasActualProgress);
  if (hasActualProgress) {
    generationProgressBar.style.width = `${progress}%`;
    generationProgressTrack.setAttribute("aria-valuemin", "0");
    generationProgressTrack.setAttribute("aria-valuemax", "100");
    generationProgressTrack.setAttribute("aria-valuenow", String(progress));
    generationProgressLabel.textContent = `${state.index === 3 ? "当前采样" : "当前阶段"} ${progress}% · 后端真实进度`;
  } else {
    generationProgressBar.style.removeProperty("width");
    generationProgressTrack.removeAttribute("aria-valuenow");
    generationProgressLabel.textContent = data.status === "queued"
      ? "等待执行资源，不显示虚假预计百分比"
      : "当前阶段持续处理中，不估算虚假百分比";
  }
  generationDetail.textContent = state.detail;
  generationNext.textContent = `下一步：${stage.next}`;
  return state;
}

function showWaitingState(data) {
  video.hidden = true;
  download.hidden = true;
  emptyState.hidden = true;
  waitingState.hidden = false;
  waitingState.dataset.status = data.status || "queued";
  const generationState = renderGenerationProgress(data);
  stageStatus.textContent = getStageStatus(data, generationState);
  comfortText.textContent = getComfortMessage(data);
  renderWaitHint();
  startWaitHints();
}

function hideWaitingState() {
  generationEtaJob = null;
  waitingState.hidden = true;
  stopWaitHints();
}

function showOutputMessage(message, comfort) {
  video.hidden = true;
  download.hidden = true;
  hideWaitingState();
  emptyState.hidden = false;
  emptyState.textContent = message;
  comfortText.textContent = comfort;
}

function getStageStatus(data, generationState = classifyGenerationStage(data)) {
  const runText = formatSeconds(data.run_seconds);
  if (data.warning?.message) return data.warning.message;
  if (data.status === "running") return runText
    ? `${generationStage(data, generationState.index).title} · 已运行 ${runText}`
    : generationStage(data, generationState.index).title;
  if (Number(data.jobs_ahead) > 0) {
    return `等待生成中，前方还有 ${Number(data.jobs_ahead)} 个任务`;
  }
  return "等待生成中";
}

function getComfortMessage(data) {
  if (data.warning?.kind === "long_running") {
    return "这类长任务可能仍会完成。若 GPU 占用长时间为 0，优先查看 ComfyUI 日志后再决定是否重启服务。";
  }
  if (data.status === "running") {
    const index = classifyGenerationStage(data).index;
    return [
      "任务已进入执行流程，正在等待第一项准备工作。",
      "低显存模式会分段加载模型，这一步可能较安静，但任务仍在继续。",
      "正在分析并编码参考素材；素材越多，这一步通常越久。",
      "视频采样通常是最耗时的一步，后端有真实采样进度时会显示百分比。",
      "采样已经完成，正在把潜空间结果解码为可播放的音画。",
      "已经进入最后收尾，正在合成并写入视频文件。",
    ][index];
  }
  if (Number(data.jobs_ahead) > 0) return "任务已进入队列，前面的任务完成后会自动开始。";
  return "任务已进入队列，低显存模式下模型会分段换入，请保持页面打开。";
}

function startWaitHints() {
  if (waitHintTimer) return;
  waitHintTimer = setInterval(() => {
    waitHintIndex = (waitHintIndex + 1) % WAIT_HINTS.length;
    renderWaitHint();
  }, 4000);
}

function stopWaitHints() {
  if (waitHintTimer) clearInterval(waitHintTimer);
  waitHintTimer = null;
  waitHintIndex = 0;
}

function renderWaitHint() {
  waitTipText.textContent = WAIT_HINTS[waitHintIndex];
}

function formatSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return "";
  return formatDuration(Math.max(0, Math.round(seconds)));
}

function updateJobTimer(data) {
  if (data.status === "running") {
    startTimer(data.started_at || Date.now() / 1000);
    return;
  }
  stopTimer(false);
  timerText.textContent = "等待生成中";
}

let generationEtaJob = null;
function startTimer(startedAt) {
  if (activeTimer) clearInterval(activeTimer);
  const start = Number(startedAt) * 1000;
  const render = () => {
    const elapsed = Math.max(0, Math.floor((Date.now() - start) / 1000));
    timerText.textContent = `生成耗时 ${formatDuration(elapsed)}`;
    globalThis.GenerationEta.render(document.querySelector('#generationEta'), generationEtaJob);
  };
  render();
  activeTimer = setInterval(render, 1000);
}

function stopTimer(reset = true) {
  if (activeTimer) clearInterval(activeTimer);
  activeTimer = null;
  if (reset) timerText.textContent = "等待生成中";
}

function formatDuration(seconds) {
  const m = String(Math.floor(seconds / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function bindPreview(input, img, removeButton) {
  input.addEventListener("change", () => {
    const file = input.files[0];
    const box = input.closest(".upload-field")?.querySelector(".upload-box");
    const placeholder = input.closest(".upload-field")?.querySelector(".upload-placeholder");
    if (!file) {
      resetUploadPreview(input, img, removeButton);
      return;
    }
    if (img.dataset.previewUrl) URL.revokeObjectURL(img.dataset.previewUrl);
    const previewUrl = URL.createObjectURL(file);
    img.src = previewUrl;
    img.dataset.previewUrl = previewUrl;
    img.hidden = false;
    box?.classList.add("has-image");
    if (placeholder) placeholder.textContent = "点击或拖入替换";
    removeButton.hidden = false;
  });
}

function bindRemoveUpload(removeButton, input, img) {
  removeButton.addEventListener("click", (event) => {
    event.stopPropagation();
    input.value = "";
    resetUploadPreview(input, img, removeButton);
  });
}

function resetUploadPreview(input, img, removeButton) {
  const box = input.closest(".upload-field")?.querySelector(".upload-box");
  const placeholder = input.closest(".upload-field")?.querySelector(".upload-placeholder");
  if (img.dataset.previewUrl) {
    URL.revokeObjectURL(img.dataset.previewUrl);
    delete img.dataset.previewUrl;
  }
  img.hidden = true;
  img.removeAttribute("src");
  box?.classList.remove("has-image");
  if (placeholder) placeholder.textContent = "点击或拖入图片";
  removeButton.hidden = true;
}

function bindUploadBox(box, input) {
  box.addEventListener("click", (event) => {
    event.stopPropagation();
    input.click();
  });
}

function bindUploadDrop(box, onImageDrop) {
  box.addEventListener("dragenter", (event) => {
    event.preventDefault();
    box.classList.add("drag-over");
  });
  box.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    box.classList.add("drag-over");
  });
  box.addEventListener("dragleave", (event) => {
    if (!box.contains(event.relatedTarget)) box.classList.remove("drag-over");
  });
  box.addEventListener("drop", (event) => {
    event.preventDefault();
    box.classList.remove("drag-over");
    const file = getDroppedImage(event);
    if (!file) {
      showUploadMessage("请拖入 png、jpg、jpeg 或 webp 图片");
      return;
    }
    onImageDrop(file);
  });
}

function getDroppedImage(event) {
  const file = Array.from(event.dataTransfer?.files || []).find(isSupportedImageFile);
  return file || null;
}

function isSupportedImageFile(file) {
  if (!file) return false;
  const supportedTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
  if (supportedTypes.has(file.type)) return true;
  return /\.(png|jpe?g|webp)$/i.test(file.name || "");
}

function setFileInput(input, file) {
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function showUploadMessage(message) {
  jobText.textContent = message;
}

function clearKeyframeInputs() {
  firstFrameInput.value = "";
  lastFrameInput.value = "";
  resetUploadPreview(firstFrameInput, firstPreview, firstRemove);
  resetUploadPreview(lastFrameInput, lastPreview, lastRemove);
}

function clearReferenceSlots() {
  while (referenceSlots.length) {
    removeReferenceSlot(referenceSlots[0]);
  }
}

function clearReferenceVideoSlots() {
  while (referenceVideoSlots.length) {
    removeReferenceVideoSlot(referenceVideoSlots[0]);
  }
}

function clearReferenceAudioSlots() {
  referenceAudioSlots.splice(0).forEach((slot) => slot.element.remove());
  updateReferenceAudioSlots();
}

function addReferenceAudioFiles(files) {
  const accepted = Array.from(files || []).filter(isSupportedAudioFile).slice(0, 3 - referenceAudioSlots.length);
  accepted.forEach((file) => {
    const element = document.createElement("div");
    element.className = "upload-field reference-audio-field";
    const name = document.createElement("span");
    name.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "upload-remove";
    remove.textContent = "移除";
    const slot = { file, element };
    remove.addEventListener("click", () => {
      referenceAudioSlots.splice(referenceAudioSlots.indexOf(slot), 1);
      element.remove();
      updateReferenceAudioSlots();
    });
    element.append(name, remove);
    referenceGrid.append(element);
    referenceAudioSlots.push(slot);
  });
  updateReferenceAudioSlots();
}

function updateReferenceAudioSlots() {
  addReferenceAudioButton.disabled = referenceAudioSlots.length >= 3;
  const count = referenceAudioSlots.length;
  updateNumberedHint(referenceAudioHint, "3", `${count ? `已选择 ${count} 个参考音频。` : ""}支持 MP3、WAV、M4A、AAC、FLAC、OGG，单个不超过 50MB；提示词中使用 <Audio 1> 引用。`);
}

function isSupportedAudioFile(file) {
  return Boolean(file) && (file.type.startsWith("audio/") || /\.(mp3|wav|m4a|aac|flac|ogg)$/i.test(file.name || ""));
}

function setMode(mode) {
  hideReferenceHoverPreview();
  activeMode = mode;
  modeTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });
  keyframePanel.hidden = mode !== "keyframes";
  referencePanel.hidden = mode !== "references";
  if (mode === "references" && referenceSlots.length === 0) {
    addReferenceSlot();
  }
}

function addReferenceSlot() {
  if (referenceSlots.length >= 9) return null;
  const slot = {
    file: null,
    previewUrl: "",
    previewVersion: 0,
    isLoadingPreview: false,
    elements: null,
  };
  referenceSlots.push(slot);
  referenceGrid.appendChild(createReferenceSlotElement(slot));
  updateReferenceSlots();
  return slot;
}

function removeReferenceSlot(slot) {
  const index = referenceSlots.indexOf(slot);
  if (index < 0) return;
  referenceSlots.splice(index, 1);
  if (slot?.previewUrl) URL.revokeObjectURL(slot.previewUrl);
  slot.previewVersion += 1;
  slot.elements?.field.remove();
  updateReferenceSlots();
}

function createReferenceSlotElement(slot) {
  const field = document.createElement("div");
  field.className = "upload-field reference-image-field";
  field.draggable = true;

  const label = document.createElement("span");

  const box = document.createElement("button");
  box.className = "upload-box";
  box.type = "button";
  box.draggable = true;

  const img = document.createElement("img");
  img.alt = "";
  img.hidden = true;
  img.draggable = false;

  const order = document.createElement("span");
  order.className = "reference-order";
  order.setAttribute("aria-hidden", "true");

  const placeholder = document.createElement("span");
  placeholder.className = "upload-placeholder";
  placeholder.textContent = "点击或拖入图片";

  const remove = document.createElement("button");
  remove.className = "upload-remove";
  remove.type = "button";
  remove.textContent = "移除";

  const input = document.createElement("input");
  input.className = "file-input";
  input.type = "file";
  input.accept = "image/png,image/jpeg,image/webp";

  box.append(img, order, placeholder);
  field.append(label, box, remove, input);
  slot.elements = { field, label, box, img, order, placeholder, remove, input };

  box.addEventListener("click", (event) => {
    event.stopPropagation();
    if (slot.file) input.click();
    else referenceFilesInput.click();
  });
  bindReferenceUploadDrop(box, slot);
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    removeReferenceSlot(slot);
  });
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (!file) return;
    setReferenceSlotFile(slot, file);
  });
  field.addEventListener("dragstart", (event) => {
    draggedReferenceSlot = slot;
    field.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
  });
  field.addEventListener("dragend", () => {
    draggedReferenceSlot = null;
    field.classList.remove("is-dragging");
    updateReferenceImageOrderFromDom();
  });
  field.addEventListener("dragover", (event) => {
    if (!draggedReferenceSlot || draggedReferenceSlot === slot) return;
    event.preventDefault();
    const draggedField = draggedReferenceSlot.elements.field;
    const rect = field.getBoundingClientRect();
    const insertAfter = event.clientX > rect.left + rect.width / 2;
    referenceGrid.insertBefore(draggedField, insertAfter ? field.nextSibling : field);
  });

  return field;
}

function updateReferenceImageOrderFromDom() {
  const ordered = Array.from(referenceGrid.querySelectorAll(".reference-image-field"))
    .map((field) => referenceSlots.find((slot) => slot.elements?.field === field))
    .filter(Boolean);
  referenceSlots.splice(0, referenceSlots.length, ...ordered);
  updateReferenceSlots();
}

function updateReferenceSlots() {
  referenceSlots.forEach((slot, index) => {
    updateReferenceSlotElement(slot, index);
  });
  addReferenceButton.disabled = referenceSlots.length >= 9;
  const selectedReferenceCount = referenceSlots.filter((slot) => slot.file).length;
  updateNumberedHint(
    referenceHint,
    "1",
    selectedReferenceCount
      ? `已选择 ${selectedReferenceCount} 张参考图；提示词中用 <Picture 1>、<Picture 2> 引用。`
      : "可上传最多 9 张参考图；提示词中用 <Picture 1>、<Picture 2> 引用。",
  );
}

function updateReferenceSlotElement(slot, index) {
  const elements = slot.elements;
  if (!elements) return;
  elements.label.textContent = `参考图 ${index + 1}`;
  elements.order.textContent = String(index + 1);
  elements.box.classList.toggle("has-image", Boolean(slot.previewUrl));
  elements.box.classList.toggle("is-loading", slot.isLoadingPreview);
  elements.box.setAttribute("aria-label", `选择参考图 ${index + 1}`);
  elements.remove.title = `移除参考图 ${index + 1}`;
  elements.img.hidden = !slot.previewUrl;
  if (slot.previewUrl && elements.img.src !== slot.previewUrl) {
    elements.img.src = slot.previewUrl;
  }
  if (!slot.previewUrl) {
    elements.img.removeAttribute("src");
  }
  elements.placeholder.textContent = slot.isLoadingPreview
    ? "正在生成预览..."
    : slot.file
      ? "点击或拖入替换"
      : "点击或拖入图片";
}

async function addReferenceFiles(files, preferredSlot = null) {
  const incoming = Array.from(files || []);
  const supported = incoming.filter(isSupportedImageFile);
  if (!supported.length) {
    showUploadMessage("请选择 png、jpg、jpeg 或 webp 图片");
    return;
  }

  const targets = [];
  if (preferredSlot && referenceSlots.includes(preferredSlot)) targets.push(preferredSlot);
  referenceSlots.forEach((slot) => {
    if (!slot.file && !targets.includes(slot)) targets.push(slot);
  });

  const selectedCount = referenceSlots.filter((slot) => slot.file).length;
  const replacementCapacity = preferredSlot?.file ? 1 : 0;
  const accepted = supported.slice(0, Math.min(supported.length, 9 - selectedCount + replacementCapacity));
  accepted.forEach(() => {
    if (targets.length >= accepted.length) return;
    const slot = addReferenceSlot();
    if (slot) targets.push(slot);
  });
  await Promise.all(accepted.map((file, index) => setReferenceSlotFile(targets[index], file)));

  const skipped = incoming.length - accepted.length;
  if (skipped > 0) {
    showUploadMessage(`已添加 ${accepted.length} 张参考图，另有 ${skipped} 张因格式不支持或超过 9 张上限未添加。`);
  }
}

function bindReferenceUploadDrop(box, slot) {
  box.addEventListener("dragenter", (event) => {
    event.preventDefault();
    box.classList.add("drag-over");
  });
  box.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    box.classList.add("drag-over");
  });
  box.addEventListener("dragleave", (event) => {
    if (!box.contains(event.relatedTarget)) box.classList.remove("drag-over");
  });
  box.addEventListener("drop", (event) => {
    event.preventDefault();
    box.classList.remove("drag-over");
    addReferenceFiles(event.dataTransfer?.files, slot);
  });
}

async function setReferenceSlotFile(slot, file) {
  if (!isSupportedImageFile(file)) {
    showUploadMessage("参考图只支持 png、jpg、jpeg 或 webp");
    return;
  }
  const version = slot.previewVersion + 1;
  slot.previewVersion = version;
  const oldPreviewUrl = slot.previewUrl;
  slot.file = file;
  slot.previewUrl = "";
  slot.isLoadingPreview = true;
  if (oldPreviewUrl) URL.revokeObjectURL(oldPreviewUrl);
  updateReferenceSlots();

  try {
    const nextPreviewUrl = await createServerThumbnailUrl(file);
    if (slot.previewVersion !== version) {
      URL.revokeObjectURL(nextPreviewUrl);
      return;
    }
    slot.previewUrl = nextPreviewUrl;
  } catch {
    if (slot.previewVersion !== version) return;
    slot.previewUrl = URL.createObjectURL(file);
  }
  slot.isLoadingPreview = false;
  updateReferenceSlots();
}

async function createServerThumbnailUrl(file) {
  const body = new FormData();
  body.append("image", file);
  const response = await fetch("/api/reference-thumbnail", { method: "POST", body });
  if (!response.ok) throw new Error("缩略图生成失败");
  return URL.createObjectURL(await response.blob());
}

function addReferenceVideoSlot() {
  if (referenceVideoSlots.length >= 3) return null;
  const slot = {
    file: null,
    previewUrl: "",
    elements: null,
  };
  referenceVideoSlots.push(slot);
  referenceVideoGrid.appendChild(createReferenceVideoSlotElement(slot));
  updateReferenceVideoSlots();
  return slot;
}

function removeReferenceVideoSlot(slot) {
  const index = referenceVideoSlots.indexOf(slot);
  if (index < 0) return;
  referenceVideoSlots.splice(index, 1);
  if (slot.previewUrl) URL.revokeObjectURL(slot.previewUrl);
  slot.elements?.field.remove();
  updateReferenceVideoSlots();
}

function createReferenceVideoSlotElement(slot) {
  const field = document.createElement("div");
  field.className = "upload-field reference-video-field";

  const label = document.createElement("span");

  const box = document.createElement("button");
  box.className = "upload-box";
  box.type = "button";

  const preview = document.createElement("video");
  preview.muted = true;
  preview.playsInline = true;
  preview.preload = "metadata";
  preview.hidden = true;
  preview.draggable = false;

  const order = document.createElement("span");
  order.className = "reference-order";
  order.setAttribute("aria-hidden", "true");

  const placeholder = document.createElement("span");
  placeholder.className = "upload-placeholder";
  placeholder.textContent = "点击或拖入视频";

  const remove = document.createElement("button");
  remove.className = "upload-remove";
  remove.type = "button";
  remove.textContent = "移除";

  const input = document.createElement("input");
  input.className = "file-input";
  input.type = "file";
  input.accept = "video/mp4,video/quicktime,.mp4,.mov";

  box.append(preview, order, placeholder);
  field.append(label, box, remove, input);
  slot.elements = { field, label, box, preview, order, placeholder, remove, input };

  box.addEventListener("click", (event) => {
    event.stopPropagation();
    if (slot.file) input.click();
    else referenceVideoFilesInput.click();
  });
  bindVideoUploadDrop(box, (file) => setReferenceVideoSlotFile(slot, file));
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    removeReferenceVideoSlot(slot);
  });
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (file) setReferenceVideoSlotFile(slot, file);
  });

  return field;
}

function updateReferenceVideoSlots() {
  referenceVideoSlots.forEach((slot, index) => {
    const elements = slot.elements;
    if (!elements) return;
    elements.label.textContent = index === 0 ? "参考视频" : `参考视频 ${index + 1}`;
    elements.order.textContent = String(index + 1);
    elements.box.classList.toggle("has-image", Boolean(slot.previewUrl));
    elements.box.setAttribute("aria-label", `选择参考视频 ${index + 1}`);
    elements.remove.title = `移除参考视频 ${index + 1}`;
    elements.preview.hidden = !slot.previewUrl;
    if (slot.previewUrl && elements.preview.src !== slot.previewUrl) {
      elements.preview.src = slot.previewUrl;
      elements.preview.load();
    }
    if (!slot.previewUrl) elements.preview.removeAttribute("src");
    elements.placeholder.textContent = slot.file ? "点击或拖入替换" : "点击或拖入视频";
  });
  addReferenceVideoButton.disabled = referenceVideoSlots.length >= 3;
  const selectedCount = referenceVideoSlots.filter((slot) => slot.file).length;
  const selectedText = selectedCount ? `已选择 ${selectedCount} 个参考视频。` : "";
  updateNumberedHint(
    referenceVideoHint,
    "2",
    `${selectedText}支持 MP4、MOV，2–15 秒、单个不超过 50MB；非 24 FPS 会自动转换。提示词中使用 <Video 1> 引用，视频内音轨会自动配对为 <Audio N>。`,
  );
}

function updateNumberedHint(element, number, text) {
  const badge = document.createElement("span");
  badge.className = "hint-number";
  badge.textContent = number;
  const content = document.createElement("span");
  content.className = "hint-text";
  content.textContent = text;
  element.replaceChildren(badge, content);
}

function addReferenceVideoFiles(files) {
  const incoming = Array.from(files || []);
  const supported = incoming.filter(isSupportedVideoFile);
  if (!supported.length) {
    showUploadMessage("请选择 mp4 或 mov 视频");
    return;
  }

  const targets = referenceVideoSlots.filter((slot) => !slot.file);
  const selectedCount = referenceVideoSlots.filter((slot) => slot.file).length;
  const accepted = supported.slice(0, Math.min(supported.length, 3 - selectedCount));
  accepted.forEach(() => {
    if (targets.length >= accepted.length) return;
    const slot = addReferenceVideoSlot();
    if (slot) targets.push(slot);
  });
  accepted.forEach((file, index) => setReferenceVideoSlotFile(targets[index], file));

  const skipped = incoming.length - accepted.length;
  if (skipped > 0) {
    showUploadMessage(`已添加 ${accepted.length} 个参考视频，另有 ${skipped} 个因格式不支持或超过 3 个上限未添加。`);
  }
}

function bindReferenceHoverPreview() {
  new Set([referenceGrid, referenceVideoGrid]).forEach((grid) => {
    grid.addEventListener("pointerover", (event) => {
      const box = event.target.closest(".upload-box");
      if (!box || box.contains(event.relatedTarget)) return;
      const media = box.querySelector("img:not([hidden]), video:not([hidden])");
      if (!media?.src) return;
      clearTimeout(referenceHoverTimer);
      referenceHoverTimer = setTimeout(() => showReferenceHoverPreview(box, media), 500);
    });
    grid.addEventListener("pointerout", (event) => {
      const box = event.target.closest(".upload-box");
      if (!box || box.contains(event.relatedTarget)) return;
      hideReferenceHoverPreview();
    });
    grid.addEventListener("scroll", hideReferenceHoverPreview);
  });
}

function showReferenceHoverPreview(box, media) {
  referenceHoverPreview.replaceChildren();
  const preview = document.createElement(media.tagName === "VIDEO" ? "video" : "img");
  preview.src = media.src;
  if (preview.tagName === "VIDEO") {
    preview.muted = true;
    preview.loop = true;
    preview.playsInline = true;
    preview.autoplay = true;
  } else {
    preview.alt = "参考图大图预览";
  }
  referenceHoverPreview.append(preview);
  referenceHoverPreview.hidden = false;

  const boxRect = box.getBoundingClientRect();
  const previewRect = referenceHoverPreview.getBoundingClientRect();
  const gap = 10;
  let left = boxRect.right + gap;
  if (left + previewRect.width > window.innerWidth - gap) {
    left = boxRect.left - previewRect.width - gap;
  }
  left = Math.max(gap, Math.min(left, window.innerWidth - previewRect.width - gap));
  let top = boxRect.top + boxRect.height / 2 - previewRect.height / 2;
  top = Math.max(gap, Math.min(top, window.innerHeight - previewRect.height - gap));
  referenceHoverPreview.style.left = `${left}px`;
  referenceHoverPreview.style.top = `${top}px`;
  preview.play?.().catch(() => {});
}

function hideReferenceHoverPreview() {
  clearTimeout(referenceHoverTimer);
  referenceHoverTimer = null;
  referenceHoverPreview.querySelector("video")?.pause();
  referenceHoverPreview.hidden = true;
  referenceHoverPreview.replaceChildren();
}

function setReferenceVideoSlotFile(slot, file) {
  if (!isSupportedVideoFile(file)) {
    showUploadMessage("参考视频只支持 mp4、mov");
    return;
  }
  if (file.size > 50 * 1024 * 1024) {
    showUploadMessage("参考视频不能超过 50MB");
    return;
  }
  if (slot.previewUrl) URL.revokeObjectURL(slot.previewUrl);
  slot.file = file;
  slot.previewUrl = URL.createObjectURL(file);
  updateReferenceVideoSlots();
}

function bindVideoUploadDrop(box, onVideoDrop) {
  box.addEventListener("dragenter", (event) => {
    event.preventDefault();
    box.classList.add("drag-over");
  });
  box.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    box.classList.add("drag-over");
  });
  box.addEventListener("dragleave", (event) => {
    if (!box.contains(event.relatedTarget)) box.classList.remove("drag-over");
  });
  box.addEventListener("drop", (event) => {
    event.preventDefault();
    box.classList.remove("drag-over");
    const file = Array.from(event.dataTransfer?.files || []).find(isSupportedVideoFile);
    if (!file) {
      showUploadMessage("请拖入 mp4 或 mov 视频");
      return;
    }
    onVideoDrop(file);
  });
}

function isSupportedVideoFile(file) {
  if (!file) return false;
  if (["video/mp4", "video/quicktime"].includes(file.type)) return true;
  return /\.(mp4|mov)$/i.test(file.name || "");
}

async function loadWorks(options = {}) {
  refreshTaskQueue();
  if (worksRequestInFlight && !options.replace) return;
  if (options.replace) worksBrowser.controller?.abort();
  const controller = new AbortController();
  worksBrowser.controller = controller;
  const requestKey = worksBrowser.params(false).toString();
  worksRequestInFlight = true;
  try {
    const worksRes = await fetch(`/api/works/page?${worksBrowser.params(options.force)}`, {signal:controller.signal});
    const worksData = await worksRes.json();
    if (controller.signal.aborted || requestKey !== worksBrowser.params(false).toString()) return;
    if (!worksRes.ok) throw new Error(worksData.detail || "作品读取失败");
    worksRequestInFlight = false;
    if (!worksBrowser.update(worksData)) return;
    const items = worksData.items || [];
    updateWorksSummary(latestTaskItems.filter(item => ['queued','running'].includes(item.status)).length, worksData.total);
    document.querySelector('#worksStatus').textContent = '';
    const nextSignature = createWorksRenderSignature(items, []);
    if (!options.force && nextSignature === worksRenderSignature) {
      return;
    }
    worksRenderSignature = nextSignature;
    renderWorks(items);
  } catch (err) {
    if (err.name !== 'AbortError' && worksBrowser.controller === controller) {
      document.querySelector('#worksStatus').textContent = `作品读取失败：${err.message}，可点击刷新重试。`;
    }
  } finally {
    if (worksBrowser.controller === controller) worksRequestInFlight = false;
  }
}

async function refreshTaskQueue() {
  renderTasks([...submissionQueue.rows(), ...latestServerTasks]);
  if (taskRequestInFlight) return;
  taskRequestInFlight = true;
  try {
    const response = await fetch('/api/jobs');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '任务读取失败');
    latestServerTasks = (data.items || []).map(item => ({...item, sampledAt:Date.now()}));
    renderTasks([...submissionQueue.rows(), ...latestServerTasks]);
    taskList.title = '';
  } catch (error) {
    taskList.title = `任务刷新失败：${error.message}，稍后自动重试`;
    if (!latestTaskItems.length) taskList.innerHTML = `<p class="work-meta">${escapeHtml(taskList.title)}</p>`;
  } finally { taskRequestInFlight = false; }
}

function updateWorksSummary(activeCount, completedCount) {
  const active = Math.max(0, Number.parseInt(activeCount, 10) || 0);
  const completed = Math.max(0, Number.parseInt(completedCount, 10) || 0);
  worksSummary.textContent = `${active} 生成中 / ${completed} 已完成`;
}

function createWorksRenderSignature(items, activeItems) {
  return JSON.stringify({
    items: items.map((item) => ({
      id: item.id,
      media_type: item.media_type,
      output: item.output,
      prompt: item.prompt,
      resolution: item.resolution,
      steps: item.steps,
      mode: item.mode,
      referenceImageCount: (item.reference_images || []).length,
      referenceVideoCount: (item.reference_videos || []).length,
      created_at: item.created_at,
      elapsed_seconds: item.elapsed_seconds,
    })),
    activeItems: activeItems.map((item) => ({
      id: item.id,
      status: item.status,
      prompt: item.prompt,
      resolution: item.resolution,
      steps: item.steps,
      mode: item.mode,
      referenceImageCount: (item.reference_images || []).length,
      referenceVideoCount: (item.reference_videos || []).length,
      created_at: item.created_at,
      queue_position: item.queue_position,
      jobs_ahead: item.jobs_ahead,
      warning: item.warning?.message || "",
    })),
  });
}

function findHistoryWork(id) {
  return historyWorks.find((item) => item.id === id) || null;
}

function startWorksPolling() {
  loadWorks({ force: true });
  setInterval(() => { if (!document.hidden) loadWorks(); }, 3000);
}

async function refreshResources() {
  if (document.hidden || !document.querySelector('.resource-panel').open || document.querySelector('.shell').classList.contains('tasks-collapsed')) return;
  if (resourceRequestInFlight) return;
  resourceRequestInFlight = true;
  try {
    const res = await fetch("/api/resources");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "资源读取失败");
    renderResources(data);
  } catch (err) {
    if (resourceUpdated) resourceUpdated.textContent = `读取失败：${err.message}`;
  } finally {
    resourceRequestInFlight = false;
  }
}

function renderResources(data) {
  cpuUsage.textContent = `${formatPercent(data.cpu?.percent)}`;
  memoryUsage.textContent = `${formatPercent(data.memory?.percent)} · ${formatGb(data.memory?.used_gb)}/${formatGb(data.memory?.total_gb)}`;
  diskUsage.textContent = `${formatPercent(data.disk?.percent)} · ${formatGb(data.disk?.used_gb)}/${formatGb(data.disk?.total_gb)}`;
  const gpu = data.gpu?.[0];
  const reserveGb = data.policy?.vram_reserve_gb;
  gpuUsage.textContent = gpu
    ? `${formatPercent(gpu.percent)} · 显存 ${formatPercent(gpu.memory_percent)} · 预留 ${formatGb(reserveGb)}`
    : "未检测到";
  resourceUpdated.textContent = `更新于 ${new Date().toLocaleTimeString()}`;
}

function startResourcePolling() {
  if (resourceTimer) clearInterval(resourceTimer);
  refreshResources();
  resourceTimer = setInterval(refreshResources, RESOURCE_REFRESH_MS);
}

function formatPercent(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : "--";
}

function formatGb(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}GB` : "--";
}

function referenceModeLabel(item) {
  const imageCount = (item.reference_images || []).length;
  const videoCount = (item.reference_videos || []).length;
  const parts = [];
  if (imageCount) parts.push(`${imageCount} 张图`);
  if (videoCount) parts.push(`${videoCount} 个视频`);
  return parts.length ? `多参考素材 · ${parts.join(" · ")}` : "多参考素材";
}

function renderTasks(items) {
  latestTaskItems = items;
  const signature = createWorksRenderSignature([], items);
  if (signature !== taskRenderSignature) {
    taskRenderSignature = signature;
    taskList.innerHTML = items.length ? items.map(renderActiveJobCard).join('') : '<p class="work-meta">暂无生成任务，开始创作后可在这里查看进度。</p>';
  }
  document.querySelector('#taskCount').textContent = String(items.filter(item => ['queued','running'].includes(item.status)).length);
  updateWorksSummary(items.filter(item => ['queued','running'].includes(item.status)).length, worksBrowser.total);
  updateActiveJobCards(items);
}

function renderWorks(items) {
  workThumbnailObserver?.disconnect();
  historyWorks = items;
  if (!items.length) {
    works.innerHTML = `<div class="work-meta">${worksBrowser.query ? '没有匹配的作品，请修改搜索条件。' : '还没有这类作品。'}</div>`;
    return;
  }
  const historyMarkup = items.map((item) => {
    const image = isImageWork(item);
    const when = new Date(item.created_at * 1000).toLocaleString();
    const elapsed = item.elapsed_seconds ? formatDuration(Math.round(item.elapsed_seconds)) : "未知";
    const modeLabel = image ? '图片创作' : item.mode === "references"
      ? referenceModeLabel(item)
      : "首尾帧";
    return `
      <article class="work-card${item.output === activeWorkOutput ? " work-card-selected" : ""}" data-id="${escapeHtml(item.id)}" data-output="${escapeHtml(item.output)}" tabindex="0" role="button" aria-label="${image ? '预览图片作品' : '在输出面板播放作品'}">
        <div class="work-thumb">
          <span class="work-thumb-fallback-label">视频预览</span>
          ${image ? `<img class="work-image" src="${escapeHtml(item.output)}" loading="lazy" alt="图片作品">` : `<video data-work-thumbnail data-src="${escapeHtml(item.output)}#t=0.12" muted playsinline preload="metadata"></video><span class="work-play-mark" aria-hidden="true"></span>`}
        </div>
        <div class="work-menu">
          <button class="menu-trigger" type="button" data-menu="${escapeHtml(item.id)}" data-output="${escapeHtml(item.output)}" aria-label="作品菜单">⋯</button>
        </div>
        <div class="work-body">
          <div class="work-meta">${escapeHtml(when)} · 耗时 ${escapeHtml(elapsed)}</div>
          <div class="work-meta">${escapeHtml(modeLabel)} · ${escapeHtml(item.resolution)} · ${escapeHtml(String(item.steps))} steps</div>
          <div class="work-prompt">${escapeHtml(item.user_brief || item.prompt)}</div>
        </div>
      </article>
    `;
  }).join("");
  works.innerHTML = historyMarkup;
  document.querySelectorAll("[data-menu]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleWorkMenu(button);
    });
  });
  document.querySelectorAll(".work-card[data-output]").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest(".work-menu")) return;
      openWork(card.dataset.id);
    });
    card.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== ' ') return;
      if (event.target.closest('.work-menu')) return;
      event.preventDefault();
      openWork(card.dataset.id);
    });
  });
  hydrateWorkThumbnails();
  if (document.body.dataset.mobileSection === "works") {
    loadInitialWorkThumbnails();
  }
}

function openWork(id) {
  const item = findHistoryWork(id);
  if (!item) return;
  if (isImageWork(item)) worksBrowser.openImage(item);
  else playWorkInOutput(item.output);
}

function renderAllVideoItem(item) {
  const timestamp = Number(item.created_at);
  const when = Number.isFinite(timestamp) ? new Date(timestamp * 1000).toLocaleString() : "时间未知";
  const elapsed = Number(item.elapsed_seconds) > 0
    ? ` · 耗时 ${formatDuration(Math.round(item.elapsed_seconds))}`
    : "";
  let detailText;
  let promptText;
  if (item.has_metadata) {
    const modeLabel = item.mode === "references"
      ? referenceModeLabel(item)
      : "首尾帧";
    const details = [modeLabel, item.resolution];
    if (Number.isFinite(Number(item.steps))) details.push(`${item.steps} steps`);
    detailText = details.filter(Boolean).join(" · ");
    promptText = item.user_brief || item.prompt || item.filename;
  } else {
    detailText = `历史视频文件 · ${item.filename}`;
    promptText = "该视频早于当前作品索引，仅保留视频文件与生成时间。";
  }
  return `
    <article class="all-video-card" data-all-video-id="${escapeHtml(item.id)}" data-all-video-filename="${escapeHtml(item.filename)}">
      <video data-all-video data-src="${escapeHtml(item.output)}" controls playsinline preload="none" aria-label="${escapeHtml(when)}生成的视频"></video>
      <div class="all-video-info">
        <div class="work-meta">${escapeHtml(when)}${escapeHtml(elapsed)}</div>
        <div class="work-meta">${escapeHtml(detailText)}</div>
        <div class="all-video-prompt">${escapeHtml(promptText)}</div>
      </div>
    </article>
  `;
}

function updateAllVideosHeader() {
  allVideosCount.textContent = allVideosLoading && !allVideosItems.length
    ? "正在读取视频..."
    : `已显示 ${allVideosItems.length} / 共 ${allVideosTotal} 个视频`;
  videoOrderButtons.forEach((button) => {
    const isActive = button.dataset.videoOrder === allVideosOrder;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function updateAllVideosLoadState() {
  allVideosContent.querySelector("[data-all-videos-load-state]")?.remove();
  let message = "";
  let stateClass = "";
  if (allVideosLoadError) {
    message = `视频加载失败：${allVideosLoadError}`;
    stateClass = " error";
  } else if (allVideosLoading) {
    message = "正在加载 100 个视频...";
  } else if (!allVideosItems.length && !allVideosHasMore) {
    message = "还没有历史视频。";
  } else if (allVideosHasMore) {
    message = "继续向下滚动加载更多";
  } else {
    message = `已加载全部 ${allVideosItems.length} 个视频`;
  }
  allVideosContent.insertAdjacentHTML(
    "beforeend",
    `<div class="all-videos-load-state${stateClass}" data-all-videos-load-state>${escapeHtml(message)}</div>`,
  );
  updateAllVideosHeader();
}

function hydrateAllVideos(videos) {
  const loadVideo = (videoElement) => {
    if (videoElement.src || !videoElement.dataset.src) return;
    bindVideoAspectRatio(videoElement);
    videoElement.preload = "metadata";
    videoElement.src = videoElement.dataset.src;
  };

  if (!("IntersectionObserver" in window)) {
    videos.forEach(loadVideo);
    return;
  }

  if (!allVideosObserver) {
    allVideosObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        allVideosObserver.unobserve(entry.target);
        loadVideo(entry.target);
      });
    }, { root: allVideosContent, rootMargin: "320px 0px" });
  }
  videos.forEach((videoElement) => allVideosObserver.observe(videoElement));
}

function bindVideoAspectRatio(videoElement) {
  if (videoElement.dataset.aspectRatioBound) return;
  videoElement.dataset.aspectRatioBound = "1";
  videoElement.addEventListener("loadedmetadata", () => syncVideoAspectRatio(videoElement));
  if (videoElement.readyState >= 1) syncVideoAspectRatio(videoElement);
}

function syncVideoAspectRatio(videoElement) {
  const width = Number(videoElement.videoWidth);
  const height = Number(videoElement.videoHeight);
  if (!(width > 0 && height > 0)) return;
  videoElement.style.setProperty("--video-aspect-ratio", `${width} / ${height}`);
  if (videoElement === video) {
    outputVideoWidth = width;
    outputVideoHeight = height;
    fitOutputVideo();
  }
}

function fitOutputVideo() {
  if (!(outputVideoWidth > 0 && outputVideoHeight > 0 && stage?.clientWidth && stage.clientHeight)) return;
  const videoRatio = outputVideoWidth / outputVideoHeight;
  const stageRatio = stage.clientWidth / stage.clientHeight;
  video.classList.toggle("video-fit-height", videoRatio < stageRatio);
}

function appendAllVideoItems(items) {
  if (!items.length) return;
  allVideosContent.querySelector("[data-all-videos-load-state]")?.remove();
  const startIndex = allVideosContent.querySelectorAll(".all-video-card").length;
  allVideosContent.insertAdjacentHTML("beforeend", items.map(renderAllVideoItem).join(""));
  const videos = [...allVideosContent.querySelectorAll("[data-all-video]")].slice(startIndex);
  hydrateAllVideos(videos);
}

async function loadNextAllVideos() {
  if (allVideosLoading || !allVideosHasMore || allVideosModal.hidden) return;
  const token = allVideosLoadToken;
  allVideosLoading = true;
  allVideosLoadError = "";
  updateAllVideosLoadState();
  try {
    const params = new URLSearchParams({
      offset: String(allVideosNextOffset),
      limit: String(ALL_VIDEOS_PAGE_SIZE),
      order: allVideosOrder,
    });
    const res = await fetch(`/api/videos?${params}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "视频读取失败");
    if (token !== allVideosLoadToken || allVideosModal.hidden) return;

    const knownFiles = new Set(allVideosItems.map((item) => item.filename));
    const newItems = (data.items || []).filter((item) => !knownFiles.has(item.filename));
    allVideosItems.push(...newItems);
    allVideosTotal = Number(data.total) || 0;
    allVideosNextOffset = Number(data.next_offset) || allVideosItems.length;
    allVideosHasMore = Boolean(data.has_more);
    appendAllVideoItems(newItems);
  } catch (err) {
    if (token !== allVideosLoadToken || allVideosModal.hidden) return;
    allVideosLoadError = err.message;
    allVideosHasMore = false;
  } finally {
    if (token === allVideosLoadToken) {
      allVideosLoading = false;
      updateAllVideosLoadState();
    }
  }
}

function resetAllVideos() {
  allVideosLoadToken += 1;
  allVideosObserver?.disconnect();
  allVideosObserver = null;
  allVideosContent.querySelectorAll("video").forEach((videoElement) => videoElement.pause());
  allVideosItems = [];
  allVideosTotal = 0;
  allVideosNextOffset = 0;
  allVideosHasMore = true;
  allVideosLoading = false;
  allVideosLoadError = "";
  allVideosContent.innerHTML = "";
  allVideosContent.scrollTop = 0;
  updateAllVideosHeader();
  loadNextAllVideos();
}

function openAllVideos() {
  closeWorkMenus();
  allVideosModal.hidden = false;
  resetAllVideos();
  closeAllVideosButton.focus();
}

function closeAllVideos() {
  if (allVideosModal.hidden) return;
  allVideosLoadToken += 1;
  allVideosLoading = false;
  allVideosObserver?.disconnect();
  allVideosObserver = null;
  allVideosContent.querySelectorAll("video").forEach((videoElement) => {
    videoElement.pause();
    videoElement.removeAttribute("src");
    videoElement.load();
  });
  allVideosModal.hidden = true;
  showAllVideosButton.focus();
}

function setAllVideosOrder(order) {
  if (order !== "asc" && order !== "desc") return;
  if (order === allVideosOrder) return;
  allVideosOrder = order;
  resetAllVideos();
}

function loadMoreAllVideosNearBottom() {
  const remaining = allVideosContent.scrollHeight - allVideosContent.scrollTop - allVideosContent.clientHeight;
  if (remaining <= 800) loadNextAllVideos();
}

function formatActiveJobQueueText(item) {
  if (item.pendingSubmission) return item.error || item.message;
  const runText = formatSeconds(item.run_seconds);
  return item.status === "running"
    ? runText
      ? `正在生成 · 已运行 ${runText}`
      : "正在生成"
    : Number(item.jobs_ahead) > 0
      ? `等待生成中 · 前方 ${Number(item.jobs_ahead)} 个`
      : "等待生成中";
}

function taskElapsedText(item) {
  if (item.status === 'queued') return '';
  const elapsed = Number(item.elapsed_seconds ?? item.run_seconds);
  const started = Number(item.run_started_at || item.started_at);
  const seconds = Number.isFinite(elapsed) && elapsed >= 0 ? elapsed + (item.status === 'running' && item.sampledAt ? (Date.now() - item.sampledAt) / 1000 : 0) : started > 0 ? Date.now() / 1000 - started : null;
  return seconds == null ? '耗时 --' : `耗时 ${formatDuration(Math.max(0, Math.round(seconds)))}`;
}

function renderActiveJobCard(item) {
  const when = new Date(item.created_at * 1000).toLocaleString();
  const modeLabel = item.mode === "references"
    ? referenceModeLabel(item)
    : "首尾帧";
  const queueText = formatActiveJobQueueText(item);
  const warningMarkup = item.warning?.message
    ? `<div class="work-meta" data-active-warning>${escapeHtml(item.warning.message)}</div>`
    : "";
  const progressState = classifyGenerationStage(item);
  const stage = generationStage(item, progressState.index);
  const progressValue = actualGenerationProgress(item, progressState.index);
  return `
    <article class="work-card work-card-active" data-active-job-id="${escapeHtml(item.id)}" data-status="${escapeHtml(item.status)}" tabindex="0" role="button" aria-label="查看生成任务">
      <div class="work-thumb work-thumb-waiting">
        <span class="wait-spinner" aria-hidden="true"></span>
        <span class="work-wait-label">${escapeHtml(statusLabel(item.status))}</span>
      </div>
      <div class="work-body">
        <div class="work-meta" data-active-queue-text>${escapeHtml(queueText)}</div>
        <div class="work-meta" data-task-elapsed>${escapeHtml(taskElapsedText(item))}</div>
        <div class="work-stage-row"><strong data-active-stage>${escapeHtml(`步骤 ${generationStageIndex(item, progressState.index) + 1}/${GENERATION_STAGES.length} · ${stage.title}`)}</strong><span data-active-progress>${progressValue ? `${progressValue}%` : "处理中"}</span></div>
        <div class="work-stage-track"><i data-active-progress-bar style="width:${progressValue}%"></i></div>
        <div class="work-meta" data-active-detail>${escapeHtml(progressState.detail)}</div>
        ${warningMarkup}
        ${['queued','running'].includes(item.status) ? `<button type="button" ${item.pendingSubmission ? 'data-cancel-submission' : 'data-cancel-job'}="${escapeHtml(item.id)}">取消任务</button>` : ''}
        <div class="work-meta">${escapeHtml(modeLabel)} · ${escapeHtml(item.resolution)} · ${escapeHtml(String(item.steps))} steps</div>
        <div class="work-prompt">${escapeHtml(item.user_brief || item.prompt)}</div>
        <div class="work-meta">提交于 ${escapeHtml(when)}</div>
      </div>
    </article>
  `;
}

function updateActiveJobCards(activeItems) {
  activeItems.forEach((item) => {
    const card = taskList.querySelector(`[data-active-job-id="${cssEscape(item.id)}"]`);
    const queueText = card?.querySelector("[data-active-queue-text]");
    const elapsed = card?.querySelector('[data-task-elapsed]');
    if (elapsed) elapsed.textContent = taskElapsedText(item);
    if (queueText) queueText.textContent = formatActiveJobQueueText(item);
    const warning = card?.querySelector("[data-active-warning]");
    if (warning && item.warning?.message) warning.textContent = item.warning.message;
    const state = classifyGenerationStage(item);
    const progressValue = actualGenerationProgress(item, state.index);
    const stage = card?.querySelector("[data-active-stage]");
    if (stage) stage.textContent = `步骤 ${generationStageIndex(item, state.index) + 1}/${GENERATION_STAGES.length} · ${generationStage(item, state.index).title}`;
    const progressText = card?.querySelector("[data-active-progress]");
    if (progressText) progressText.textContent = progressValue ? `${progressValue}%` : "处理中";
    const progressBar = card?.querySelector("[data-active-progress-bar]");
    if (progressBar) progressBar.style.width = `${progressValue}%`;
    const detail = card?.querySelector("[data-active-detail]");
    if (detail) detail.textContent = state.detail;
  });
}

function playWorkInOutput(src) {
  if (!src) return;
  activeWorkOutput = src;
  setMobileSection("output");
  closeWorkMenus();
  hideWaitingState();
  stopTimer(false);
  if (video.getAttribute("src") !== src) {
    video.src = src;
  }
  video.hidden = false;
  video.controls = true;
  emptyState.hidden = true;
  download.href = src;
  download.download = src.split("/").pop() || "MiniMax_H3.mp4";
  download.hidden = true;
  updateActiveWorkCard();
  updateOutputWorkLabel();
  timerText.textContent = "历史作品预览";
  comfortText.textContent = "可在输出面板播放，也可点击播放器自带全屏按钮全屏。";
  video.focus();
  video.play().catch(() => {});
}

function isHistoryWorkPlaybackActive() {
  return Boolean(activeWorkOutput);
}

function updateActiveWorkCard() {
  document.querySelectorAll(".work-card[data-output]").forEach((card) => {
    card.classList.toggle("work-card-selected", card.dataset.output === activeWorkOutput);
  });
}

function updateOutputWorkLabel() {
  const videos = historyWorks.filter(item => !isImageWork(item));
  const index = videos.findIndex((item) => item.output === activeWorkOutput);
  jobText.textContent = index >= 0
    ? `正在预览历史作品 ${index + 1}/${videos.length}`
    : "正在预览历史作品";
}

function switchOutputWork(direction) {
  const videos = historyWorks.filter(item => !isImageWork(item));
  const currentIndex = videos.findIndex((item) => item.output === activeWorkOutput);
  if (currentIndex < 0) return;
  const targetIndex = currentIndex + direction;
  if (targetIndex < 0 || targetIndex >= videos.length) {
    showSwipeFeedback(targetIndex < 0 ? "已是第一条作品" : "已是最后一条作品");
    return;
  }

  video.classList.remove("work-swipe-next", "work-swipe-previous");
  void video.offsetWidth;
  video.classList.add(direction > 0 ? "work-swipe-next" : "work-swipe-previous");
  playWorkInOutput(videos[targetIndex].output);
  showSwipeFeedback(`第 ${targetIndex + 1} / ${videos.length} 个作品`);
}

function showSwipeFeedback(message) {
  if (!swipeFeedback) return;
  swipeFeedback.textContent = message;
  swipeFeedback.hidden = false;
  clearTimeout(swipeFeedbackTimer);
  swipeFeedbackTimer = window.setTimeout(() => {
    swipeFeedback.hidden = true;
  }, 1200);
}

function bindOutputSwipe() {
  if (!stage) return;
  const options = { passive: true, capture: true };
  const begin = (clientX, clientY) => {
    if (!window.matchMedia("(max-width: 760px)").matches || isOutputVideoFullscreen()) return;
    outputTouchStart = { x: clientX, y: clientY };
  };
  const finish = (clientX, clientY) => {
    if (!outputTouchStart || !window.matchMedia("(max-width: 760px)").matches || isOutputVideoFullscreen()) {
      outputTouchStart = null;
      return;
    }
    const deltaX = clientX - outputTouchStart.x;
    const deltaY = clientY - outputTouchStart.y;
    outputTouchStart = null;
    if (Math.abs(deltaX) < 56 || Math.abs(deltaX) <= Math.abs(deltaY) * 1.2) return;
    switchOutputWork(deltaX < 0 ? 1 : -1);
  };

  if (window.PointerEvent) {
    stage.addEventListener("pointerdown", (event) => {
      if (event.pointerType !== "touch") return;
      outputPointerId = event.pointerId;
      begin(event.clientX, event.clientY);
    }, options);
    stage.addEventListener("pointerup", (event) => {
      if (event.pointerId !== outputPointerId) return;
      outputPointerId = null;
      finish(event.clientX, event.clientY);
    }, options);
    stage.addEventListener("pointercancel", () => {
      outputPointerId = null;
      outputTouchStart = null;
    }, options);
    return;
  }

  stage.addEventListener("touchstart", (event) => {
    const touch = event.touches[0];
    if (touch) begin(touch.clientX, touch.clientY);
  }, options);
  stage.addEventListener("touchend", (event) => {
    const touch = event.changedTouches[0];
    if (touch) finish(touch.clientX, touch.clientY);
  }, options);
  stage.addEventListener("touchcancel", () => {
    outputTouchStart = null;
  }, options);
}

function isOutputVideoFullscreen() {
  return Boolean(document.fullscreenElement || document.webkitFullscreenElement || video.webkitDisplayingFullscreen);
}

function toggleWorkMenu(button) {
  const menuId = button.dataset.menu;
  if (!workMenuLayer || !menuId) return;
  const shouldClose = !workMenuLayer.hidden && workMenuLayer.dataset.menuId === menuId;
  closeWorkMenus();
  if (shouldClose) return;

  workMenuLayer.dataset.menuId = menuId;
  workMenuLayer.innerHTML = `
    <div class="floating-menu-panel" role="menu">
      <button type="button" data-reedit="${escapeHtml(menuId)}" role="menuitem">重新编辑</button>
      <button type="button" data-copy-prompt="${escapeHtml(menuId)}" role="menuitem">复制提示词</button>
      <a href="${escapeHtml(button.dataset.output)}" download role="menuitem">下载</a>
      <button class="danger" type="button" data-delete="${escapeHtml(menuId)}" role="menuitem">删除</button>
    </div>
  `;
  workMenuLayer.hidden = false;
  positionWorkMenu(button);

  workMenuLayer.querySelector("[data-reedit]")?.addEventListener("click", (event) => {
    event.stopPropagation();
    reeditWork(menuId);
  });
  workMenuLayer.querySelector("[data-copy-prompt]")?.addEventListener("click", (event) => {
    event.stopPropagation();
    copyWorkPrompt(menuId, event.currentTarget);
  });
  workMenuLayer.querySelector("[data-delete]")?.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteWork(menuId);
  });
}

function positionWorkMenu(button) {
  const panel = workMenuLayer?.querySelector(".floating-menu-panel");
  if (!panel) return;
  const rect = button.getBoundingClientRect();
  const panelRect = panel.getBoundingClientRect();
  const gap = 6;
  const left = Math.max(8, Math.min(window.innerWidth - panelRect.width - 8, rect.right - panelRect.width));
  let top = rect.bottom + gap;
  if (top + panelRect.height > window.innerHeight - 8) {
    top = Math.max(8, rect.top - panelRect.height - gap);
  }
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
}

function closeWorkMenus() {
  if (!workMenuLayer) return;
  workMenuLayer.hidden = true;
  workMenuLayer.removeAttribute("data-menu-id");
  workMenuLayer.innerHTML = "";
}

async function reeditWork(id) {
  const item = findHistoryWork(id);
  if (!item) { alert('没有找到这个历史作品'); closeWorkMenus(); return; }
  closeWorkMenus();
  if (isImageWork(item)) { location.href = `/image-studio/?edit_image=${encodeURIComponent(id)}`; return; }
  const revision = ++editorRevision;
  const assets = item.mode === 'references'
    ? [...(item.reference_images || []).slice(0,9).map(path => ({path, kind:'image'})),
       ...(item.reference_videos || []).slice(0,3).map(path => ({path, kind:'video'})),
       ...(item.reference_audios || []).slice(0,3).map(path => ({path, kind:'audio'}))]
    : [{path:item.first_frame, kind:'first'}, {path:item.last_frame, kind:'last'}].filter(asset => asset.path);
  const restored = await Promise.all(assets.map(async asset => {
    try { return {...asset, file:await fetchReferenceAssetFile(asset.path, 'reference.png')}; }
    catch { return asset; }
  }));
  if (revision !== editorRevision) return;
  setMobileSection('create');
  promptInput.value = item.user_brief || item.prompt || '';
  compiledPromptCache = {fingerprint:'', prompt:'', plan:{}, optimizer:null};
  compiledPromptInput.value = item.user_brief ? item.prompt || '' : '';
  if (item.resolution) resolutionInput.value = item.resolution;
  resolutionControls.sync();
  if (item.duration != null) durationInput.value = item.duration;
  if (item.steps != null) stepsInput.value = item.steps;
  seedInput.value = item.seed ?? '';
  clearKeyframeInputs(); clearReferenceSlots(); clearReferenceVideoSlots(); clearReferenceAudioSlots();
  setMode(item.mode === 'references' ? 'references' : 'keyframes');
  const images = [];
  for (const asset of restored.filter(asset => asset.file)) {
    if (asset.kind === 'image') images.push(asset.file);
    else if (asset.kind === 'video') addReferenceVideoFiles([asset.file]);
    else if (asset.kind === 'audio') addReferenceAudioFiles([asset.file]);
    else setFileInput(asset.kind === 'first' ? firstFrameInput : lastFrameInput, asset.file);
  }
  if (images.length) await addReferenceFiles(images);
  if (revision !== editorRevision) return;
  const failed = restored.filter(asset => !asset.file).length;
  showUploadMessage(`已恢复原始需求和参数，可以继续编辑。${failed ? ` ${failed} 个原参考素材已丢失，请重新添加。` : ''}`);
  promptInput.focus();
  saveVideoDraft();
}

async function fetchReferenceAssetFile(assetPath, fallbackName) {
  const res = await fetch(referenceInputUrl(assetPath));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "参考素材读取失败");
  }
  const blob = await res.blob();
  const filename = String(assetPath || "").split("/").pop() || fallbackName;
  return new File([blob], filename, { type: blob.type || referenceMimeFromName(filename) });
}

function referenceInputUrl(assetPath) {
  return `/api/reference-input/${String(assetPath || "").split("/").map(encodeURIComponent).join("/")}`;
}

function referenceMimeFromName(filename) {
  if (/\.mp4$/i.test(filename)) return "video/mp4";
  if (/\.mov$/i.test(filename)) return "video/quicktime";
  if (/\.webp$/i.test(filename)) return "image/webp";
  if (/\.jpe?g$/i.test(filename)) return "image/jpeg";
  return "image/png";
}

async function copyWorkPrompt(id, feedbackButton) {
  const item = findHistoryWork(id);
  if (!item) {
    alert("没有找到这个历史作品");
    closeWorkMenus();
    return;
  }

  try {
    await writeClipboardText(item.prompt || "");
    if (feedbackButton) feedbackButton.textContent = "已复制";
    setTimeout(closeWorkMenus, 500);
  } catch (err) {
    alert(`复制失败：${err.message}`);
  }
}

async function writeClipboardText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const ok = document.execCommand("copy");
  textarea.remove();
  if (!ok) throw new Error("浏览器拒绝访问剪贴板");
}

function hydrateWorkThumbnails() {
  const videos = document.querySelectorAll("[data-work-thumbnail]");
  if (!videos.length) return;
  if (!("IntersectionObserver" in window)) {
    videos.forEach(loadWorkThumbnail);
    return;
  }
  if (!workThumbnailObserver) {
    workThumbnailObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        workThumbnailObserver.unobserve(entry.target);
        loadWorkThumbnail(entry.target);
      });
    }, { root: works, rootMargin: "180px 0px" });
  }
  videos.forEach((videoElement) => workThumbnailObserver.observe(videoElement));
}

function loadInitialWorkThumbnails(limit = 8) {
  Array.from(document.querySelectorAll("[data-work-thumbnail]"))
    .filter((videoElement) => !videoElement.dataset.thumbnailLoading)
    .slice(0, limit)
    .forEach(loadWorkThumbnail);
}

function loadWorkThumbnail(videoElement) {
  if (videoElement.dataset.thumbnailLoading) return;
  videoElement.dataset.thumbnailLoading = "1";
  videoElement.addEventListener("loadedmetadata", () => seekWorkThumbnail(videoElement), { once: true });
  videoElement.addEventListener("loadeddata", () => markWorkThumbnailLoaded(videoElement), { once: true });
  videoElement.addEventListener("seeked", () => captureWorkThumbnail(videoElement), { once: true });
  videoElement.addEventListener("error", () => markWorkThumbnailFallback(videoElement), { once: true });
  videoElement.src = videoElement.dataset.src;
  videoElement.load();
}

function seekWorkThumbnail(videoElement) {
  const duration = Number(videoElement.duration);
  const target = Number.isFinite(duration) && duration > 0.3 ? Math.min(0.18, duration / 4) : 0;
  try {
    videoElement.currentTime = target;
  } catch {
    markWorkThumbnailLoaded(videoElement);
  }
}

function captureWorkThumbnail(videoElement) {
  if (!videoElement.videoWidth || !videoElement.videoHeight) {
    markWorkThumbnailLoaded(videoElement);
    return;
  }
  try {
    const canvas = document.createElement("canvas");
    const maxWidth = 520;
    const scale = Math.min(1, maxWidth / videoElement.videoWidth);
    canvas.width = Math.max(1, Math.round(videoElement.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(videoElement.videoHeight * scale));
    const context = canvas.getContext("2d");
    context.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
    videoElement.poster = canvas.toDataURL("image/jpeg", 0.78);
  } catch {
    // Same-origin videos can be drawn to canvas; if a browser refuses, keep the loaded video frame.
  }
  markWorkThumbnailLoaded(videoElement);
}

function markWorkThumbnailLoaded(videoElement) {
  const thumb = videoElement.closest(".work-thumb");
  thumb?.classList.add("has-thumbnail");
}

function markWorkThumbnailFallback(videoElement) {
  const thumb = videoElement.closest(".work-thumb");
  thumb?.classList.add("thumbnail-failed");
}

function setMobileSection(section) {
  document.body.dataset.view = section === 'works' ? 'works' : 'video';
  document.body.dataset.mobileSection = section;
  document.querySelectorAll('[data-view-target]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.viewTarget === document.body.dataset.view)));
  mobileDockButtons.forEach((button) => {
    const isActive = button.dataset.mobileTarget === section;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
  closeWorkMenus();
  if (section === "works") {
    requestAnimationFrame(() => {
      hydrateWorkThumbnails();
      loadInitialWorkThumbnails();
    });
  }
}

async function deleteWork(id) {
  closeWorkMenus();
  const item = findHistoryWork(id);
  if (!item || !confirm('确定删除这个作品？此操作无法撤销。')) return;
  const path = isImageWork(item) ? `/api/image-studio/images/${encodeURIComponent(id)}` : `/api/works/${encodeURIComponent(id)}`;
  const res = await fetch(path, { method: "DELETE" }).catch(error => { alert(`删除失败：${error.message}`); return null; });
  if (!res) return;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || "删除失败");
    return;
  }
  loadWorks({ force: true, replace:true });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(String(value));
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

refreshButton.addEventListener("click", refreshStatus);
generateButton.addEventListener("click", startGenerate);
refreshWorksButton.addEventListener("click", () => loadWorks({ force: true, replace:true }));
showAllVideosButton.addEventListener("click", openAllVideos);
closeAllVideosButton.addEventListener("click", closeAllVideos);
videoOrderButtons.forEach((button) => {
  button.addEventListener("click", () => setAllVideosOrder(button.dataset.videoOrder));
});
document.addEventListener("click", closeWorkMenus);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.fullscreenElement) closeAllVideos();
});
works.addEventListener("scroll", closeWorkMenus);
allVideosContent.addEventListener("scroll", loadMoreAllVideosNearBottom);
window.addEventListener("resize", closeWorkMenus);
window.addEventListener("resize", fitOutputVideo);
modeTabs.forEach((tab) => {
  tab.addEventListener("click", () => setMode(tab.dataset.mode));
});
mobileDockButtons.forEach((button) => {
  button.addEventListener("click", () => setMobileSection(button.dataset.mobileTarget));
});
addReferenceButton.addEventListener("click", () => referenceFilesInput.click());
referenceFilesInput.addEventListener("change", () => {
  addReferenceFiles(referenceFilesInput.files).finally(() => {
    referenceFilesInput.value = "";
  });
});
addReferenceVideoButton.addEventListener("click", () => referenceVideoFilesInput.click());
referenceVideoFilesInput.addEventListener("change", () => {
  addReferenceVideoFiles(referenceVideoFilesInput.files);
  referenceVideoFilesInput.value = "";
});
addReferenceAudioButton.addEventListener("click", () => referenceAudioFilesInput.click());
referenceAudioFilesInput.addEventListener("change", () => {
  addReferenceAudioFiles(referenceAudioFilesInput.files);
  referenceAudioFilesInput.value = "";
});
bindUploadBox(firstUploadBox, firstFrameInput);
bindUploadBox(lastUploadBox, lastFrameInput);
bindUploadDrop(firstUploadBox, (file) => setFileInput(firstFrameInput, file));
bindUploadDrop(lastUploadBox, (file) => setFileInput(lastFrameInput, file));
bindRemoveUpload(firstRemove, firstFrameInput, firstPreview);
bindRemoveUpload(lastRemove, lastFrameInput, lastPreview);
bindPreview(firstFrameInput, firstPreview, firstRemove);
bindPreview(lastFrameInput, lastPreview, lastRemove);
bindOutputSwipe();
bindReferenceHoverPreview();
bindVideoAspectRatio(video);
if ("ResizeObserver" in window) {
  new ResizeObserver(fitOutputVideo).observe(stage);
}

setMobileSection("create");
setMode(activeMode);
async function restoreImageStudioReference() {
  const imageId = new URLSearchParams(location.search).get('image_id');
  if (!imageId) return;
  try {
    const response = await fetch(`/api/image-studio/images/${encodeURIComponent(imageId)}/file`);
    if (!response.ok) throw new Error('来源图片不存在或已丢失');
    const file = new File([await response.blob()], `qwen-${imageId}.png`, {type:'image/png'});
    if (referenceSlots.filter(slot => slot.file).length >= 9) throw new Error('视频参考图片已满 9 张，请先移除部分参考图');
    setMode('references');
    await addReferenceFiles([file]);
    setMobileSection('create');
    showUploadMessage('已从图片创作添加视频参考图，请填写视频描述后点击生成。');
    const url = new URL(location.href); url.searchParams.delete('image_id'); history.replaceState(null, '', url);
  } catch (error) { showUploadMessage(`图片参考图带入失败：${error.message}`); }
}
loadConfig().then(async () => { await restoreImageStudioReference(); return refreshStatus(); })
  .catch(error => { statusText.textContent = `配置读取失败：${error.message}，请刷新页面重试`; });
startWorksPolling();
startResourcePolling();

taskList.addEventListener('click', event => {
  const cancel = event.target.closest('[data-cancel-submission]');
  if (cancel) { submissionQueue.cancel(cancel.dataset.cancelSubmission).catch(error => alert(error.message)); return; }
  const cancelJob = event.target.closest('[data-cancel-job]');
  if (cancelJob) {
    if (!confirm('确定取消这个生成任务？')) return;
    cancelJob.disabled = true;
    fetch(`/api/jobs/${encodeURIComponent(cancelJob.dataset.cancelJob)}`, {method:'DELETE'})
      .then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data.detail || '取消失败'); return refreshTaskQueue(); })
      .catch(error => alert(error.message)).finally(() => { cancelJob.disabled = false; });
    return;
  }
  const card = event.target.closest('[data-active-job-id]');
  if (!card) return;
  activeWorkOutput = ''; updateActiveWorkCard(); setMobileSection('output');
  followedJobId = card.dataset.activeJobId;
  const pending = submissionQueue.rows().find(item => item.id === followedJobId);
  if (pending) {
    if (['queued','running'].includes(pending.status)) showWaitingState(pending);
    else showOutputMessage(pending.error || pending.message, '任务需求已保留，可重新提交。');
  } else startJobPolling(followedJobId);
});
submissionQueue.start().catch(error => setPromptOptimizeState(`队列恢复失败：${error.message}`, 'error'));

document.querySelectorAll('[data-view-target]').forEach(button => button.addEventListener('click', () => setMobileSection(button.dataset.viewTarget === 'works' ? 'works' : 'create')));
document.querySelector('#clearInput').addEventListener('click', () => {
  editorRevision++;
  promptInput.value = ''; compiledPromptInput.value = '';
  compiledPromptCache = {fingerprint:'', prompt:'', plan:{}, optimizer:null};
  clearKeyframeInputs(); clearReferenceSlots(); clearReferenceVideoSlots(); clearReferenceAudioSlots();
  referenceFilesInput.value = ''; referenceVideoFilesInput.value = ''; referenceAudioFilesInput.value = '';
  hideReferenceHoverPreview();
  setPromptOptimizeState('输入已清空，已提交任务继续执行');
  promptInput.focus();
  saveVideoDraft();
});
document.querySelector('.controls-content').addEventListener('input', () => { editorRevision++; saveVideoDraft(); });
document.querySelector('.controls-content').addEventListener('click', event => {
  if (event.target.closest('.upload-remove')) editorRevision++;
}, true);
document.querySelector('.controls-content').addEventListener('drop', () => editorRevision++, true);
document.querySelector('#videoParameters').addEventListener('input', () => { editorRevision++; saveVideoDraft(); });
modeTabs.forEach(tab => tab.addEventListener('click', () => { editorRevision++; saveVideoDraft(); }));
const toggleTasks = document.querySelector('#toggleTasks');
toggleTasks.addEventListener('click', () => {
  const collapsed = document.querySelector('.shell').classList.toggle('tasks-collapsed');
  toggleTasks.setAttribute('aria-expanded', String(!collapsed));
  toggleTasks.setAttribute('aria-label', collapsed ? '展开生成任务' : '折叠生成任务');
  taskList.inert = collapsed;
});
const parametersMedia = matchMedia('(max-width:760px)');
function positionVideoParameters() {
  const parameters = document.querySelector('#videoParameters');
  if (parametersMedia.matches) document.querySelector('.controls-content').prepend(parameters);
  else document.querySelector('.preview-panel').append(parameters);
}
parametersMedia.addEventListener('change', positionVideoParameters);
positionVideoParameters();
document.querySelector('.task-panel').append(document.querySelector('.resource-panel'));
document.querySelector('.resource-panel').addEventListener('toggle', refreshResources);
setInterval(() => { if (!document.hidden) updateActiveJobCards(latestTaskItems); }, 1000);
taskList.addEventListener('keydown', event => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-active-job-id]')) { event.preventDefault(); event.target.click(); }
});
