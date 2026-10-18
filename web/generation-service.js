// 内嵌页面由工作台检查服务；独立 Web 页面直接检查当前 API。
export async function requestGenerationService(apiBase, useWorkbenchBridge = false) {
  if (useWorkbenchBridge && window.parent !== window) {
    return new Promise(resolve => {
      const nonce = crypto.randomUUID();
      const finish = result => {
        clearTimeout(timer);
        window.removeEventListener('message', receive);
        resolve(result);
      };
      const receive = event => {
        if (event.source === window.parent && event.data?.type === 'generation-service-result' && event.data.nonce === nonce) finish(event.data);
      };
      const timer = setTimeout(() => {
        window.alert('无法确认后端服务状态，请检查服务连接后重试。');
        finish({allowed:false});
      }, 10000);
      window.addEventListener('message', receive);
      window.parent.postMessage({type:'generation-service-check',nonce}, '*');
    });
  }
  try {
    const response = await fetch(`${apiBase}/api/status`, {signal:AbortSignal.timeout(5000)});
    if (response.ok) return {allowed:true,apiBase};
  } catch {}
  window.alert('后端服务未启动，请先启动后端服务后再点击生成。');
  return {allowed:false};
}
