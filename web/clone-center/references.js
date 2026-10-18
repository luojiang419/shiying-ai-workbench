export function referenceEntries(items) {
  let pictures = 0, videos = 0, audios = 0;
  return items.map(item => {
    const type = item.audio ? "Audio" : item.video ? "Video" : "Picture";
    const number = type === "Audio" ? ++audios : type === "Video" ? ++videos : ++pictures;
    return { ...item, number, label: `${type === "Audio" ? "A" : type === "Video" ? "V" : "P"}${number}`, tag: `<${type} ${number}>` };
  });
}

export function assetManifest(mode, items, frames = {}) {
  if (mode === "references") return referenceEntries(items).map(item => ({
    id:item.id, kind:item.audio ? "Audio" : item.video ? "Video" : "Picture",
    number:item.number, tag:item.tag, name:item.file.name,
  }));
  const entries = [];
  for (const role of ["first_frame", "last_frame"]) {
    const item = frames[role];
    if (item) entries.push({id:item.id, kind:"Picture", number:entries.length + 1,
      tag:`<Picture ${entries.length + 1}>`, name:item.file.name, role});
  }
  return entries;
}

export function normalizeUserReferences(input, manifest) {
  const bindings = new Map();
  const byTag = new Map(manifest.map(item => [item.tag, item]));
  const resolve = (kind, number) => {
    const tag = `<${kind} ${number}>`;
    const item = byTag.get(tag);
    if (!item) throw new Error(`${tag} 没有对应的已上传素材，请检查编号或重新选择素材。`);
    bindings.set(item.id, {assetId:item.id, tag, kind, name:item.name});
    return tag;
  };
  let text = String(input).replace(/<(Picture|Video|Audio)\s+(\d+)>/gi,
    (_, kind, number) => resolve(kind[0].toUpperCase() + kind.slice(1).toLowerCase(), Number(number)));
  text = text.replace(/(?:参考)?(图片|图|视频|音频)\s*([1-9]\d*)/g, (_, word, number) =>
    resolve(word === "图片" || word === "图" ? "Picture" : word === "视频" ? "Video" : "Audio", Number(number)));
  return {text, bindings:[...bindings.values()]};
}

export function remapReferences(text, previous, current) {
  const oldTags = new Map(previous.map(item => [item.tag, item]));
  const newIds = new Map(current.map(item => [item.id, item]));
  const remap = (tag, render, fallback = tag) => {
    const old = oldTags.get(tag);
    if (!old) return fallback;
    const next = newIds.get(old.id);
    if (!next) return `[已移除素材：${old.file.name}]`;
    return render(next);
  };
  // 两次替换都只按旧清单查 ID；交换编号时不能把已经更新的标签再次替换。
  const markers = [];
  const staged = text.replace(/<(Picture|Video|Audio) (\d+)>|(?:参考)?(图片|图|视频|音频)\s*([1-9]\d*)/g,
    (matched, kind, number, word, aliasNumber) => {
      const type = kind || (word === '图片' || word === '图' ? 'Picture' : word === '视频' ? 'Video' : 'Audio');
      const tag = `<${type} ${kind ? number : aliasNumber}>`;
      const replacement = remap(tag, next => kind ? next.tag : matched.replace(/\d+$/, String(next.number)), matched);
      const marker = `\u0000REF${markers.length}\u0000`;
      markers.push(replacement);
      return marker;
    });
  return staged.replace(/\u0000REF(\d+)\u0000/g, (_, index) => markers[Number(index)]);
}

export function validateReferences(text, mode, items, frames = {}) {
  if (text.includes("[已移除素材：")) throw new Error("提示词包含已移除素材，请删除失效引用或重新选择素材。");
  const tags = [...text.matchAll(/<(Picture|Video|Audio) (\d+)>/g)];
  const entries = assetManifest(mode, items, frames);
  for (const [, kind, number] of tags) {
    const tag = `<${kind} ${number}>`;
    if (!entries.some(item => item.tag === tag)) throw new Error(`${mode === "references" ? "引用" : "首尾帧引用"} ${tag} 没有对应素材，请重新选择。`);
  }
}

