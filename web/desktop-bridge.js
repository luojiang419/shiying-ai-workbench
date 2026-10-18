// 正式浏览器适配层。页面与业务来自 desktop-workbench/src，不模拟生成结果。
const origin = location.origin;
const root = document.querySelector('#minimax-plan');
const query = selector => root.querySelector(selector);

async function request(path, options = {}) {
  const response = await fetch(origin + path, {...options, headers:{'Content-Type':'application/json',...options.headers}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
  return data;
}
const command = (name,args={}) => request(`${name === 'optimize_server_prompt' ? '/api/workbench' : boot.managementPath || '/api/workbench'}/command/${encodeURIComponent(name)}`,{method:'POST',body:JSON.stringify(args)});
const listeners = new Map();
let boot;
const preferences = {reduce_motion:false,f11_fullscreen_enabled:true};
// 局域网 HTTP 页面也能创建任务 ID、复制提示词；不要求浏览器安全上下文。
if (!crypto.randomUUID) crypto.randomUUID = () => '10000000-1000-4000-8000-100000000000'.replace(/[018]/g,
  value => (Number(value) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(value)/4).toString(16));
if (!navigator.clipboard) Object.defineProperty(navigator,'clipboard',{value:{writeText:async text => {
  const input=document.createElement('textarea');input.value=text;input.style.cssText='position:fixed;left:-9999px';
  document.body.append(input);input.select();
  const copied=document.execCommand('copy');input.remove();
  if (!copied) throw new Error('浏览器未允许复制，请手动选择文字复制');
}}});

function openPage(name) {
  query(`.dock [data-target="${name}"]`)?.click();
}
function settings() {
  return {...boot.settings,...preferences,generation_engine:'local',download_mode:'manual',download_directory:''};
}
function fileUrl(filename) {
  if (typeof filename !== 'string' || !filename || /[/\\]/.test(filename) || filename === '.' || filename === '..') throw new Error('作品文件名无效');
  if (/^[a-f0-9]{32}\.png$/.test(filename)) return `${origin}/api/image-studio/images/${filename.slice(0,-4)}/file`;
  return `${origin}/outputs/${encodeURIComponent(filename)}`;
}
async function download(url, name, options = {}) {
  const target = new URL(url,origin);
  if (target.origin !== origin) throw new Error('仅支持下载当前服务的作品');
  const response = await fetch(target,options);
  if (!response.ok) throw new Error('作品下载失败，文件可能已丢失');
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href=objectUrl; link.download=name || '作品';
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl),60000);
  return {saved:false,browserDownload:true};
}
async function upscale(filename) {
  const task = await request('/api/upscale',{method:'POST',body:JSON.stringify({filename})});
  for (;;) {
    const result = await request(`/api/upscale/${encodeURIComponent(task.task_id)}`);
    if (result.status === 'failed') throw new Error(result.error || '超分失败');
    if (result.status === 'done') return result;
    await new Promise(resolve => setTimeout(resolve,2000));
  }
}
async function invoke(name,args={}) {
  if (name === 'service_status') return {running:true,web_url:origin,message:'已连接当前 Web 服务',managed:false};
  if (name === 'get_panel_settings') return settings();
  if (name === 'set_generation_engine') {
    if (args.engine !== 'local') throw new Error('浏览器使用当前服务的生成引擎；外部节点及云端账号请在桌面软件配置');
    return settings();
  }
  if (name === 'set_reduce_motion' || name === 'set_f11_fullscreen_enabled') {
    const key = name === 'set_reduce_motion' ? 'reduce_motion' : 'f11_fullscreen_enabled';
    preferences[key]=Boolean(args.enabled);
    localStorage.setItem('minimax-browser-preferences',JSON.stringify(preferences));
    return settings();
  }
  if (boot.canManage && (name.startsWith('set_') || ['get_upscale_settings','save_upscale_settings',
    'save_assistant_providers','fetch_assistant_models','chat_assistant_api','storage_paths'].includes(name))) {
    const result = await command(name,args);
    if (name.startsWith('set_')) {boot.settings=result;return settings();}
    if (name === 'save_assistant_providers') boot.providers=result;
    return result;
  }
  if (name === 'install_directory_status') return {path:'',source:'server'};
  if (name === 'reference_image_cache_status') return {buckets:[]};
  if (name === 'get_upscale_settings') return {
    settings:{engine:boot.upscale?.engine || 'realesrgan',topaz_model:boot.upscale?.topaz_model || 'prob-4',topaz_path:''},
    models:[],preferred_model:boot.upscale?.topaz_model || 'prob-4',
    topaz:{ready:false,error:'超分由服务主机管理'},starlight:{message:'请在服务主机配置'},
  };
  if (name === 'optimize_server_prompt') return boot.compatibility
    ? request('/api/optimize-prompt',{method:'POST',body:JSON.stringify(args.request)}) : command(name,args);
  if (name === 'aux_runtime_available') return true; // 当前服务负责依赖补齐；浏览器不安装运行时。
  if (name === 'restore_workbench_state') {
    return {cloudTasks:[],upscales:JSON.parse(localStorage.getItem('minimax-browser-upscales') || '[]')};
  }
  if (name === 'persist_workbench_state') {
    if (args.kind === 'upscales') localStorage.setItem('minimax-browser-upscales',JSON.stringify(args.data));
    return;
  }
  if (name === 'list_local_works') return request('/api/works/page?'+new URLSearchParams({offset:args.offset||0,limit:args.limit||24,
    order:args.order||'desc',query:args.query||'',media_type:args.mediaType||'all'}));
  if (name === 'download_work') return download(fileUrl(args.sourceFilename),args.suggestedFilename||args.sourceFilename);
  if (name === 'download_node_work') return download(args.output,args.suggestedFilename);
  if (name === 'open_work_video_path') { window.open(fileUrl(args.sourceFilename),'_blank','noopener'); return; }
  if (name === 'open_workbench') { openPage('workbench'); return; }
  if (name === 'open_generated_directory' || name === 'open_data_directory') { openPage('works'); return; }
  if (name === 'open_service_log') { window.open((boot.managementPath || '/api/workbench')+'/log','_blank','noopener'); return; }
  if (name === 'open_canvas') { window.open('/docs','_blank','noopener'); return; }
  if (name === 'copyable_addresses') return [origin];
  if (name === 'free_backend_cache') return request('/api/comfy/free',{method:'POST',body:'{}'});
  if (name === 'upscale_video') return upscale(args.sourceFilename);
  if (name === 'export_clone_comparison') return download(`/api/workbench/clone/${encodeURIComponent(args.taskId)}/comparison`,
    `clone-comparison-${args.taskId}.mp4`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({variant:args.variant||'original'})});
  if (name === 'cloud_h3_configured') return false;
  if (name === 'platform_load_login' || name === 'platform_load_refresh_token') return null;
  if (name === 'get_update_settings') return {policy:'manual',networkMode:'direct',manualProxyUrl:''};
  throw new Error('此操作需要在服务主机上的桌面软件中执行');
}

