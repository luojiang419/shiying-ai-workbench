import {cloudErrorText} from './cloud-error-zh.js';
const invoke = window.__TAURI__?.core?.invoke || window.parent?.__TAURI__?.core?.invoke;
const ACCESS_KEY = 'minimax-platform-access';
const AUTO_KEY = 'minimax-platform-auto-login';
const USER_KEY = 'minimax-platform-user-id';
let basePromise;

const SESSION_KEY='minimax-platform-session-generation';
function identityChanged(){
  sessionStorage.setItem(SESSION_KEY,crypto.randomUUID());
  for(const target of new Set([window,window.parent])){
    try{target.dispatchEvent(new target.CustomEvent('platform-identity-changed'));}catch{}
  }
}
const authExclusive=run=>navigator.locks ? navigator.locks.request('minimax-platform-auth-session',run) : run();
function requestIdentity(){return `${sessionStorage.getItem(SESSION_KEY)||''}:${sessionStorage.getItem(USER_KEY)||''}`;}
function assertIdentity(expected){if(requestIdentity()!==expected)throw new Error('登录账号已变化，已忽略上一账号的返回结果');}

async function baseUrl() {
  if (!basePromise) basePromise = invoke('platform_service_config').then(x => x.base_url);
  return basePromise;
}

async function raw(path, options = {}) {
  const base = await baseUrl();
  const headers = {...(options.headers || {})};
  if (base.startsWith('https://')) {
    let body = options.body || null;
    if (body instanceof FormData) {
      const image = body.get('image');
      const dataUrl = image instanceof Blob ? await new Promise((resolve,reject) => {
        const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('读取截图失败'));reader.readAsDataURL(image);
      }) : null;
      body = JSON.stringify({text:String(body.get('text')||''),image_data_url:dataUrl});
      path += '-json';
    }
    if (body) headers['Content-Type']='application/json';
    const response = await invoke('platform_http',{path,method:options.method||'GET',body,headers,responseType:options.responseType==='blob'?'base64':'json',taskContext:options.taskContext||null});
    const data = options.responseType==='blob' && response.status<400
      ? new Blob([Uint8Array.from(atob(response.body.base64),c=>c.charCodeAt(0))],{type:response.body.mime})
      : response.body;
    if(response.status>=400){const error=Object.assign(new Error(cloudErrorText(data?.detail || data,{status:response.status})),{raw:data});error.status=response.status;throw error}
    return data;
  }
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(base + path, {...options, headers});
  const type = response.headers.get('content-type') || '';
  const data = options.responseType==='blob' ? await response.blob() : type.includes('json') ? await response.json() : await response.text();
  if (!response.ok) { const error=Object.assign(new Error(cloudErrorText(data?.detail || data,{status:response.status})),{raw:data}); error.status=response.status; throw error; }
  return data;
}

async function refreshUnlocked() {
  const identity=requestIdentity();
  const token = await invoke('platform_load_refresh_token');
  if (!token) throw new Error('请先登录云端账号');
  const data = await raw('/auth/refresh', {method:'POST',body:JSON.stringify({refresh_token:token})});
  assertIdentity(identity);
  await invoke('platform_save_refresh_token', {token:data.refresh_token});
  assertIdentity(identity);
  sessionStorage.setItem(ACCESS_KEY, data.access_token);
  return data.access_token;
}

async function refresh(){return authExclusive(refreshUnlocked);}

async function request(path, options = {}) {
  const identity=requestIdentity();
  let token = sessionStorage.getItem(ACCESS_KEY);
  if (!token) token = await refresh();
  assertIdentity(identity);
  const headers = {...(options.headers || {}), Authorization:`Bearer ${token}`};
  try {const result=await raw(path,{...options,headers});assertIdentity(identity);return result;}
  catch (error) {
    assertIdentity(identity);
    if (error.status!==401) throw error;
    token = await refresh();assertIdentity(identity);
    const result=await raw(path,{...options,headers:{...headers,Authorization:`Bearer ${token}`}});
    assertIdentity(identity);return result;
  }
}

async function loginUnlocked(username,password,autoLogin,totpCode) {
  const identity=requestIdentity();
  const data = await raw('/auth/login',{method:'POST',body:JSON.stringify({username,password,totp_code:totpCode||null})});
  assertIdentity(identity);
  await invoke('platform_save_refresh_token',{token:data.refresh_token});
  assertIdentity(identity);
  sessionStorage.setItem(ACCESS_KEY,data.access_token);
  sessionStorage.setItem(USER_KEY,data.user.id);
  identityChanged();
  localStorage.setItem(AUTO_KEY,autoLogin?'true':'false');
  return data;
}

async function login(...args){return authExclusive(()=>loginUnlocked(...args));}

async function rememberLogin(username,password,remember) {
  if (remember) await invoke('platform_save_login',{username,password});
  else await invoke('platform_clear_login');
}

async function rememberedLogin() { return invoke('platform_load_login'); }

async function logout() {
  const access=sessionStorage.getItem(ACCESS_KEY);
  sessionStorage.removeItem(ACCESS_KEY);sessionStorage.removeItem(USER_KEY);identityChanged();
  localStorage.setItem(AUTO_KEY,'false');
  await authExclusive(async()=>{
    const token=await invoke('platform_load_refresh_token').catch(()=>null);
    if(token&&access)await raw('/auth/logout',{method:'POST',headers:{Authorization:`Bearer ${access}`},body:JSON.stringify({refresh_token:token})}).catch(()=>{});
    await invoke('platform_clear_refresh_token');
  });
}

async function bootstrap() {
  if (localStorage.getItem(AUTO_KEY)!=='true' && !sessionStorage.getItem(ACCESS_KEY))return null;
  sessionStorage.removeItem(USER_KEY);identityChanged();
  const identity=requestIdentity();
  try {
    if(!sessionStorage.getItem(ACCESS_KEY))await refresh();
    const member=await request('/me');sessionStorage.setItem(USER_KEY,member.id);identityChanged();return member;
  }catch {if(requestIdentity()===identity){sessionStorage.removeItem(ACCESS_KEY);sessionStorage.removeItem(USER_KEY);identityChanged();}return null;}
}

async function submit(payload, requestId = crypto.randomUUID(), context = {}) {
  const job = await request('/jobs',{method:'POST',headers:{'Idempotency-Key':requestId},taskContext:{...context,requestId,ownerId:sessionStorage.getItem(USER_KEY)},body:JSON.stringify({payload})});
  return {task_id:job.id, quoted_points:job.quoted_points};
}

async function task(taskId) {
  const job = await request(`/jobs/${encodeURIComponent(taskId)}`);
  return {task:{id:job.id,status:job.status,
    resolution:job.resolution,ratio:job.ratio,duration:job.duration,
    estimated_remaining_seconds:job.estimated_remaining_seconds,eta_updated_at:job.eta_updated_at,
    content:{url:job.result_url||''},error:job.error||'',created_at:job.created_at,updated_at:job.updated_at}};
}

async function cancel(taskId) { return request(`/jobs/${encodeURIComponent(taskId)}/cancel`,{method:'POST'}); }
async function balance() { const wallet=await request('/wallet');return {available_points:wallet.available,reserved_points:wallet.reserved,total_points:wallet.available+wallet.reserved}; }

export const platformApi={baseUrl,raw,request,login,logout,bootstrap,rememberLogin,rememberedLogin,submit,task,cancel,balance,userId:()=>sessionStorage.getItem(USER_KEY)};
