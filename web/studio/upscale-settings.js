// 独立绑定，便于用正式控件验证设置与路径选择。
export function bindUpscaleSettings(root, invoke) {
  const qs = selector => root.querySelector(selector);
  const status = qs('.upscale-settings-state');
  const engine = qs('.upscale-engine');
  const path = qs('.topaz-path');
  const model = qs('.topaz-model');
  const buttons = [...root.querySelectorAll('.topaz-pick, .topaz-detect, .topaz-auto, .upscale-save')];
  let loaded = false;
  let busy = false;
  let ready = false;
  let detectedPath = null;
  function render(data, restoreSettings = false) {
    const settings = data.settings;
    if (restoreSettings) { engine.value = settings.engine; path.value = settings.topaz_path; }
    const selected = restoreSettings ? settings.topaz_model : model.value || settings.topaz_model;
    model.replaceChildren();
    for (const item of data.models || []) model.add(new Option(`${item.name} · ${item.description}`, item.id));
    ready = Boolean(data.topaz.ready && model.options.length);
    detectedPath = path.value.trim();
    model.disabled = !ready;
    if (!model.options.length) model.add(new Option('没有可用的经典模型', ''));
    model.value = [...model.options].some(option => option.value === selected) ? selected : data.preferred_model;
    qs('.topaz-install-info').textContent = ready
      ? `${data.topaz.version || '本机 Topaz'} · ${data.topaz.install_dir} · ${data.models.length} 个模型；缺失权重由 Topaz 自动下载`
      : data.topaz.error || '没有可用的经典模型';
    qs('.topaz-starlight').textContent = data.starlight.message;
    status.textContent = ready ? 'Topaz 已就绪' : 'Topaz 未就绪';
    loaded = true;
  }
  async function action(work) {
    if (busy) return;
    busy = true;
    buttons.forEach(button => { button.disabled = true; });
    engine.disabled = path.disabled = model.disabled = true;
    status.textContent = '正在检测并处理…';
    try { await work(); }
    catch (error) { status.textContent = String(error); }
    finally {
      busy = false;
      buttons.forEach(button => { button.disabled = false; });
      engine.disabled = path.disabled = false;
      model.disabled = !ready;
    }
  }
  const detect = async () => render(await invoke('get_upscale_settings', {path: path.value.trim()}));
  qs('.topaz-detect').addEventListener('click', () => action(detect));
  qs('.topaz-auto').addEventListener('click', () => action(async () => { path.value = ''; await detect(); }));
  qs('.topaz-pick').addEventListener('click', () => action(async () => {
    const selected = await invoke('pick_topaz_directory');
    if (selected !== null) { path.value = selected; await detect(); }
    else status.textContent = '已取消目录选择';
  }));
  qs('.upscale-save').addEventListener('click', () => action(async () => {
    if (engine.value === 'topaz' && (!ready || detectedPath !== path.value.trim())) await detect();
    if (engine.value === 'topaz' && !ready) throw new Error('请先选择并检测可用的 Topaz 安装路径');
    const data = await invoke('save_upscale_settings', {settings: {
      engine: engine.value, topaz_path: path.value.trim(), topaz_model: model.value || 'prob-4',
    }});
    render(data, true);
    status.textContent = `已保存 · ${engine.value === 'topaz' ? 'Topaz' : 'Real-ESRGAN'} 2×`;
  }));
  return { load: () => loaded ? Promise.resolve() : action(async () => render(await invoke('get_upscale_settings', {path:null}), true)) };
}