const fullscreenWindow = {
  isFullscreen:async () => Boolean(document.fullscreenElement),
  setFullscreen:async value => value ? document.documentElement.requestFullscreen() : document.exitFullscreen(),
};
window.__TAURI__ = {
  core:{invoke,convertFileSrc:path=>new URL(path,origin).href},
  event:{listen:async (name,callback) => { listeners.set(name,callback); return () => listeners.delete(name); }},
  window:{getCurrentWindow:() => fullscreenWindow},
};

function browserControls() {
  // 浏览器使用同一个后端与桌面界面，隐藏系统窗口/进程/安装器专有操作。
  for (const selector of ['.stop-service','.restart-service']) {
    root.querySelectorAll(selector).forEach(element => {element.disabled=true;element.title='服务进程由服务器进程管理器托管';});
  }
  for (const selector of ['.aux-runtime-install',
    '.service-prestart-comfy','.install-directory-setting','.storage-download-mode','.storage-clear-refs',
    '.window-close-behavior']) {
    const element=query(selector);
    if (element) (element.closest('.setting') || element).dataset.browserUnavailable='true';
  }
  for (const selector of ['[data-setting-pane="engine"]','[data-setting-pane="update"]']) {
    root.querySelectorAll(selector).forEach(element=>element.dataset.browserUnavailable='true');
  }
  query('.topaz-pick').dataset.browserUnavailable='true';
  query('.settings-stop-service').dataset.browserUnavailable='true';
  query('.settings-restart-service').dataset.browserUnavailable='true';
  query('.settings-open-canvas').textContent='API 文档';
  query('[data-setting-pane="service"] .settings-section').dataset.browserUnavailable='true';
  query('[data-setting-pane="about"] .settings-section .setting').dataset.browserUnavailable='true';
  query('.about-service-state').closest('.setting').querySelector('small').textContent=`Python API · ${origin}`;
  query('[data-setting-pane="window"] .settings-pane-head h2').textContent='浏览器全屏';
  query('[data-setting-pane="window"] .settings-pane-head p').textContent='全屏与显示偏好仅保存在当前浏览器';
  query('.about-block .subtitle').textContent=`Web 工作台 v${boot.version}`;
  const engine=query('.performance-engine');
  engine.disabled=true;
  const description=query('[data-setting-pane="performance"] .settings-pane-head p');
  if (description) description.textContent='配置当前服务器的生成引擎；新任务使用保存后的设置，显存预留在后端下次启动时生效。';
  const upscaleDescription=query('[data-setting-pane="upscale"] .settings-pane-head p');
  if (upscaleDescription) upscaleDescription.textContent='应用于当前服务器的视频与克隆超分；Topaz 需要在服务器上安装并通过检测。';
  const about=query('[data-setting-pane="about"] .settings-pane-head p');
  if (about) about.textContent=`当前 Web 工作台 v${boot.version}，服务地址 ${origin}`;
  root.querySelectorAll('[data-run-mode]').forEach(button => {
    button.hidden=button.dataset.runMode !== 'local';
    if (!button.hidden) button.textContent='当前服务';
  });
  const paths = {'.quick-open-output span':'作品管理','.storage-open-output':'查看作品',
    '.storage-open-data':'查看作品','.open-web-workbench':'进入视频创作','.settings-service-address':origin};
  for (const [selector,text] of Object.entries(paths)) { const element=query(selector); if(element)element.textContent=text; }
  const canvasButton=[...root.querySelectorAll('.service-actions button')].find(button=>button.textContent.includes('画布'));
  if (canvasButton) canvasButton.textContent='API 文档';
  query('.brand-version').textContent=`v${boot.version} · Web`;
  query('.aux-runtime-state').textContent='由服务主机自动管理';
  root.querySelectorAll('.start-service').forEach(element=>element.title='Web 服务已连接');
}

