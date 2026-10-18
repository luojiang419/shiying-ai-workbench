import {cloudErrorText} from './cloud-error-zh.js';
import {abnormalTask} from './task-cleanup.js';
const TASK_KEY='minimax-remote-node-tasks-v1', SOURCE_KEY='minimax-remote-node-sources-v1';
const read=key=>{try{const rows=JSON.parse(localStorage.getItem(key)||'[]');return Array.isArray(rows)?rows:[];}catch{return [];}};
const seconds=value=>typeof value==='number'?value:Date.parse(value||'')/1000||Date.now()/1000;
export function nodeSource(base) {
  const url=new URL(base);
  if (!['http:','https:'].includes(url.protocol) || url.username || url.password || url.pathname!=='/' || url.search || url.hash) throw new Error('算力节点地址无效');
  return url.origin;
}

// 下载与状态轮询解耦；限并发、去重、失败退避，退出后依靠未完成记录恢复。
export class ResultSaveQueue {
  active=0; waiting=[]; keys=new Set(); retry=new Map();
  constructor(limit=2){this.limit=limit;}
  add(key, run) {
    if(this.keys.has(key) || (this.retry.get(key)||0)>Date.now())return;
    this.keys.add(key);this.waiting.push({key,run});this.pump();
  }
  pump(){
    while(this.active<this.limit && this.waiting.length){
      const {key,run}=this.waiting.shift();this.active++;
      Promise.resolve().then(run).then(()=>this.retry.delete(key),()=>this.retry.set(key,Date.now()+30000))
        .finally(()=>{this.keys.delete(key);this.active--;this.pump();});
    }
  }
}

