// 2026-10-18 核对优云官方公开错误表；纯内置翻译，不发送翻译请求。
export const CLOUD_ERROR_CATALOG = Object.freeze([
  {
    "code": "226601",
    "english": "The instance is in arrears. Please go to Order Management to pay the unpaid order.",
    "zh": "实例已欠费，请前往订单管理支付欠费订单",
    "source": "https://www.compshare.cn/docs/gpus/compshareerrorcode"
  },
  {
    "code": "226602",
    "english": "The instance has expired. Please go to Renewal Management to renew the instance.",
    "zh": "实例已过期，请前往续费管理执行续费操作",
    "source": "https://www.compshare.cn/docs/gpus/compshareerrorcode"
  },
  {
    "code": "226603",
    "english": "gpu type is not support current image, You can try another gpu type",
    "zh": "所选镜像不支持该卡型，您可以换一个卡型重试",
    "source": "https://www.compshare.cn/docs/gpus/compshareerrorcode"
  },
  {
    "code": "226604",
    "english": "Resource not enough, Please try again later",
    "zh": "当前资源不足，请稍后再试",
    "source": "https://www.compshare.cn/docs/gpus/compshareerrorcode"
  },
  {
    "code": "226605",
    "english": "Update image use time task exist.",
    "zh": "更新镜像使用时长任务已存在",
    "source": "https://www.compshare.cn/docs/gpus/compshareerrorcode"
  },
  {
    "code": "226606",
    "english": "The account has not been verified.",
    "zh": "账号未实名认证，请先实名认证",
    "source": "https://www.compshare.cn/docs/gpus/compshareerrorcode"
  },
  {
    "code": "8433",
    "english": "service error， please contact technical support",
    "zh": "服务异常，请联系技术支持",
    "source": "https://www.compshare.cn/docs/gpus/compshareerrorcode"
  },
  {
    "code": "param_error",
    "english": "Invalid param",
    "zh": "参数不合法，调用我们未支持的接口时会触发",
    "httpStatus": 400,
    "type": "invalid_request_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "invalid_messages",
    "english": "Sensitive chat messages",
    "zh": "对话内容触发敏感词/合规校验",
    "httpStatus": 400,
    "type": "invalid_request_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "sensitive_check_error",
    "english": "Sensitive check error",
    "zh": "合规/敏感内容检测失败",
    "httpStatus": 400,
    "type": "invalid_request_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "websearch_error",
    "english": "Web search error",
    "zh": "联网搜索失败或不可用",
    "httpStatus": 400,
    "type": "invalid_request_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "model_error",
    "english": "No permission to use the model",
    "zh": "没有模型权限",
    "httpStatus": 400,
    "type": "invalid_request_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "tokens_too_long",
    "english": "Prompt tokens too long",
    "zh": "提示词过长",
    "httpStatus": 400,
    "type": "invalid_request_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "auth_error",
    "english": "Validate Certification failed",
    "zh": "访问凭据无效或已失效，请检查接口密钥或重新登录。",
    "httpStatus": 401,
    "type": "invalid_request_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "timeout",
    "english": "Request timeout, please try again later",
    "zh": "请求超时，请稍后重试",
    "httpStatus": 408,
    "type": "timeout",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "rate_limit",
    "english": "Rate limit exceeded, please try again later",
    "zh": "触发限流，请稍后重试",
    "httpStatus": 429,
    "type": "rate_limit_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "internal_error",
    "english": "Internal Server Error",
    "zh": "服务器内部错误",
    "httpStatus": 500,
    "type": "internal_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "model_server_error",
    "english": "Request llm server Error",
    "zh": "模型服务异常，请稍后重试。",
    "httpStatus": 500,
    "type": "internal_error",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "gateway_timeout_error",
    "english": "Gateway timeout, please try again later or use stream api",
    "zh": "云端网关响应超时，请稍后查询任务状态。",
    "httpStatus": 504,
    "type": "timeout",
    "source": "https://www.compshare.cn/docs/modelverse/models/common/error-code"
  },
  {
    "code": "prompt_moderation_blocked",
    "english": "",
    "zh": "提示词未通过审核。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/qwen-image-api"
  },
  {
    "code": "prompt_moderation_unassessable",
    "english": "",
    "zh": "提示词无法评估。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/qwen-image-api"
  },
  {
    "code": "prompt_moderation_failed",
    "english": "",
    "zh": "审核服务异常或超时。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/qwen-image-api"
  },
  {
    "code": "prompt_moderation_timeout",
    "english": "",
    "zh": "审核服务异常或超时。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/qwen-image-api"
  },
  {
    "code": "reference_moderation_blocked",
    "english": "",
    "zh": "参考图未通过审核。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/qwen-image-api"
  },
  {
    "code": "reference_moderation_unassessable",
    "english": "",
    "zh": "参考图无法评估。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/qwen-image-api"
  },
  {
    "code": "reference_moderation_failed",
    "english": "",
    "zh": "参考图审核异常或超时。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/qwen-image-api"
  },
  {
    "code": "reference_moderation_timeout",
    "english": "",
    "zh": "参考图审核异常或超时。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/qwen-image-api"
  },
  {
    "code": "bad_request_error",
    "english": "",
    "zh": "请求参数不符合平台要求，请检查输入内容和生成设置。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/minimax-h3-video-api"
  },
  {
    "code": "8039",
    "english": "job not found",
    "zh": "未找到该云端任务，请核对任务编号。",
    "source": "https://www.compshare.cn/docs/minimax-h3/api/minimax-h3-video-api"
  },
  {
    "code": "generation_failed",
    "english": "",
    "zh": "云端生成失败，平台未返回更具体的中文原因。",
    "source": "已验证的优云 H3 实际任务返回"
  }
]);


