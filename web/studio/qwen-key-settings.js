export function bindQwenKeySettings(root, {baseUrl, startService}) {
  const pane = root.querySelector('[data-setting-pane="qwen-api"]');
  const $ = selector => pane.querySelector(selector);
  const dialog = $('.qwen-key-dialog');
  let keys = [], access = {}, loading = false;
  const message = text => { $('.qwen-keys-state').textContent = text; };
  const request = async (path, options = {}) => {
    const response = await fetch(baseUrl() + path, {...options, headers:{'Content-Type':'application/json', ...options.headers}});
    if (response.status === 204) return {};
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 ${response.status}`);
    return data;
  };
  const when = value => value ? new Date(value * 1000).toLocaleString('zh-CN', {hour12:false}) : '尚未使用';
  const button = (text, run) => {
    const element = document.createElement('button');
    element.type = 'button'; element.className = 'secondary'; element.textContent = text;
    element.addEventListener('click', async () => {
      element.disabled = true;
      try { await run(element); } catch (error) { message(error.message || String(error)); }
      finally { element.disabled = false; }
    });
    return element;
  };
  const masked = key => `${key.token_prefix}••••••••`;
  const secret = key => request(`/api/qwen/keys/${encodeURIComponent(key.id)}/secret`).then(data => data.api_key);
  const copy = async text => { await navigator.clipboard.writeText(text); message('已复制'); };

  function render() {
    const query = $('.qwen-key-search').value.trim().toLocaleLowerCase();
    const body = $('.qwen-keys-body'); body.replaceChildren();
    $('.qwen-active-total').textContent = keys.filter(key => !key.disabled).length;
    $('.qwen-calls-total').textContent = keys.reduce((sum, key) => sum + key.calls_today, 0);
    $('.qwen-images-total').textContent = keys.reduce((sum, key) => sum + key.image_count, 0);
    $('.qwen-keys-count').textContent = `${keys.length} 个`;
    for (const key of keys.filter(item => item.name.toLocaleLowerCase().includes(query))) {
      const row = document.createElement('tr'); row.dataset.keyId = key.id;
      const cells = Array.from({length:7}, () => document.createElement('td'));
      cells[0].textContent = key.name;
      const value = document.createElement('code'); value.className = 'qwen-key-value'; value.textContent = masked(key);
      cells[1].append(value);
      cells[2].textContent = key.disabled ? '已停用' : '启用中';
      cells[2].className = key.disabled ? 'qwen-key-disabled' : 'qwen-key-enabled';
      cells[3].textContent = key.calls_today;
      cells[4].textContent = key.image_count;
      cells[5].textContent = when(key.last_used_at);
      const actions = document.createElement('div'); actions.className = 'qwen-key-actions';
      if (key.can_reveal) {
        let revealed = false;
        const reveal = button('查看', async element => {
          if (revealed) { value.textContent = masked(key); element.textContent = '查看'; revealed = false; }
          else { value.textContent = await secret(key); element.textContent = '隐藏'; revealed = true; }
        });
        reveal.setAttribute('aria-label', `查看或隐藏 ${key.name} 的密钥`);
        const copyKey = button('复制', () => secret(key).then(copy));
        copyKey.setAttribute('aria-label', `复制 ${key.name} 的密钥`);
        actions.append(reveal, copyKey, button('复制接入信息', async () => {
          const token = await secret(key);
          const url = access.client_url || `${new URL(baseUrl()).origin}/qwen`;
          await copy(`名称：${key.name}\nAPI 地址：${url}\nOpenAI base_url：${url}/v1\nAPI Key：${token}\n模型：qwen-image-2.1`);
        }));
      } else {
        const note = document.createElement('small'); note.textContent = '仅保存哈希，原 Key 仍可用'; cells[1].append(note);
      }
      if (!key.disabled) {
        actions.append(button('重置', async () => {
          if (!window.confirm(`重置「${key.name}」会使原 Key 立即失效，调用方必须更新。确定重置吗？`)) return;
          const data = await request(`/api/qwen/keys/${encodeURIComponent(key.id)}/rotate`, {method:'POST'});
          showCreated(key.name, data.api_key); await load();
        }), button('停用', async () => {
          if (!window.confirm(`停用「${key.name}」后，原调用方将无法继续访问。确定停用吗？`)) return;
          await request(`/api/qwen/keys/${encodeURIComponent(key.id)}/disable`, {method:'POST'}); await load();
        }));
      }
      actions.append(button('删除', async () => {
        if (!window.confirm(`永久删除「${key.name}」？原 Key 将立即失效，已生成图片保留。`)) return;
        await request(`/api/qwen/keys/${encodeURIComponent(key.id)}`, {method:'DELETE'}); await load();
      }));
      cells[6].append(actions); row.append(...cells); body.append(row);
    }
    if (!body.children.length) {
      const row = document.createElement('tr'), cell = document.createElement('td'); cell.colSpan = 7;
      cell.textContent = query ? '没有匹配的密钥' : '尚未创建密钥，点击“创建密钥”分配给调用方'; row.append(cell); body.append(row);
    }
    $('.qwen-compat-address').textContent = access.client_url || `${baseUrl()}/qwen`;
    const failed = (access.listeners || []).filter(item => !item.listening);
    $('.qwen-compat-status').textContent = failed.length ? failed.map(item => item.error).join('；')
      : access.legacy_ports_enabled ? ((access.listeners || []).length ? '原 8192 / 8193 地址已接管，旧应用无需修改地址和 Key' : '兼容地址已配置，重启本机服务后启用')
      : '新应用可使用兼容地址接入；导入旧项目后保留原端口';
    $('.qwen-migration-status').textContent = access.migration_error || (access.migration
      ? `已迁移 ${access.migration.keys} 把 Key、${access.migration.images} 张图片和 ${access.migration.jobs} 条任务；原 Key 保持有效。`
      : '可导入旧 Qwen 项目，保留已有 Key、状态和关联图片。');
  }

  async function load() {
    if (loading) return;
    loading = true; message('正在读取密钥配置…');
    try {
      const [data, status] = await Promise.all([request('/api/qwen/keys'), request('/api/qwen/access')]);
      keys = data.keys || []; access = status; render(); message('密钥配置已读取'); $('.qwen-keys-start').hidden = true;
    } catch (error) { message(`读取失败：${error.message}。请确认本机服务已启动。`); $('.qwen-keys-start').hidden = false; }
    finally { loading = false; }
  }
  function showCreated(name, token) {
    $('.qwen-key-form').hidden = true; $('.qwen-key-created').hidden = false;
    $('.qwen-created-name').textContent = name; $('.qwen-created-value').textContent = token;
    if (!dialog.open) dialog.showModal();
  }
  const close = () => { dialog.close(); $('.qwen-created-value').textContent = ''; };
  $('.qwen-key-new').addEventListener('click', () => {
    $('.qwen-key-form').hidden = false; $('.qwen-key-created').hidden = true;
    $('.qwen-key-name').value = ''; $('.qwen-key-form-state').textContent = '';
    dialog.showModal(); $('.qwen-key-name').focus();
  });
  $('.qwen-key-form').addEventListener('submit', async event => {
    event.preventDefault(); const submit = $('.qwen-key-submit'); submit.disabled = true;
    try {
      const data = await request('/api/qwen/keys', {method:'POST', body:JSON.stringify({name:$('.qwen-key-name').value.trim()})});
      showCreated(data.name, data.api_key); await load();
    } catch (error) { $('.qwen-key-form-state').textContent = error.message; }
    finally { submit.disabled = false; }
  });
  for (const element of pane.querySelectorAll('[data-qwen-key-close]')) element.addEventListener('click', close);
  dialog.addEventListener('close', () => { $('.qwen-created-value').textContent = ''; });
  $('.qwen-created-copy').addEventListener('click', () => copy($('.qwen-created-value').textContent).catch(error => message(error.message)));
  $('.qwen-key-search').addEventListener('input', render);
  $('.qwen-keys-refresh').addEventListener('click', load);
  $('.qwen-keys-start').addEventListener('click', async () => { await startService(); await load(); });
  $('.qwen-migrate').addEventListener('click', async event => {
    const trigger = event.currentTarget;
    trigger.disabled = true;
    try {
      message('正在迁移旧项目数据…');
      await request('/api/qwen/import', {method:'POST', body:JSON.stringify({path:$('.qwen-migration-path').value.trim()})});
      await load();
    } catch (error) { message(`迁移未完成：${error.message}`); }
    finally { trigger.disabled = false; }
  });
  return {load};
}