export class RemoteTaskArchive {
  tasks=new Map();sources=new Map();busy=false;
  constructor(invoke, fetchJson, changed=()=>{}, queue=new ResultSaveQueue()) {
    Object.assign(this,{invoke,fetchJson,changed,queue});this.restore({remoteTasks:read(TASK_KEY),nodeSources:read(SOURCE_KEY)});
  }
  restore(saved){
    for(const row of saved.remoteTasks||[])if(row?.archiveKey){
      const previous=this.tasks.get(row.archiveKey);
      this.tasks.set(row.archiveKey,{...previous,...row,historyHidden:Boolean(previous?.historyHidden||row.historyHidden),localPath:row.localPath||previous?.localPath});
    }
    for(const source of saved.nodeSources||[])if(source?.id)this.sources.set(source.id,source);
    this.cache();
  }
  cache(){try{localStorage.setItem(TASK_KEY,JSON.stringify(this.rows(true)));localStorage.setItem(SOURCE_KEY,JSON.stringify([...this.sources.values()]));}catch{/* 原生磁盘记录仍为主存储；浏览器配额不足不阻断任务和下载。 */}}
  async watch(base){
    base=nodeSource(base);if(this.sources.has(base))return;
    const source={id:base,since:Date.now()/1000};
    this.sources.set(base,source);this.cache();
    await this.invoke('upsert_workbench_task',{kind:'node-sources',task:source});
  }
  removeLocal(key){const row=this.tasks.get(key);if(row)this.tasks.set(key,{...row,archivedDeleted:true,localPath:'',filename:''});this.cache();this.changed();}
  rows(includeHidden=false){return [...this.tasks.values()].filter(row=>includeHidden||!row.historyHidden).sort((a,b)=>(b.created_at||0)-(a.created_at||0));}
  async clearAbnormal(base, deletedIds=[]) {
    const removed=[];
    for(const row of this.rows()) {
      if(!abnormalTask(row) && !(row.sourceBase===base && row.kind==='video' && deletedIds.includes(row.id) && !row.localPath))continue;
      const next={...row,historyHidden:true};
      await this.invoke('upsert_workbench_task',{kind:'remote-tasks',task:next});
      this.tasks.set(row.archiveKey,next);removed.push(row.archiveKey);
    }
    this.cache();this.changed();return removed;
  }
  async remember(base, kind, raw) {
    if(!raw?.id)return;
    base=nodeSource(base); const archiveKey=`${base}|${kind}|${raw.id}`;
    const previous=this.tasks.get(archiveKey)||{};
    const status=({completed:'done',succeeded:'done',failed:'failed',blocked:'failed',cancelled:'canceled'})[raw.status]||raw.status||previous.status||'queued';
    let output=raw.output||raw.result?.original_video||raw.result?.video||previous.output||'';
    if(kind==='image')output=`/api/image-studio/images/${raw.id}/file`;
    if(output.startsWith(base+'/'))output=output.slice(base.length);
    if(output && !/^\/outputs\/[^/]+$|^\/api\/image-studio\/images\/[a-f0-9]{32}\/file$/.test(output))output='';
    const row={...previous,id:raw.id,archiveKey,sourceBase:base,remoteNode:true,kind,
      status:kind==='image'?'done':status,media_type:kind==='image'?'image':'video',
      mode:raw.mode||previous.mode|| (kind==='clone'?'克隆创作':'references'),
      prompt:raw.prompt||raw.request?.prompt||previous.prompt||'',userBrief:raw.userBrief||raw.user_brief||previous.userBrief,
      resolution:raw.resolution||raw.request?.resolution||previous.resolution,
      duration:raw.duration||raw.request?.output_duration||previous.duration,
      created_at:seconds(raw.created_at||previous.created_at),output,
      externalUrl:output?base+output:'', output_available:!!output||!!previous.localPath,
      error_raw:raw.error||previous.error_raw, error:raw.error?cloudErrorText(raw.error):previous.error,
      updated_at:Date.now()/1000};
    if(JSON.stringify({...previous,updated_at:0})===JSON.stringify({...row,updated_at:0})){this.schedule(previous);return previous;}
    this.tasks.set(archiveKey,row);this.cache();
    await this.invoke('upsert_workbench_task',{kind:'remote-tasks',task:row});
    this.schedule(row);return row;
  }
  schedule(row){
    if(row.historyHidden||row.status!=='done'||!row.output||row.localPath||row.archivedDeleted)return;
    this.queue.add(row.archiveKey,async()=>{
      try {
        const result=await this.invoke('archive_node_work',{task:this.tasks.get(row.archiveKey)||row});
        const current=this.tasks.get(row.archiveKey);
        this.tasks.set(row.archiveKey,{...current,...result,historyHidden:Boolean(current?.historyHidden||result.historyHidden)});this.cache();this.changed();
      }catch(error){
        const next={...this.tasks.get(row.archiveKey),archiveError:cloudErrorText(error)};
        this.tasks.set(row.archiveKey,next);this.cache();this.changed();throw error;
      }
    });
  }
  async sync(){
    if(this.busy)return;this.busy=true;
    try {
      await Promise.all([...this.sources.values()].map(async source=>{
        const base=source.id;
        const endpoints=[['video','/api/job-history?limit=100&order=desc','items'],['video','/api/jobs','items'],['clone','/api/clone/tasks','items'],['image','/api/image-studio/images','images'],['image-job','/api/image-studio/jobs','jobs']];
        await Promise.all(endpoints.map(async([kind,path,key])=>{
          try {
            const result=await this.fetchJson(base,path);
            for(const raw of result[key]||[]){
              if(kind==='clone'&&raw.kind!=='generate')continue;
              const existing=this.tasks.has(`${base}|${kind}|${raw.id}`);
              // 旧节点也可恢复；只自动归档本机连接期间产生的记录，避免复制整台节点旧作品。
              if(!existing&&seconds(raw.created_at)<source.since-5 && ['done','completed','succeeded','failed','error','canceled','cancelled'].includes(raw.status|| (kind==='image'?'done':'')))continue;
              await this.remember(base,kind,raw);
              if(kind==='image-job')for(const image of raw.images||[])await this.remember(base,'image',image);
            }
          }catch{/* 节点离线时保留本机任务和已经保存的作品。 */}
        }));
        // 单条查询补齐超出节点最近 100 条窗口的活动任务。
        for(const row of this.rows().filter(row=>row.sourceBase===base&&!['done','failed','error','canceled'].includes(row.status))){
          try{const raw=await this.fetchJson(base,`/api/${row.kind==='clone'?'clone/tasks':row.kind==='image-job'?'image-studio/jobs':'jobs'}/${encodeURIComponent(row.id)}`);await this.remember(base,row.kind,raw);}catch{}
        }
      }));
      this.rows().forEach(row=>this.schedule(row));this.changed();
    }finally{this.busy=false;}
  }
}
