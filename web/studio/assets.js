import { assetManifest, referenceEntries, remapReferences, ReferenceMenu } from "./references.js";

export function appendAssetFiles(body, mode, references, frames) {
  const append = (field, file) => body.append(field, file, file.name.replace(/\.jfif$/i, ".jpg"));
  if (mode === "references") {
    references.forEach(({ file, video, audio }) => append(audio ? "reference_audios" : video ? "reference_videos" : "reference_images", file));
  } else {
    for (const role of ["first_frame", "last_frame"]) if (frames[role]) append(role, frames[role].file);
  }
}

export class AssetPicker {
  mode = "references";
  references = [];
  frames = { first_frame: null, last_frame: null };
  queue = [];
  active = 0;
  previousEntries = [];
  dragging = "";
  constructor(root, api, onMode, onChange = () => {}) {
    this.root = root;
    this.api = api;
    this.onMode = onMode;
    this.onChange = onChange;
    this.grid = root.querySelector(".asset-grid");
    this.referenceMenu = new ReferenceMenu(root, () => this.mode === "references" ? referenceEntries(this.references) : assetManifest("keyframes", [], this.frames).map(item => ({...item, file:{name:item.name}, label:item.role === "first_frame" ? "首帧" : "尾帧"})));
    this.input = document.createElement("input");
    this.input.type = "file";
    this.input.className = "asset-file-input";
    this.input.hidden = true;
    root.append(this.input);
    this.input.addEventListener("change", () => {
      this.addFiles(this.input.files, this.pickTarget);
      this.input.value = '';
    });
    this.grid.addEventListener('dragover', event => {
      if (event.dataTransfer.types.includes('Files')) event.preventDefault();
    });
    this.grid.addEventListener('drop', event => {
      if (!event.dataTransfer.files.length) return;
      event.preventDefault();
      const role = event.target.closest('[data-frame-role]')?.dataset.frameRole || 'first_frame';
      this.addFiles(event.dataTransfer.files, {mode: this.mode, role});
    });
    root.querySelectorAll("[data-work-mode]").forEach(button => button.addEventListener("click", () => this.setMode(button.dataset.workMode)));
    this.setMode("references");
    window.addEventListener("beforeunload", () => {
      [...this.references, ...Object.values(this.frames)].forEach(item => this.dispose(item));
    });
  }
  addFiles(files, {mode = this.mode, role, replaceId} = {}) {
      let rejected = 0;
      for (const file of files) {
        const video = file.type.startsWith("video/") || /\.(mp4|mov)$/i.test(file.name);
        const audio = file.type.startsWith("audio/") || /\.(mp3|wav|m4a|aac|flac|ogg)$/i.test(file.name);
        const isImage = file.type.startsWith("image/") || /\.(png|jpe?g|jfif|webp)$/i.test(file.name);
        if ((!video && !audio && !isImage) || (mode !== 'references' && (video || audio))) { rejected++; continue; }
        const replaceIndex = replaceId ? this.references.findIndex(item => item.id === replaceId) : -1;
        if (replaceId && (replaceIndex < 0 || this.references[replaceIndex].video !== video || Boolean(this.references[replaceIndex].audio) !== audio)) { rejected++; continue; }
        if (mode === "references" && replaceIndex < 0 && this.references.filter(item => item.video === video && Boolean(item.audio) === audio).length >= (video || audio ? 3 : 9)) { rejected++; continue; }
        const item = { id: crypto.randomUUID(), file, video, audio, url: "", removed: false };
        if (replaceIndex >= 0) { item.id = replaceId; this.dispose(this.references[replaceIndex]); this.references[replaceIndex] = item; }
        else if (mode === "references") this.references.push(item);
        else { this.dispose(this.frames[role]); this.frames[role] = item; }
        this.queue.push(item);
        if (replaceId || mode !== 'references') break;
      }
      this.input.value = "";
      this.render();
      this.pump();
      if (rejected) this.root.querySelector('.asset-summary').textContent += ` · ${rejected} 个文件类型不支持或超出数量限制`;
  }
  setMode(mode) {
    if (!["references", "keyframes"].includes(mode)) return;
    this.mode = mode;
    this.referenceMenu.close();
    this.root.querySelectorAll("[data-work-mode]").forEach(button => {
      const selected = button.dataset.workMode === mode;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    this.onMode(mode);
    this.render();
    globalThis.WorkbenchMotion?.page(this.grid, mode);
  }
  choose(role, replaceId) {
    this.pickTarget = { mode: this.mode, role, replaceId };
    this.input.multiple = this.mode === "references" && !replaceId;
    this.input.accept = this.mode === "references" ? "image/png,image/jpeg,image/webp,.jfif,video/mp4,video/quicktime,.mov,audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/flac,audio/ogg,.mp3,.wav,.m4a,.aac,.flac,.ogg" : "image/png,image/jpeg,image/webp,.jfif";
    this.input.click();
  }
  dispose(item) {
    if (!item) return;
    item.removed = true;
    if (item.url) URL.revokeObjectURL(item.url);
  }
  clear() {
    [...this.references, ...Object.values(this.frames), ...this.queue].forEach(item => this.dispose(item));
    this.references = [];
    this.frames = {first_frame:null, last_frame:null};
    this.queue = [];
    this.previousEntries = [];
    this.dragging = '';
    this.input.value = '';
    this.referenceMenu.close();
    this.render();
  }
  card(item, label, role) {
    const card = document.createElement("div");
    card.className = "asset filled reference-asset";
    card.dataset.assetId = item.id;
    if (!role) {
      card.draggable = true;
      card.addEventListener("dragstart", event => {
        if (event.target.closest(".asset-remove")) return event.preventDefault();
        this.dragging = item.id;
        event.dataTransfer.setData("application/x-minimax-asset", item.id);
        event.dataTransfer.effectAllowed = "move";
        card.classList.add("is-dragging");
        this.referenceMenu.close();
      });
      card.addEventListener("dragover", event => {
        if (!this.dragging) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        card.classList.add("is-drag-target");
      });
      card.addEventListener("dragleave", () => card.classList.remove("is-drag-target"));
      card.addEventListener("drop", event => {
        if (!this.dragging) return;
        event.preventDefault();
        const from = this.references.findIndex(value => value.id === this.dragging);
        const to = this.references.indexOf(item);
        this.dragging = "";
        if (from >= 0 && to >= 0) this.references.splice(to, 0, this.references.splice(from, 1)[0]);
        this.render();
      });
      card.addEventListener("dragend", () => { this.dragging = ""; this.render(); });
    }
    if (role) card.dataset.frameRole = role;
    const preview = document.createElement("button");
    preview.type = "button";
    preview.className = "asset-preview";
    preview.title = `${label}：${item.file.name}${role ? "，点击替换" : ""}`;
    if (item.url) {
      const image = document.createElement("img");
      image.src = item.url;
      image.alt = item.file.name;
      image.decoding = "async";
      image.draggable = false;
      preview.append(image);
    } else preview.textContent = item.failed ? (item.audio ? "音频" : item.video ? "视频" : "预览不可用") : item.audio ? "音频素材" : "加载中…";
    preview.title = `${label}：${item.file.name}，点击替换`;
    preview.addEventListener("click", () => this.choose(role, role ? undefined : item.id));
    const caption = document.createElement("span");
    caption.className = "asset-caption";
    caption.textContent = `${label} · ${item.file.name}`;
    caption.title = item.file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "asset-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `移除${label}`);
    remove.addEventListener("click", () => {
      this.dispose(item);
      if (role) this.frames[role] = null;
      else this.references = this.references.filter(value => value !== item);
      this.render();
    });
    const badge = document.createElement("span");
    badge.className = "asset-number";
    badge.textContent = label;
    badge.title = role ? label : `${item.video ? "视频" : "图片"} ${label.slice(1)}，输入 @ 引用`;
    card.append(preview, caption, badge, remove);
    return card;
  }
  render() {
    if (this.dragging) return;
    const current = referenceEntries(this.references);
    const prompt = this.root.querySelector(".work-prompt");
    const updated = this.mode === 'references' ? remapReferences(prompt.value, this.previousEntries, current) : prompt.value;
    this.previousEntries = current;
    if (updated !== prompt.value) {
      const start = prompt.selectionStart, end = prompt.selectionEnd;
      prompt.value = updated;
      prompt.setSelectionRange(start, end);
      prompt.dispatchEvent(new Event("input", {bubbles:true}));
    }
    const nodes = [];
    let pictures = 0, videos = 0, audios = 0;
    const add = (label, role) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "asset add-asset";
      button.textContent = label;
      if (role) button.dataset.frameRole = role;
      button.addEventListener("click", () => this.choose(role));
      nodes.push(button);
    };
    if (this.mode === "references") {
      this.references.forEach(item => nodes.push(this.card(item, item.audio ? `A${++audios}` : item.video ? `V${++videos}` : `P${++pictures}`)));
      if (pictures < 9 || videos < 3 || audios < 3) add("＋");
    } else {
      for (const [role, label] of [["first_frame", "首帧"], ["last_frame", "尾帧"]]) {
        const item = this.frames[role];
        if (item) nodes.push(this.card(item, label, role));
        else add(`＋ ${label}`, role);
      }
    }
    this.grid.replaceChildren(...nodes);
    this.referenceMenu.update();
    this.root.querySelector(".asset-summary").textContent = this.mode === "references"
      ? `参考素材 · 图片 ${pictures}/9 · 视频 ${videos}/3 · 音频 ${audios}/3`
      : "首尾帧 · 点击添加或替换，支持仅首帧或纯文本生成";
    this.onChange();
  }
  pump() {
    while (this.active < 2 && this.queue.length) {
      const item = this.queue.shift();
      if (item.removed) continue;
      this.active++;
      this.preview(item).then(blob => {
        if (!item.removed) item.url = URL.createObjectURL(blob);
      }).catch(() => { item.failed = true; }).finally(() => { this.active--; this.render(); this.pump(); });
    }
  }
  async restoreWork(work) {
    const mode = work.mode === 'references' ? 'references' : 'keyframes';
    const paths = mode === 'references'
      ? [...(work.reference_images || []).map(path => ({path, video:false})), ...(work.reference_videos || []).map(path => ({path, video:true})), ...(work.reference_audios || []).map(path => ({path, video:false, audio:true}))]
      : ['first_frame', 'last_frame'].filter(role => work[role]).map(role => ({path:work[role], role, video:false}));
    // 顺序下载限制解码和网络占用；缺失时保留整个当前创作，避免悄悄错用素材。
    const restored = [];
    for (const entry of paths) {
      const url = `${this.api}/api/reference-input/${entry.path.split('/').map(encodeURIComponent).join('/')}`;
      const response = await fetch(url, {signal:AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error(`参考素材不可用：${entry.path} (${response.status})，当前创作已保留`);
      const blob = await response.blob();
      restored.push({...entry, file:new File([blob], entry.path.split('/').pop(), {type:blob.type})});
    }
    [...this.references, ...Object.values(this.frames)].forEach(item => this.dispose(item));
    this.references = []; this.frames = {first_frame:null, last_frame:null}; this.previousEntries = [];
    this.setMode(mode);
    for (const entry of restored) this.addFiles([entry.file], {mode, role:entry.role});
  }
  async preview(item) {
    if (item.audio) throw new Error("audio preview not required");
    if (!item.video) {
      try {
        const body = new FormData();
        body.append("image", item.file);
        const response = await fetch(`${this.api}/api/reference-thumbnail`, {method:"POST", body, signal:AbortSignal.timeout(8000)});
        if (!response.ok) throw new Error("thumbnail");
        return await response.blob();
      } catch {
        // 离线也可预览，原始文件始终保留用于上传。
        const bitmap = await createImageBitmap(item.file);
        try { return await scaledBlob(bitmap, bitmap.width, bitmap.height); }
        finally { bitmap.close(); }
      }
    }
    const url = URL.createObjectURL(item.file);
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("video timeout")), 10000);
        video.onloadeddata = () => { clearTimeout(timer); resolve(); };
        video.onerror = () => { clearTimeout(timer); reject(new Error("video decode")); };
        video.src = url;
      });
      return await scaledBlob(video, video.videoWidth, video.videoHeight);
    } finally { video.removeAttribute("src"); video.load(); URL.revokeObjectURL(url); }
  }
}

function scaledBlob(source, width, height) {
  const scale = Math.min(1, 384 / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("preview encode")), "image/jpeg", 0.85));
}
