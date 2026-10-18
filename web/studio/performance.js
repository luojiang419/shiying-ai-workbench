export const pageCount = (total, size) => Math.max(1, Math.ceil(total / size));
export const lastOffset = (total, size) => (pageCount(total, size) - 1) * size;

export function worksFailure(error, service) {
  if (error.status === 404) return "本地服务需要更新，请在概览页重启服务后重试。";
  if (!error.status && service?.running === false) return "服务未启动，启动服务后即可浏览作品。";
  if (error.name === "TimeoutError") return "作品读取超时，已保留当前内容，请稍后刷新重试。";
  if (error instanceof TypeError) return "无法连接本地服务，请在概览页检查服务状态后重试。";
  return `作品读取失败：${error.message || error}，点击刷新重试`;
}

// 新请求取消旧请求；即使旧服务器仍返回，也不能覆盖当前页。
export class LatestRequest {
  generation = 0;
  controller = null;
  begin() {
    this.controller?.abort();
    this.controller = new AbortController();
    const generation = ++this.generation;
    return { signal: this.controller.signal, current: () => generation === this.generation };
  }
  cancel() { this.controller?.abort(); ++this.generation; }
}

export function createPoller(run, delay, visible = () => !document.hidden) {
  let timer;
  let running = false;
  let pending = false;
  const tick = async () => {
    clearTimeout(timer);
    if (running) { pending = true; return; }
    if (!visible()) return;
    running = true;
    try { await run(); } catch { /* 页面保留上次成功数据，各请求负责显示错误。 */ }
    finally {
      running = false;
      if (visible()) timer = setTimeout(tick, pending ? 0 : delay());
      pending = false;
    }
  };
  return { wake: tick, pause: () => { clearTimeout(timer); pending = false; } };
}

export class ThumbnailLoader {
  observed = new Set();
  queue = [];
  active = 0;
  inflight = new Map();
  constructor(limit = 4) {
    this.limit = limit;
    this.observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        this.observer.unobserve(entry.target);
        this.observed.delete(entry.target);
        this.queue.push(entry.target);
      }
      this.pump();
    }, { rootMargin: "100px" });
  }
  observe(image) { this.observed.add(image); this.observer.observe(image); }
  release(container) {
    for (const image of this.observed) {
      if (!container.contains(image)) continue;
      this.observer.unobserve(image);
      this.observed.delete(image);
    }
    this.queue = this.queue.filter((image) => !container.contains(image));
    for (const [image, cancel] of this.inflight) if (container.contains(image)) cancel();
  }
  pump() {
    while (this.active < this.limit && this.queue.length) {
      const image = this.queue.shift();
      if (!image.isConnected) continue;
      this.active++;
      let finished = false;
      const finish = () => {
        if (finished) return;
        finished = true;
        clearTimeout(timeout);
        image.onload = null;
        image.onerror = null;
        this.inflight.delete(image);
        this.active--;
        this.pump();
      };
      const fail = () => {
        const fallback = document.createElement("span");
        fallback.className = "thumbnail-fallback";
        fallback.textContent = "▶ 点击查看";
        image.replaceWith(fallback);
        image.removeAttribute("src");
        finish();
      };
      const timeout = setTimeout(fail, 15000);
      this.inflight.set(image, () => { image.onload = null; image.onerror = null; image.removeAttribute('src'); finish(); });
      image.onload = () => { image.classList.add("is-loaded"); finish(); };
      image.onerror = fail;
      image.src = image.dataset.src;
    }
  }
}
