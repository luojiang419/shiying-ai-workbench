"""共享创作界面的浏览器接口及受管理入口保护的主机设置。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.background import BackgroundTask

from . import browser_settings, config
from .prompt_optimizer import load_assistant_providers, optimize_preferred_prompt
from .qwen_keys import check_management_request, public_request
from .upscale_settings import load_upscale_settings

DEFAULTS = {
    'generation_engine':'local', 'cloud_clone_reference_type':'source', 'workbench_reference_model':'fl2va',
    'close_behavior':'tray', 'acceleration_mode':'sage_attention', 'sage_attention_enabled':True,
    'default_steps':12, 'preflight_enabled':True, 'free_gpu_cache_after_task':False,
    'prestart_comfyui':True, 'f11_fullscreen_enabled':True, 'reduce_motion':False,
    'nsfw_enabled':False, 'nsfw_profile':'aftermidnight', 'download_mode':'manual', 'download_directory':'',
    'vram_reserve_gb':2.0,
}

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default



def panel_settings() -> dict:
    data = read_json(config.CONTROL_PANEL_SETTINGS_FILE, {})
    if not isinstance(data, dict):
        raise TypeError('控制面板配置必须是对象')
    # 浏览器连接当前 Web 服务；不继承桌面当前选择的外部计算或云端账号。
    return {**DEFAULTS, **{key:data[key] for key in DEFAULTS if key in data},
            'generation_engine':'local', 'download_mode':'manual', 'download_directory':''}



def install_routes(app, *, root: Path = config.ROOT_DIR, clone_manager=None) -> None:
    router = APIRouter()
    public_edition = read_json(root / 'config/public-release.json', {}).get('edition') == 'video-clone'

    @router.get('/api/workbench/bootstrap')
    def bootstrap(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        if public_request(request):
            raise HTTPException(404,'接口不存在')
        try:
            check_management_request(request)
            can_manage = True
        except HTTPException:
            can_manage = False
        if public_edition:
            can_manage = False
        providers = []
        if can_manage:
            providers = browser_settings.public_providers()
        elif load_assistant_providers():
            providers = [{'id':'server-managed','name':'服务端 AI 助手','defaultModel':'服务端已配置',
                          'models':['服务端已配置'],'baseUrl':str(request.base_url).rstrip('/'),
                          'enabled':True,'serverManaged':True}]
        version = read_json(root / 'web/studio/version.json', {'version':'0.4.131'}).get('version','0.4.131')
        upscale = load_upscale_settings(root)
        return {'settings':panel_settings(),'providers':providers,'canManage':can_manage,'version':version,
                **({'publicRelease':{'enabled':True}} if public_edition else {}),
                'upscale':{'engine':upscale['engine'],'topaz_model':upscale['topaz_model']}}

    @router.post('/api/workbench/command/{command}')
    def command_route(command: str, args: dict, request: Request):
        if public_request(request):
            raise HTTPException(404,'接口不存在')
        try:
            if command == 'optimize_server_prompt':
                return optimize_preferred_prompt(args.get('request',{}))
            check_management_request(request)
            if public_edition:
                from .public_settings import is_owner
                if not is_owner(request, root):
                    raise HTTPException(401, '请先使用实例管理码解锁设置')
            result = browser_settings.execute(command, args, root)
            return panel_settings() if command in browser_settings.SETTING_COMMANDS else result
        except KeyError as error:
            raise HTTPException(404,'接口不存在') from error
        except (ValueError,TypeError,RuntimeError) as error:
            raise HTTPException(400,str(error)) from error

    @router.get('/api/workbench/log',response_class=PlainTextResponse)
    def service_log(request: Request):
        check_management_request(request)
        if public_edition:
            from .public_settings import is_owner
            if not is_owner(request, root):
                raise HTTPException(401, '请先使用实例管理码解锁设置')
        paths = [path for path in config.LOG_DIR.glob('*.log') if path.is_file()]
        if not paths:
            return '暂无服务日志'
        path = max(paths,key=lambda item:item.stat().st_mtime)
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-128*1024))
            return stream.read().decode('utf-8',errors='replace')

    @router.post('/api/workbench/clone/{task_id}/comparison')
    def comparison(task_id: str, args: dict):
        if clone_manager is None:
            raise HTTPException(503,'克隆服务尚未连接')
        if args.get('variant','original') not in {'original','upscaled'}:
            raise HTTPException(400,'对比版本无效')
        from .clone_compare_export import export_comparison
        from .clone_depth_runtime import find_tool
        try:
            job = clone_manager.get(task_id)
            if job['kind'] != 'generate' or job['status'] != 'done':
                raise ValueError('克隆视频尚未完成')
            source = clone_manager.folder(task_id) / 'clip.mp4'
            if not source.is_file() and job['request'].get('depth_id'):
                source = clone_manager.folder(job['request']['depth_id']) / 'clip.mp4'
            result = job.get('result',{})
            url = (result.get('upscaled_video') if args.get('variant')=='upscaled' else None) or result.get('original_video') or result.get('video','')
            if not isinstance(url,str) or not url.startswith('/outputs/'):
                raise ValueError('克隆视频文件不可用')
            generated = (config.WEB_OUTPUT_DIR / url.removeprefix('/outputs/')).resolve()
            if not generated.is_relative_to(config.WEB_OUTPUT_DIR.resolve()):
                raise ValueError('视频路径无效')
            directory = root / 'outputs/runtime/browser-exports'
            directory.mkdir(parents=True,exist_ok=True)
            descriptor,name = tempfile.mkstemp(suffix='.mp4',dir=directory)
            os.close(descriptor)
            destination = Path(name)
            try:
                export_comparison(source,generated,destination,Path(find_tool(root,'ffmpeg') or 'ffmpeg'),Path(find_tool(root,'ffprobe') or 'ffprobe'))
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return FileResponse(destination,media_type='video/mp4',filename=f'clone-comparison-{task_id}.mp4',
                                background=BackgroundTask(destination.unlink,missing_ok=True))
        except (ValueError,KeyError,OSError,RuntimeError) as error:
            raise HTTPException(400,str(error)) from error

    app.include_router(router)
    if public_edition:
        from .public_settings import install_routes as install_public_settings
        install_public_settings(app, root=root)
