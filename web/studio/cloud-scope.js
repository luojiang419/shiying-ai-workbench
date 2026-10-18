// 账号云任务只能归属明确登录用户；个人 Key 仅显示同一 Key 的本机提交回执。
export function currentCloudScope() {
  return {ownerId:sessionStorage.getItem('minimax-platform-user-id')||'',personalScope:sessionStorage.getItem('minimax-personal-cloud-scope')||'',engine:localStorage.getItem('minimax-active-generation-engine')||'local'};
}
export function cloudTaskVisible(task, scope=currentCloudScope()) {
  if(task.provider==='platform')return Boolean(scope.ownerId && task.ownerId===scope.ownerId);
  if(scope.engine==='platform_h3')return false;
  if(!scope.personalScope || task.personalScope!==scope.personalScope || !(task.localReceipt || task.requestId))return false;
  return scope.ownerId ? task.ownerId===scope.ownerId : !task.ownerId;
}
export function workVisible(work,scope=currentCloudScope()) {
  if(work.cloud)return cloudTaskVisible(work,scope);
  if(/^(?:优云H3-|cloud-h3-)/.test(work.filename||''))return false;
  return scope.engine!=='platform_h3';
}