// 原文只供诊断保存；未知动态英文也必须返回可读中文，绝不外发翻译。
export function cloudErrorText(value, options = {}) {
  const raw = typeof value === 'string' ? value : value?.message || value?.reason || value?.detail || value?.error?.message || '';
  const text = String(raw || '').trim();
  const code = String(options.code || value?.code || value?.error?.code || '');
  const lower = text.toLowerCase();
  if (/non.consensual intimate|sexualized.*(?:real person|identifiable face)|real person.*sexualized/i.test(text)) {
    const picture = text.match(/picture\s*(\d+)/i)?.[1];
    const details = [];
    if (/sexualized attire|stockings|garter|strapless|mini dress/i.test(text)) details.push(/thigh.high polka.dot stockings.*garter straps.*strapless mini dress/i.test(text)?'高筒波点丝袜及吊袜带、露肩迷你连衣裙等带有性暗示的服装':'带有性暗示的服装');
    if (/low.angle|leg.opening/i.test(text)) details.push('低角度张腿镜头');
    if (/suggestive pos/i.test(text)) details.push('挑逗性姿势');
    return `未通过平台内容审查。平台判定：请求将${picture ? `参考图片 ${picture} 中` : ''}真实人物可识别的面部移入${details.length ? `包含${details.join('、')}的` : '带有性暗示的'}视频，构成涉及真实人物的非自愿亲密影像，因此已终止生成。`;
  }
  const rules = [
    [/insufficient (?:balance|points|credits|funds)|not enough (?:points|credits)|balance.*insufficient/i, '账户余额或积分不足，请充值后再提交。'],
    [/content.*(?:policy|moderation|safety)|sexual|nsfw|sensitive content|moderation.*block/i, '未通过平台内容审查，平台已拒绝本次请求。请检查提示词和参考素材是否符合平台规则。'],
    [/api.?key.*(?:invalid|expired|missing)|unauthori[sz]ed|authentication/i, '身份验证失败，请检查 API Key 或重新登录。'],
    [/rate.?limit|too many requests/i, '请求过于频繁，请稍后再试。'],
    [/timeout|timed out|deadline exceeded/i, '云服务响应超时。任务可能仍在执行，请先查看任务记录，避免重复提交。'],
    [/job not found|task not found/i, '未找到该云端任务，请核对任务编号。'],
    [/file.*too large|payload too large|request.*too large|exceed.*(?:mb|mib|bytes|size)/i, '素材或请求体超过平台容量限制，请减少素材或压缩后再提交。'],
    [/invalid.*(?:image|video|audio)|unsupported.*(?:format|media)|decode.*fail/i, '参考素材格式无效或无法解码，请检查素材格式和文件是否完整。'],
    [/failed to fetch|network|connection|dns|fetch failed/i, '网络连接失败，请检查网络。任务记录已保留，请勿重复提交尚未确认的任务。'],
    [/permission denied|forbidden|access denied/i, '当前账号无权执行此操作，请检查账号权限。'],
    [/internal server|service unavailable|bad gateway|upstream.*error/i, '云服务暂时异常，请稍后查看任务状态。'],
  ];
  for (const [pattern, zh] of rules) if (pattern.test(text)) return zh;
  // 已翻译的中文原因再次读取时保留全文，不能被通用错误码覆盖。
  if (/[\u3400-\u9fff]/.test(text) && !/[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){1,}/.test(text)) return text;
  const known = CLOUD_ERROR_CATALOG.find(item => (code && item.code === code) || new RegExp(`(^|[^a-z0-9_])${item.code}([^a-z0-9_]|$)`,'i').test(text) || (item.english && lower.includes(item.english.toLowerCase())));
  if (known) return known.zh;
  const http = Number(options.status || text.match(/HTTP\s*(\d{3})/i)?.[1]);
  const httpText = {400:'请求参数不符合平台要求，请检查生成设置和参考素材。',401:'身份验证已失效，请重新登录或检查 API Key。',402:'账户余额或积分不足。',403:'平台拒绝访问，请检查账号权限及内容规则。',404:'云端未找到请求的任务或资源。',409:'任务状态冲突，请刷新任务记录后查看。',413:'素材或请求体超过平台容量限制。',422:'生成参数校验失败，请检查提示词、素材及生成设置。',429:'请求过于频繁，请稍后查看任务状态。',500:'云服务内部异常，请稍后查看任务状态。',502:'云端网关异常，请稍后查看任务状态。',503:'云服务暂不可用，请稍后查看任务状态。',504:'云端网关超时，请先查看任务记录，避免重复提交。'};
  if (httpText[http]) return httpText[http];
  // 中文本地错误可保留技术标识；出现完整英文说明时使用中文兜底。

  return '平台返回了未收录的错误，详细原文已保留在本机诊断记录中。请联系管理员核查任务，避免重复提交。';
}