export class ReferenceMenu {
  selected = 0;
  composing = false;
  constructor(root, entries) {
    this.input = root.querySelector(".work-prompt");
    this.menu = root.querySelector(".mention-menu");
    this.entries = entries;
    this.menu.id = "asset-reference-menu";
    this.input.setAttribute("aria-controls", this.menu.id);
    this.input.setAttribute("aria-autocomplete", "list");
    this.input.addEventListener("input", () => this.update());
    this.input.addEventListener("click", () => this.update());
    this.input.addEventListener("compositionstart", () => { this.composing = true; this.close(); });
    this.input.addEventListener("compositionend", () => { this.composing = false; this.update(); });
    this.input.addEventListener("keydown", event => {
      if (event.isComposing || this.composing || !this.menu.classList.contains("open")) return;
      if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        if (this.matches.length) this.selected = (this.selected + (event.key === "ArrowDown" ? 1 : -1) + this.matches.length) % this.matches.length;
        this.highlight();
      } else if (event.key === "Enter" && this.matches.length) { event.preventDefault(); this.use(this.matches[this.selected]); }
      else if (event.key === "Escape") { event.preventDefault(); this.close(); }
      else if (["ArrowLeft", "ArrowRight", "Home", "End", "Tab"].includes(event.key)) this.close();
    });
    this.input.addEventListener("blur", () => this.close());
  }
  close() {
    this.query = null;
    this.menu.classList.remove("open");
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
  }
  update() {
    if (this.composing || document.activeElement !== this.input) return this.close();
    const end = this.input.selectionStart;
    const match = this.input.value.slice(0, end).match(/@([^\s@<>]*)$/);
    if (!match || this.input.selectionEnd !== end) return this.close();
    this.start = end - match[0].length;
    this.end = end;
    const query = match[1].toLocaleLowerCase();
    const selectedId = this.query === query ? this.matches?.[this.selected]?.id : null;
    this.query = query;
    this.matches = this.entries().filter(item => `${item.label} ${item.number || ""} ${item.file.name}`.toLocaleLowerCase().includes(query));
    this.selected = Math.max(0, this.matches.findIndex(item => item.id === selectedId));
    const nodes = this.matches.map((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.id = `asset-option-${index}`;
      button.setAttribute("role", "option");
      if (item.url) {
        const img = document.createElement("img");
        img.src = item.url;
        img.alt = "";
        button.append(img);
      } else {
        const placeholder = document.createElement("span");
        placeholder.className = "mention-placeholder";
        placeholder.textContent = item.video ? "视频" : "图片";
        button.append(placeholder);
      }
      const text = document.createElement("span");
      text.className = "mention-copy";
      text.textContent = `${item.label} · ${item.file.name}`;
      const tag = document.createElement("small");
      tag.textContent = item.tag;
      text.append(tag);
      button.append(text);
      button.addEventListener("mousedown", event => event.preventDefault());
      button.addEventListener("click", () => this.use(item));
      return button;
    });
    if (!nodes.length) {
      const empty = document.createElement("small");
      empty.textContent = "没有匹配素材，请先添加素材或调整关键词";
      nodes.push(empty);
    }
    this.menu.replaceChildren(...nodes);
    this.menu.classList.add("open");
    this.input.setAttribute("aria-expanded", "true");
    this.highlight();
  }
  highlight() {
    this.menu.querySelectorAll("button").forEach((button, index) => {
      const active = index === this.selected;
      button.setAttribute("aria-selected", String(active));
      if (active) { this.input.setAttribute("aria-activedescendant", button.id); button.scrollIntoView({block:"nearest"}); }
    });
  }
  use(item) {
    this.input.setRangeText(`${item.tag} `, this.start, this.end, "end");
    this.input.dispatchEvent(new Event("input", {bubbles:true}));
    this.close();
    this.input.focus();
  }
}
