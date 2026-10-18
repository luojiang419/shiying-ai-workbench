"""公开镜像的独立启动入口，避免改变自用工作台行为。"""
from __future__ import annotations

from importlib import import_module

from fastapi import Request

from . import config
from .public_release import ROOT, description, enabled, install_routes

if not enabled():
    raise RuntimeError('请先安装公开镜像配置')

if (ROOT/'public-web').is_dir():
    config.WEB_DIR = ROOT/'public-web'
server = import_module('app.server')
app = server.app
app.title = '诗影 AI 工作台'
# 图片生成及其旧别名均不进入本次视频/克隆公开版。
app.router.routes[:] = [route for route in app.router.routes
    if getattr(getattr(route, 'endpoint', None), '__module__', '') not in {
        'app.image_studio', 'app.qwen_api', 'app.qwen_keys'}
    and getattr(route, 'path', '') not in {'/api/workbench/bootstrap', '/api/node-info'}]
install_routes(app)


@app.middleware('http')
async def public_scope(request: Request, call_next):
    from fastapi.responses import JSONResponse
    path = request.url.path
    if path.startswith(('/image-studio', '/api/image-studio', '/api/qwen', '/qwen', '/v1/images')):
        return JSONResponse({'detail':'当前公开版提供视频创作和克隆功能'}, status_code=404)
    if path.startswith('/api/workbench/command/') and not path.endswith('/optimize_server_prompt'):
        return JSONResponse({'detail':'请使用实例管理码配置 AI 助手'}, status_code=403)
    if path == '/api/workbench/log':
        return JSONResponse({'detail':'请在实例终端查看日志'}, status_code=403)
    response = await call_next(request)
    if path.startswith(('/setup', '/api/public-release')):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/api/node-info')
def node_info():
    return {'api':'minimax-compute-node', 'version':1, 'capabilities':['h3','clone','works','upscale']}


@app.get('/api/workbench/bootstrap')
def bootstrap(request: Request):
    from .browser_workbench import panel_settings, read_json
    from .prompt_optimizer import load_assistant_providers
    from .upscale_settings import load_upscale_settings
    upscale = load_upscale_settings(ROOT)
    providers = []
    configured=load_assistant_providers()
    if configured:
        model=configured[0]['defaultModel']
        providers = [{'id':'server-managed','name':'节点 AI 助手','defaultModel':model,
                      'models':[model],'baseUrl':str(request.base_url).rstrip('/'),
                      'enabled':True,'serverManaged':True}]
    return {'settings':panel_settings(),'providers':providers,'canManage':False,
            'version':read_json(ROOT/'web/studio/version.json',{}).get('version','0.4.138'),
            'upscale':{'engine':upscale['engine'],'topaz_model':upscale['topaz_model']},
            'publicRelease':description()}


def main():
    server.main()


if __name__ == '__main__':
    main()
