// 公开镜像的产品范围和品牌，独立于桌面/自用版本。
const response = await fetch('/api/public-release', {cache:'no-store'});
if (response.ok) {
  const edition = await response.json();
  document.title = edition.brand;
  const root = document.querySelector('#minimax-plan');
  root.dataset.publicEdition = 'video-clone';
  const style = document.createElement('style');
  style.textContent = '#minimax-plan [data-target="image"],#minimax-plan [data-page="image"],#minimax-plan [data-target="settings"],#minimax-plan [data-page="settings"]{display:none!important}.public-onboarding{padding:16px 20px;border:1px solid var(--mm-line);border-radius:12px;margin:12px 0 22px;color:var(--mm-text);line-height:1.8}.public-onboarding a{color:var(--mm-accent,#a8e6cb);margin-left:12px}.public-credit{font-size:12px;opacity:.65;margin-top:8px}';
  document.head.append(style);
  const brand = root.querySelector('.brand');
  if (brand) {
    const name=brand.querySelector('span:not(.brand-mark):not(.brand-version)');if(name)name.textContent=edition.brand;
    const attribution=document.createElement('span');attribution.textContent='MiniMax H3';attribution.style.cssText='font-size:12px;opacity:.7';brand.append(attribution);
  }
  const mark = root.querySelector('.brand-mark');if(mark)mark.textContent='诗';
  const home = root.querySelector('[data-page="overview"]');
  if (home) {
    const card=document.createElement('div');card.className='public-onboarding';
    card.append(document.createTextNode(edition.assistantConfigured?'DeepSeek Flash 已配置':'首次使用请配置你自己的 DeepSeek Flash Key'));
    const setup=document.createElement('a');setup.href='/setup/';setup.textContent='AI 助手配置 →';card.append(setup);
    const credit=document.createElement('div');credit.className='public-credit';credit.textContent='视频创作 · 人物克隆 · Built with MiniMax H3';card.append(credit);
    home.prepend(card);
  }
}
