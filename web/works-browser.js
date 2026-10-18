// 浏览器作品页复用工作台的混合作品接口，不依赖原生窗口或文件系统桥接。
const $ = id => document.getElementById(id);
export const isImageWork = item => item.media_type === 'image';

export class WorksBrowser {
  constructor({reload, edit}) {
    this.reload = reload;
    this.edit = edit;
    this.offset = 0;
    this.limit = 24;
    try {
      const saved = Number(localStorage.getItem('minimax-web-works-page-size'));
      if ([12, 24, 48, 100, 200].includes(saved)) this.limit = saved;
    } catch { /* 隐私模式下仍可浏览。 */ }
    this.type = 'all';
    this.order = 'desc';
    this.query = '';
    this.items = [];
    this.total = 0;
    $('worksPageSize').value = String(this.limit);
    document.querySelectorAll('[data-works-type]').forEach(button => button.onclick = () => {
      this.type = button.dataset.worksType;
      document.querySelectorAll('[data-works-type]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      this.change();
    });
    $('worksOrder').onchange = () => { this.order = $('worksOrder').value; this.change(); };
    $('worksPageSize').onchange = () => {
      this.limit = Number($('worksPageSize').value);
      try { localStorage.setItem('minimax-web-works-page-size', String(this.limit)); } catch { /* 忽略不可写存储。 */ }
      this.change();
    };
    $('worksSearch').oninput = () => {
      // 立即使旧请求失效；防抖仅延迟新请求。
      this.query = $('worksSearch').value.trim();
      this.offset = 0;
      this.controller?.abort();
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => this.reload({replace:true}), 250);
    };
    $('clearWorksSearch').onclick = () => { $('worksSearch').value = ''; this.query = ''; this.change(); $('worksSearch').focus(); };
    $('worksPrevious').onclick = () => this.page(-1);
    $('worksNext').onclick = () => this.page(1);
    $('closeWorkImage').onclick = () => $('workImageDialog').close();
    $('workImageEdit').onclick = () => { const item = this.selectedImage; $('workImageDialog').close(); this.edit(item); };
    $('workImageZoom').onclick = () => {
      const zoomed = $('workImageDialog').classList.toggle('zoomed');
      $('workImageZoom').textContent = zoomed ? '适应窗口' : '放大';
    };
    $('workImagePrevious').onclick = () => this.moveImage(-1);
    $('workImageNext').onclick = () => this.moveImage(1);
    $('workImageDialog').addEventListener('keydown', event => {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); this.moveImage(event.key === 'ArrowRight' ? 1 : -1);
      }
    });
    $('workImage').onerror = () => { $('workImagePrompt').textContent = '图片读取失败，请刷新作品列表后重试。'; };
  }

  change() {
    clearTimeout(this.searchTimer);
    this.offset = 0;
    this.reload({replace:true});
  }

  page(direction) {
    const offset = this.offset + direction * this.limit;
    if (offset < 0 || offset >= this.total) return;
    this.offset = offset;
    this.reload({replace:true});
  }

  params(refresh) {
    return new URLSearchParams({offset:this.offset, limit:this.limit, order:this.order, query:this.query, media_type:this.type, refresh:String(Boolean(refresh))});
  }

  update(data) {
    this.items = data.items || [];
    this.total = Number(data.total) || 0;
    const last = Math.max(0, Math.ceil(this.total / this.limit) - 1) * this.limit;
    if (this.offset > last) { this.offset = last; this.reload({replace:true}); return false; }
    $('worksPageInfo').textContent = `第 ${Math.floor(this.offset / this.limit) + 1} / ${Math.max(1, Math.ceil(this.total / this.limit))} 页 · 共 ${this.total} 个作品`;
    $('worksPrevious').disabled = this.offset === 0;
    $('worksNext').disabled = this.offset + this.limit >= this.total;
    return true;
  }

  openImage(item) {
    this.selectedImage = item;
    $('workImage').src = item.output;
    $('workImageTitle').textContent = `${item.resolution || `${item.width}×${item.height}`} · 图片`;
    $('workImagePrompt').textContent = item.prompt || '';
    $('workImageDownload').href = item.output;
    $('workImageDownload').download = item.filename || `${item.id}.png`;
    $('workImageDialog').classList.remove('zoomed');
    $('workImageZoom').textContent = '放大';
    const images = this.items.filter(isImageWork);
    const index = images.findIndex(image => image.id === item.id);
    $('workImagePrevious').disabled = index <= 0;
    $('workImageNext').disabled = index < 0 || index === images.length - 1;
    if (!$('workImageDialog').open) $('workImageDialog').showModal();
  }

  moveImage(direction) {
    const images = this.items.filter(isImageWork);
    const index = images.findIndex(item => item.id === this.selectedImage?.id);
    if (images[index + direction]) this.openImage(images[index + direction]);
  }
}
