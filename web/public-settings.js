// 公开版的所有者设置入口；管理码仅用于登录，后续由 HttpOnly 会话认证。
export function installPublicSettings(root, boot) {
  root.dataset.publicEdition = 'video-clone';
  root.dataset.publicOwner = String(Boolean(boot.publicOwner));
  const style = document.createElement('style');
  style.textContent = [
    'body[data-browser-workbench] #minimax-plan[data-public-owner="true"] .dock [data-target="settings"]{display:flex!important}',
    'body[data-browser-workbench] #minimax-plan[data-public-owner="true"] .page[data-page="settings"].active{display:block!important}',
    '#minimax-plan[data-public-edition] [data-setting="qwen-api"],#minimax-plan[data-public-edition] [data-setting-pane="qwen-api"]{display:none!important}',
    '.owner-settings-dialog{max-width:480px;width:calc(100vw - 36px);box-sizing:border-box;border:1px solid #435268;border-radius:14px;background:#18212e;color:#edf3fa;padding:24px;font:14px/1.8 system-ui}',
    '.owner-settings-dialog::backdrop{background:#0009}.owner-settings-dialog input{box-sizing:border-box;width:100%;padding:12px;background:#111a26;color:#fff;border:1px solid #435268;border-radius:8px}',
    '.owner-settings-dialog code{display:block;overflow-wrap:anywhere;margin:12px 0;font-size:12px}.owner-settings-dialog button{padding:9px 16px;margin:12px 8px 0 0;cursor:pointer}',
    '.owner-settings-status{color:#ffb4aa;min-height:1.8em}',
  ].join('');
  document.head.append(style);
  const entry = document.createElement('button');
  entry.type = 'button';
  entry.textContent = boot.publicOwner ? '退出设置' : '实例设置';
  entry.className = 'public-settings-entry';
  entry.addEventListener('click', async () => {
    if (boot.publicOwner) {
      await fetch('/api/public-settings/logout', {method:'POST'});
      location.hash = '';
      location.reload();
      return;
    }
    dialog.showModal();
    input.focus();
  });
  root.querySelector('.dock').append(entry);
  const dialog = document.createElement('dialog');
  dialog.className = 'owner-settings-dialog';
  dialog.setAttribute('aria-label','解锁实例设置');
  dialog.innerHTML = [
    '<form><h2>实例设置</h2><p>输入当前服务器的实例管理码，即可管理生成参数、AI 助手和超分设置。</p>',
    '<label>实例管理码<input type="password" autocomplete="off" required></label>',
    '<small>在当前 AutoDL 实例终端执行：</small>',
    '<code>cd /root/minimax-node &amp;&amp; venv/bin/python -m app.public_release admin-code</code>',
    '<div class="owner-settings-status" role="status"></div>',
    '<button type="submit">解锁设置</button><button type="button" class="owner-settings-cancel">取消</button></form>',
  ].join('');
  document.body.append(dialog);
  const input = dialog.querySelector('input');
  const status = dialog.querySelector('[role="status"]');
  dialog.querySelector('.owner-settings-cancel').onclick = () => dialog.close();
  dialog.addEventListener('close', () => {input.value='';status.textContent='';});
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const submit = dialog.querySelector('[type="submit"]');
    submit.disabled = true;
    status.textContent = '正在解锁…';
    try {
      const response = await fetch('/api/public-settings/login', {method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({code:input.value.trim()})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '解锁失败，请重试');
      input.value = '';
      location.hash = 'settings';
      location.reload();
    } catch (error) {status.textContent = error.message;}
    finally {submit.disabled = false;}
  };
  if (boot.publicOwner && location.hash === '#settings') boot.openSettings = true;
}
