// 保留完整规格值用于请求和历史任务，界面按画幅筛选分辨率。
export function resolutionAspect(value) {
  return String(value).match(/\b\d+:\d+\b/)?.[0] || (String(value).startsWith('Square') ? '1:1' : '');
}

function resolutionArea(value) {
  const size = String(value).match(/(\d+)x(\d+)/);
  return size ? Number(size[1]) * Number(size[2]) : 0;
}

function sizeLabel(option) {
  const value = option.value;
  if (value.includes('|')) return value.split('|')[0];
  const size = value.match(/(\d+)x(\d+)/);
  if (!size) return option.textContent;
  const mp = value.startsWith('Square') ? '0.26MP' : value.split(' ')[0];
  const note = value.match(/\(([^)]+)\)/)?.[1];
  return [mp, `${size[1]}×${size[2]}`, note].filter(Boolean).join(' · ');
}

export function bindResolutionSelectors(valueSelect, aspectSelect, sizeSelect) {
  let signature = '';
  function sync() {
    const options = [...valueSelect.options];
    const next = JSON.stringify([valueSelect.value, valueSelect.disabled, options.map(o => [o.value, o.textContent])]);
    if (next === signature) return;
    signature = next;
    const aspects = [...new Set(options.map(o => resolutionAspect(o.value)).filter(Boolean))];
    const aspect = resolutionAspect(valueSelect.value);
    aspectSelect.replaceChildren(...aspects.map(value => new Option(value, value)));
    aspectSelect.value = aspect;
    sizeSelect.replaceChildren(...options.filter(o => resolutionAspect(o.value) === aspect)
      .map(o => new Option(sizeLabel(o), o.value)));
    sizeSelect.value = valueSelect.value;
    aspectSelect.disabled = sizeSelect.disabled = valueSelect.disabled || !options.length;
  }
  function choose(value) {
    valueSelect.value = value;
    sync();
    valueSelect.dispatchEvent(new Event('change', {bubbles: true}));
  }
  aspectSelect.addEventListener('change', () => {
    const candidates = [...valueSelect.options].filter(o => resolutionAspect(o.value) === aspectSelect.value);
    const currentArea = resolutionArea(valueSelect.value);
    const cloudSize = valueSelect.value.split('|')[0];
    const selected = candidates.find(o => o.value.split('|')[0] === cloudSize)
      || candidates.reduce((best, o) => !best || (currentArea &&
        Math.abs(Math.log(resolutionArea(o.value) / currentArea)) <
        Math.abs(Math.log(resolutionArea(best.value) / currentArea))) ? o : best, null);
    if (selected) choose(selected.value);
  });
  sizeSelect.addEventListener('change', () => choose(sizeSelect.value));
  valueSelect.addEventListener('change', sync);
  new MutationObserver(sync).observe(valueSelect, {childList: true, attributes: true, attributeFilter: ['disabled']});
  sync();
  return {sync};
}
