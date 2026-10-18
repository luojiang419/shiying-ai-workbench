export const ASSISTANT_PRESETS = [
  {name:'OpenAI 兼容（通用 URL）', url:'', model:''},
  {name:'OpenAI', url:'https://api.openai.com', model:'gpt-4.1-mini', apiType:'OpenAI Responses API'},
  {name:'DeepSeek', url:'https://api.deepseek.com', model:'deepseek-chat'},
  {name:'通义千问 DashScope', url:'https://dashscope.aliyuncs.com/compatible-mode/v1', model:'qwen-plus'},
  {name:'SiliconFlow', url:'https://api.siliconflow.cn/v1', model:'deepseek-ai/DeepSeek-V3.2'},
  {name:'OpenRouter', url:'https://openrouter.ai/api/v1', model:'openai/gpt-4.1-mini'},
  {name:'智谱 GLM', url:'https://open.bigmodel.cn/api/paas/v4', model:'glm-4-flash'},
  {name:'Moonshot / Kimi', url:'https://api.moonshot.cn/v1', model:'moonshot-v1-8k'},
  {name:'豆包 / 火山方舟', url:'https://ark.cn-beijing.volces.com/api/v3', model:''},
  {name:'Groq', url:'https://api.groq.com/openai/v1', model:'llama-3.3-70b-versatile'},
  {name:'Gemini · OpenAI 兼容', url:'https://generativelanguage.googleapis.com/v1beta/openai', model:'gemini-2.5-flash'},
  {name:'Ollama', url:'http://127.0.0.1:11434/v1', model:''},
  {name:'LM Studio', url:'http://127.0.0.1:1234/v1', model:''},
];

export function normalizedAssistantUrl(raw) {
  let value = raw.trim().replace(/\/+$/, '');
  if (!value) return '';
  if (!/^https?:\/\//i.test(value)) value = `${/^(localhost|127\.|\[::1\])/i.test(value) ? 'http' : 'https'}://${value}`;
  const url = new URL(value);
  if (!['http:','https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('请输入不含密钥、查询参数的 HTTP 服务地址');
  const path = url.pathname.replace(/\/(responses|chat\/completions|models)\/?$/i, '').replace(/\/+$/, '');
  url.pathname = path || '/v1';
  return url.toString().replace(/\/+$/, '');
}
