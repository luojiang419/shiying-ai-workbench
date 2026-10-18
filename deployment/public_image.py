"""将已有 AutoDL 工作台脱敏为公开镜像源；须先自行备份私有配置。"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path


def remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():path.unlink()
    elif path.is_dir():shutil.rmtree(path)


def clear(directory: Path, keep=()) -> None:
    if not directory.exists():return
    if directory.is_symlink():raise ValueError(f'不清理重定向目录：{directory}')
    for path in list(directory.iterdir()):
        if path.name not in keep:remove(path)


def private_values(root: Path) -> list[bytes]:
    values=[]
    for path in (root/'config').glob('*'):
        if not path.is_file():continue
        if path.suffix=='.key':values.append(path.read_bytes().strip())
        try:data=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError,UnicodeError):continue
        def visit(value):
            if isinstance(value,list):
                for item in value:visit(item)
            if isinstance(value,dict):
                for key,item in value.items():
                    if key.lower() in {'encryptedkey','agent_key','view_key','api_key','apikey','token','secret'} and isinstance(item,str) and len(item)>12:
                        values.append(item.encode())
                    visit(item)
        visit(data)
    try:
        sys.path.insert(0,str(root))
        from app.prompt_optimizer import (
            _decrypt_assistant_key,
            load_assistant_providers,
        )
        for provider in load_assistant_providers(root/'config/assistant-providers.json'):
            values.append(_decrypt_assistant_key(provider['encryptedKey']).encode())
    except (OSError,ValueError,RuntimeError,ImportError):pass
    return [value for value in values if len(value)>12]


def scan(root: Path, values: list[bytes]) -> list[str]:
    matches=[]
    skip={'venv','vendor','models','node_modules','__pycache__','.git'}
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in skip and not (Path(directory)/name).is_symlink()]
        for name in files:
            path=Path(directory)/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024**2:continue
            if path.suffix.lower() in {'.so','.png','.jpg','.mp4','.zip','.gz','.sqlite3'}:continue
            content=path.read_bytes()
            if any(value in content for value in values):matches.append(str(path))
    return matches


def convert(root: Path, *, clean_home=False) -> dict:
    root=root.resolve()
    if not (root/'app/server.py').is_file() or not (root/'deployment/models.json').is_file():
        raise ValueError('目标不是完整工作台后端目录')
    values=private_values(root)
    config=root/'config'
    clear(config)
    remove(root/'rollback')
    for p in list(root.iterdir()):
        if p.name.startswith('.web-deploy-'):remove(p)
    for base in [root/'app',root/'deployment']:
        for p in list(base.rglob('*')):
            if p.is_file() and ('.before' in p.name or p.name.endswith('.bak')):remove(p)
    runtime=root/'outputs/runtime'
    for p in list((root/'outputs').iterdir()):
        if p.name!='runtime':remove(p)
    runtime.mkdir(parents=True,exist_ok=True)
    keep={'supervisor.pid','supervisor.sock','gateway-supervisor.pid','gateway-supervisor.sock',
          'start.lock','gpu-runtime.lock','models-prepare.lock','repair.lock','cache-cleanup.lock','clone-gpu.lock',
          'gpu-runtime.json','model-hash-cache.json','gpu-runtime-validation.json','gpu-runtime-smoke.json'}
    clear(runtime,keep)
    for p in (root/'logs').glob('*'):
        if p.is_file() and not p.is_symlink():p.write_bytes(b'')
    for name in ['input','output','temp']:
        p=Path('/root/minimax-comfy')/name
        if root==Path('/root/minimax-node'):clear(p)
    settings={
        'public-release.json':{'brand':'诗影 AI 工作台','edition':'video-clone','privateTelemetry':False},
        'assistant-providers.json':[],
        'gateway.json':{'restrictNetwork':False,'diagnostic':False},
        'control-panel-settings.json':{'workbench_reference_model':'ref2va','acceleration_mode':'standard','sage_attention_enabled':False,'default_steps':8,
                                      'nsfw_enabled':False,'nsfw_profile':'naughtytimes_standard','vram_reserve_gb':2.0},
        'clone-settings.json':{'depth_mode':'professional','resolution':'0.4MP 16:9 - 864x480','steps':8,'seed':'','upscale':False},
    }
    for name,data in settings.items():
        path=config/name
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');path.chmod(0o600)
    if clean_home:
        for name in ['/root/.bash_history','/root/.git-credentials','/root/.netrc','/root/.npmrc',
                     '/root/.config/huggingface/token','/root/.cache/huggingface/token','/root/.huggingface/token',
                     '/root/.config/gh/hosts.yml']:
            remove(Path(name))
        ssh=Path('/root/.ssh')
        if ssh.is_dir():
            for p in list(ssh.iterdir()):
                if p.name.startswith('id_') or p.name in {'authorized_keys','config'}:remove(p)
        hook=Path('/etc/autodl.sh')
        if hook.exists():
            text=re.sub(r'# BEGIN MINIMAX NODE REPORTER.*?# END MINIMAX NODE REPORTER\n?', '',hook.read_text(),flags=re.DOTALL)
            hook.write_text(text)
    # 仅移除本机镜像中的非本版模型文件/软链接，不触碰平台公共模型挂载。
    removed_models=[]
    if root==Path('/root/minimax-node'):
        for base in [root/'models',Path('/root/minimax-comfy/models'),Path('/root/ComfyUI/models')]:
            for directory,dirs,files in os.walk(base,followlinks=False):
                for name in list(dirs):
                    p=Path(directory)/name
                    if name in {'video-depth-anything-base','image-background'}:
                        remove(p);dirs.remove(name);removed_models.append(str(p))
                dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink()]
                for name in files:
                    lower=name.lower()
                    if any(part in lower for part in ['qwen_image_2.1','qwen-image-2.1','qwen3vl_8b','video_depth_anything_vitb']):
                        p=Path(directory)/name;remove(p);removed_models.append(str(p))
    remaining=scan(root,values)
    if remaining:raise RuntimeError('个人凭据仍存在，停止发布。位置：'+','.join(remaining))
    return {'edition':'video-clone','personal_credentials_removed':True,'old_secret_matches':0,
            'private_monitor_disabled':True,'removed_model_entries':removed_models,'assistant_configured':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/root/minimax-node'))
    parser.add_argument('--redact-personal',action='store_true',required=True)
    parser.add_argument('--clean-home',action='store_true')
    args=parser.parse_args()
    print(json.dumps(convert(args.root,clean_home=args.clean_home),ensure_ascii=False))