try {
  const response=await fetch('/api/workbench/bootstrap',{cache:'no-store'});
  if (response.status === 404) {
    // 正在生成的旧服务无需被强制重启，已存在的生成/图片/作品 API 可立即使用。
    const [config,optimizer,version]=await Promise.all([request('/api/config'),request('/api/prompt-optimizer/status'),request('/assets/studio/version.json')]);
    boot={compatibility:true,canManage:false,version:version.version,
      settings:{...config,default_steps:config.defaults.steps,prestart_comfyui:true},
      providers:optimizer.enabled ? [{id:'server-managed',name:'服务端 AI 助手',baseUrl:origin,defaultModel:'服务端已配置',models:['服务端已配置'],enabled:true,serverManaged:true}] : []};
  } else {
    const data=await response.json();
    if (!response.ok) throw new Error(data.detail || `连接失败 (${response.status})`);
    boot=data;
  }
  if (boot.publicRelease?.enabled) {
    const management=await request('/api/public-settings/bootstrap');
    boot={...boot,...management};
    const {installPublicSettings}=await import('/assets/public-settings.js?v=20261018-web-settings');
    installPublicSettings(root,boot);
  }
  try { Object.assign(preferences,JSON.parse(localStorage.getItem('minimax-browser-preferences')||'{}')); } catch { /* 忽略损坏的浏览器偏好。 */ }
  // 每次打开以服务端配置为准，避免首次访问的空浏览器覆盖已有助手配置。
  localStorage.setItem('minimax-assistant-providers',JSON.stringify(boot.providers));
  for (const [source,target] of [['minimax-web-video-draft-v1','minimax-workbench-draft'],['minimax-web-works-page-size','minimax-works-size']]) {
    if (!localStorage.getItem(target) && localStorage.getItem(source)) localStorage.setItem(target,localStorage.getItem(source));
  }
  window.MinimaxBrowser={canManage:boot.canManage,compatibility:Boolean(boot.compatibility),version:boot.version,openPage};
  document.body.dataset.canManage=String(Boolean(boot.canManage));
  if (!boot.canManage) {
    for (const selector of ['.dock [data-target="settings"]','.page[data-page="settings"]']) {
      const element=query(selector);element.hidden=true;element.setAttribute('aria-hidden','true');
    }
  }
  await import('/assets/studio/demo-runtime.js?v=20261018-media-upload');
  browserControls();
  if (boot.openSettings) openPage('settings');
  const observer=new MutationObserver(() => {
    // 共享逻辑会按当前服务状态更新 disabled；再次约束浏览器不具备的能力。
    observer.disconnect();browserControls();observer.observe(root,{subtree:true,attributes:true,attributeFilter:['disabled','class']});
  });
  observer.observe(root,{subtree:true,attributes:true,attributeFilter:['disabled','class']});
} catch (error) {
  const message=document.createElement('div');message.className='browser-boot-error';
  const title=document.createElement('strong');title.textContent='工作台连接失败';
  const detail=document.createElement('p');detail.textContent=`${error.message}。请确认 Web 服务已更新并重新启动。`;
  const retry=document.createElement('button');retry.textContent='重新连接';retry.onclick=()=>location.reload();
  message.append(title,detail,retry);root.replaceChildren(message);
}
