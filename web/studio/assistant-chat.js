import { normalizedAssistantUrl } from './assistant-providers.js';

export function bindAssistantChat(root, invoke, savedProviders) {
  const qs = selector => root.querySelector(selector);
  const dialog = qs('.assistant-chat-dialog');
  const select = qs('.chat-provider');
  const log = qs('.chat-messages');
  const status = qs('.chat-state');
  const send = qs('.chat-send');
  let providers = [], history = [], epoch = 0;
  const append = (role, text) => {
    const message = document.createElement('div');
    message.className = `chat-message ${role}`;
    message.textContent = text;
    log.append(message);
    log.scrollTop = log.scrollHeight;
  };
  const reset = () => { epoch++; history = []; log.replaceChildren(); status.textContent = ''; send.disabled = select.options.length === 0; select.disabled = false; };
  const open = draft => {
    providers = draft ? [draft] : savedProviders();
    select.replaceChildren(...providers.map((p,index) => new Option(`${p.name || 'API'} · ${p.defaultModel || '未选模型'}`,String(index))));
    reset();
    if (!providers.length) status.textContent = '请先添加 API 配置';
    if (!dialog.open) dialog.showModal();
  };
  qs('.test-api-chat').addEventListener('click', () => open());
  qs('.close-chat').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { epoch++; });
  select.addEventListener('change', reset);
  qs('.chat-form').addEventListener('submit', async event => {
    event.preventDefault();
    const provider = providers[Number(select.value)];
    const content = qs('.chat-input').value.trim();
    if (!content || !provider || send.disabled) return;
    const token = ++epoch;
    send.disabled = select.disabled = true;
    status.textContent = '正在请求模型…';
    append('user', content);
    try {
      if (!provider.defaultModel) throw new Error('请先选择默认模型');
      const apiKey = provider.apiKey || (!window.MinimaxBrowser && provider.encryptedKey ? await invoke('unprotect_assistant_key', {encrypted:provider.encryptedKey}) : '');
      const result = await invoke('chat_assistant_api', {request:{
        baseUrl:normalizedAssistantUrl(provider.baseUrl), apiKey, providerId:provider.id,
        apiType:provider.apiType, model:provider.defaultModel,
        messages:[...history,{role:'user',content}],
      }});
      if (token !== epoch) return;
      history.push({role:'user',content},{role:'assistant',content:result});
      append('assistant', result);
      qs('.chat-input').value = '';
      status.textContent = '测试成功 · 模型已返回对话内容';
    } catch (error) {
      if (token === epoch) { append('error', String(error)); status.textContent = '测试失败'; }
    } finally {
      if (token === epoch) send.disabled = select.disabled = false;
    }
  });
  return open;
}
