// 应用共享动效：只响应可见性切换，不让轮询刷新重复播放动画。
(() => {
  if (globalThis.WorkbenchMotion) return;
  const EASING = 'cubic-bezier(.22,1,.36,1)';
  const preference = matchMedia('(prefers-reduced-motion: reduce)');
  const animations = new Set();
  const visibleStates = new WeakMap();
  const pageKeys = new WeakMap();
  const pageSelector = '#minimax-plan .page, #minimax-plan .settings-pane, #keyframePanel, #referencePanel, '
    + '[data-mobile-section]:not(body), .layout > .assetsarea, .layout > .workspace, .layout > .queuepanel';
  let inheritedReduced = false;
  const reduced = () => preference.matches || inheritedReduced || Boolean(document.querySelector('#minimax-plan.reduce-motion'));
  const visible = element => element.isConnected && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
  function enter(element, duration = 260) {
    if (!element || reduced() || !visible(element)) return;
    const previous = element.getAnimations().find(animation => animations.has(animation));
    const start = previous ? {opacity:getComputedStyle(element).opacity, translate:getComputedStyle(element).translate}
      : {opacity:0, translate:'0 8px'};
    previous?.cancel();
    const animation = element.animate([start, {opacity:1, translate:'0 0'}], {duration, easing:EASING});
    animations.add(animation);
    animation.finished.catch(() => {}).finally(() => animations.delete(animation));
  }
  function remove(element, done = () => element.remove()) {
    if (!element || !element.isConnected || reduced()) { done(); return; }
    element.inert = true;
    element.style.pointerEvents = 'none';
    const animation = element.animate([{opacity:getComputedStyle(element).opacity}, {opacity:0}], {duration:200, easing:EASING, fill:'forwards'});
    animations.add(animation);
    animation.finished.catch(() => {}).finally(() => { animations.delete(animation); done(); });
  }
  function page(element, key) {
    if (pageKeys.get(element) === key) return;
    pageKeys.set(element, key);
    enter(element);
  }
  function settle(element) {
    element?.getAnimations().filter(animation => animations.has(animation)).forEach(animation => animation.cancel());
  }
  function sendPreference(frame) {
    frame.contentWindow?.postMessage({type:'workbench-motion-preference', reduced:reduced()}, '*');
  }
  function updatePreference() {
    const value = String(reduced());
    if (document.documentElement.dataset.motionReduced === value) return;
    document.documentElement.dataset.motionReduced = value;
    if (reduced()) [...animations].forEach(animation => animation.cancel());
    document.querySelectorAll('iframe').forEach(sendPreference);
  }
  globalThis.WorkbenchMotion = {enter, remove, page, settle, reduced, easing:EASING};
  // 跨文档跳转可能因快速导航、减少动态效果或页面尚未 opt-in 而中止。
  // 消费浏览器提供的过渡 Promise，避免正常中止成为未处理异常。
  for (const type of ['pageswap', 'pagereveal']) window.addEventListener(type, event => {
    const transition = event.viewTransition;
    if (!transition) return;
    transition.ready.catch(() => {});
    transition.finished.catch(() => {});
    transition.updateCallbackDone.catch(() => {});
  });
  function install() {
    updatePreference();
    document.querySelectorAll('[data-motion-initial]').forEach(element => enter(element));
    document.querySelectorAll('#regionOptions, #depthExtractionOptions, #cloudDepthOptions, #backgroundImageDetails, #personPicker, #peopleTools, #lightweightPromptPreview')
      .forEach(element => element.setAttribute('data-motion-expand', ''));
    document.querySelectorAll(pageSelector).forEach(element => visibleStates.set(element, visible(element)));
    const observer = new MutationObserver(records => {
      const candidates = new Set();
      for (const record of records) {
        const target = record.target;
        if (target.id === 'minimax-plan' && record.attributeName === 'class') updatePreference();
        if (record.type !== 'attributes' || !(target instanceof Element)) continue;
        if (target.matches(pageSelector)) candidates.add(target);
        target.querySelectorAll(pageSelector).forEach(element => candidates.add(element));
      }
      const entering = [];
      for (const element of candidates) {
        const now = visible(element), previous = visibleStates.get(element);
        visibleStates.set(element, now);
        if (now && previous === false) entering.push(element);
      }
      for (const element of entering) {
        if (!entering.some(parent => parent !== element && parent.contains(element))) enter(element);
      }
    });
    observer.observe(document.body, {subtree:true, attributes:true,
      attributeFilter:['class', 'hidden', 'data-mobile-section', 'data-clone-mobile-view']});
    document.addEventListener('load', event => {
      if (event.target instanceof HTMLIFrameElement) sendPreference(event.target);
    }, true);
  }
  preference.addEventListener('change', updatePreference);
  window.addEventListener('message', event => {
    if (event.source !== parent || event.data?.type !== 'workbench-motion-preference') return;
    inheritedReduced = event.data.reduced === true;
    updatePreference();
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once:true});
  else install();
})();
