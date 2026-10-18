import {cloudErrorText} from './cloud-error-zh.js';
// IndexedDB 保留 File/Blob 快照；准备与提交串行，生成交给各引擎已有队列。
export class SubmissionQueue {
  constructor(name, run, changed = () => {}, submitted = () => {}) {
    this.name = name;
    this.run = run;
    this.changed = changed;
    this.submitted = submitted;
    this.items = [];
    this.busy = false;
    this.ready = this.open();
  }

  async open() {
    // 一个来源的多个窗口不能同时恢复并提交同一份付费任务。
    if (!navigator.locks) throw new Error('当前浏览器不支持后台任务锁，请更新浏览器后重试');
    await new Promise((resolve, reject) => {
      navigator.locks.request(`minimax-submissions-${this.name}`, {ifAvailable:true}, async lock => {
        if (!lock) { reject(new Error('另一窗口正在处理此生成队列，请回到该窗口添加任务')); return; }
        resolve();
        await new Promise(release => { this.release = release; });
      }).catch(reject);
    });
    this.db = await new Promise((resolve, reject) => {
      const request = indexedDB.open(`minimax-submissions-${this.name}`, 1);
      request.onupgradeneeded = () => request.result.createObjectStore('tasks', {keyPath:'id'});
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    this.items = await new Promise((resolve, reject) => {
      const request = this.db.transaction('tasks').objectStore('tasks').getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    this.items.sort((a, b) => a.order - b.order);
    for (const item of this.items) {
      if (item.phase === 'submitting' && item.status === 'running') {
        Object.assign(item, {status:'error', message:'上次提交结果未确认，请先检查生成记录再重新提交', error:'上次提交结果未确认，请先检查生成记录再重新提交'});
      } else if (item.status === 'running') {
        Object.assign(item, {status:'queued', phase:'queued', message:'等待准备提示词'});
      }
      await this.write(item);
    }
  }

  write(item, remove = false) {
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction('tasks', 'readwrite');
      const store = transaction.objectStore('tasks');
      if (remove) store.delete(item.id); else store.put(item);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error('任务快照保存失败'));
    });
  }

  rows() { return this.items.map(({data, order, ...job}) => ({...job, pendingSubmission:true,...(job.cloud ? {provider:data.engine==='platform_h3'?'platform':'personal',ownerId:data.ownerId,personalScope:data.personalScope,localReceipt:true}: {})})); }
  snapshot(id) { return structuredClone(this.items.find(item => item.id === id)?.data); }

  async add(data, metadata = {}) {
    const snapshot = structuredClone(data);
    const details = structuredClone(metadata);
    await this.ready;
    const item = { ...details, id:`prepare-${crypto.randomUUID()}`, order:Math.max(Date.now(), (this.items.at(-1)?.order || 0) + 1),
      created_at:Date.now()/1000, status:'queued', phase:'queued', message:'等待准备提示词', data:snapshot};
    await this.write(item);
    this.items.push(item);
    this.changed();
    this.drain();
    return item;
  }

  async cancel(id) {
    await this.ready;
    const item = this.items.find(row => row.id === id);
    if (!item || !['queued','running'].includes(item.status)) return;
    if (item.phase === 'submitting') throw new Error('正在确认生成提交结果，请稍后在生成队列中取消');
    Object.assign(item, {status:'canceled', message:'已取消', completed_at:Date.now()/1000});
    await this.write(item);
    this.changed();
  }

  async reconcile(receipts) {
    await this.ready;
    for(const item of [...this.items]){
      const job=receipts.find(row=>row.requestId===item.id);
      if(item.phase==='submitting' && job){await this.submitted(item,job);await this.write(item,true);this.items=this.items.filter(row=>row!==item);}
    }
  }

  async start() { await this.ready; this.changed(); this.drain(); }

  async drain() {
    if (this.busy) return;
    this.busy = true;
    try {
      let item;
      while ((item = this.items.find(row => row.status === 'queued' && (!this.canRun || this.canRun(row.data))))) {
        const check = () => { if (item.status === 'canceled') throw new Error('任务已取消'); };
        try {
          Object.assign(item, {status:'running', phase:'prompt', message:'正在准备提示词', started_at:Date.now()/1000});
          await this.write(item);
          check(); this.changed();
          const result = await this.run(item.data, {
            check,
            requestId:item.id,
            submitting:async () => {
              check();
              Object.assign(item, {phase:'submitting', message:'正在上传素材并提交生成'});
              await this.write(item);
              this.changed();
            },
          });
          await this.submitted(item, result);
          await this.write(item, true);
          this.items = this.items.filter(row => row !== item);

        } catch (error) {
          if (item.status !== 'canceled') Object.assign(item, {status:'error', error_raw:String(error.message || error), error:cloudErrorText(error), message:cloudErrorText(error)});
          // 不让单个任务的错误或取消阻塞后续任务。
          await this.write(item);
        }
        this.changed();
      }
    } finally { this.busy = false; }
  }
}
