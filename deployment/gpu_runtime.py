"""Detect, validate and repair GPU runtimes without mutating a running environment."""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'outputs/runtime/gpu-runtime.json'
PROBE = r'''
import json,sys,importlib
result={'ready':False,'python':sys.executable}
try:
 import torch
 result.update(torch=torch.__version__,cuda=torch.version.cuda,arch=torch.cuda.get_arch_list())
 for name in ('torchvision','torchaudio','safetensors','transformers','einops','numpy','PIL','scipy'):
  importlib.import_module(name)
 for name in (('fastapi','uvicorn','multipart','psutil','websockets','cryptography','cv2','timm','kornia','onnxruntime','spandrel') if sys.argv[1]=='api' else ('aiohttp','yaml','av','torchsde')):
  importlib.import_module(name)
 assert torch.cuda.is_available(),'CUDA unavailable'
 p=torch.cuda.get_device_properties(0)
 cc=torch.cuda.get_device_capability(0)
 result.update(gpu=p.name,capability=list(cc),memory_mib=p.total_memory//1024**2)
 assert cc>=(7,5),'Only RTX 20/30/40/50 and compatible professional GPUs are supported'
 a=torch.randn((64,64),device='cuda',dtype=torch.float32)
 assert torch.isfinite(a@a).all().item(),'Invalid FP32 matmul'
 q=torch.randn((1,2,32,32),device='cuda')
 y=torch.nn.functional.scaled_dot_product_attention(q,q,q)
 assert torch.isfinite(y).all().item(),'Invalid attention'
 if cc>=(8,0):
  b=a.to(torch.bfloat16); assert torch.isfinite(b@b).all().item(),'Invalid BF16 matmul'
 torch.cuda.synchronize()
 result.update(ready=True,bf16=cc>=(8,0))
except Exception as error:
 result['error']=str(error)[-1500:]
print('GPU_RUNTIME_JSON='+json.dumps(result))
'''


def gpu_info(env):
    sys.path.insert(0, str(ROOT))
    os.environ.update(env)
    from app.qwen_node_profile import detect_gpu
    return detect_gpu()


def runtime_plan(gpu):
    sys.path.insert(0, str(ROOT))
    from app.qwen_node_profile import capability
    cc = capability(gpu)
    if cc < 7.5:
        raise RuntimeError('未识别到受支持的 RTX 20/30/40/50 或专业卡；不支持 GTX 10 系列')
    driver = tuple(int(n) for n in gpu.get('driver','0').split('.'))
    if cc >= 10:
        family, minimum = 'cu130', (580,65,6)
    else:
        family, minimum = 'cu126', (560,28,3)
    if driver < minimum:
        raise RuntimeError(f'{family} 需要宿主机 NVIDIA 驱动至少 {".".join(map(str,minimum))}；当前 {gpu.get("driver") or "未知"}，容器不能补装宿主驱动')
    return {'family':family, 'torch':'2.12.1', 'vision':'0.27.1', 'capability':cc}


def probe(python, env, role='comfy'):
    try:
        p = subprocess.run([str(python),'-c',PROBE,role],env=env,capture_output=True,text=True,timeout=90)
        lines=[line for line in p.stdout.splitlines() if line.startswith('GPU_RUNTIME_JSON=')]
        if p.returncode != 0:return {'ready':False,'error':p.stderr[-1500:] or 'Probe process exited unsuccessfully'}
        return json.loads(lines[-1].split('=',1)[1]) if lines else {'ready':False,'error':p.stderr[-1500:] or 'Probe process failed'}
    except (OSError,subprocess.SubprocessError,ValueError) as error:
        return {'ready':False,'error':str(error)}


def save(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    temporary=STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2))
    temporary.replace(STATE)


def storage_root(env, required_bytes=12*1024**3):
    configured=env.get('MINIMAX_RUNTIME_ROOT')
    choices=[Path(configured)] if configured else [Path('/root/autodl-tmp/minimax-runtimes'),ROOT/'outputs/runtime/gpu-runtimes']
    for candidate in choices:
        parent=candidate
        while not parent.exists():parent=parent.parent
        # autodl-tmp can be a normal directory on the 30GB system overlay.
        if not configured and 'autodl-tmp' in str(candidate) and parent.stat().st_dev==ROOT.stat().st_dev:
            continue
        if shutil.disk_usage(parent).free >= required_bytes:
            candidate.mkdir(parents=True,exist_ok=True)
            return candidate
    raise RuntimeError('运行时补齐需要至少 12GiB 可用空间；请挂载数据盘，或设置 MINIMAX_RUNTIME_ROOT。不会在空间不足的系统盘下载 CUDA 包')


def install_runtime(plan, base_python, env, role):
    store=storage_root(env,required_bytes=0)
    tag=f"torch-{plan['torch']}-{plan['family']}-py{sys.version_info.major}{sys.version_info.minor}"
    index=1
    while True:
        target=store/(tag if index==1 else tag+'-'+str(index))
        python=target/'bin/python'
        if python.exists():
            result=probe(python,env,role)
            if result['ready']:return str(python),result
        if not (target/'gpu-runtime-ready.json').exists():break
        index+=1
    # Retry one uncommitted staging environment rather than filling the disk with failed copies.
    if shutil.disk_usage(store).free < 12*1024**3:
        raise RuntimeError('运行时补齐需要至少 12GiB 可用空间；请挂载数据盘或设置 MINIMAX_RUNTIME_ROOT，原运行时保持不变')
    log=ROOT/'logs/gpu-runtime-install.log';log.parent.mkdir(exist_ok=True)
    temporary=store/'tmp';temporary.mkdir(exist_ok=True)
    install_env=dict(env,TMPDIR=str(temporary),PIP_NO_CACHE_DIR='1')
    with log.open('a') as output:
        def run(args):
            subprocess.run(args,env=install_env,stdout=output,stderr=subprocess.STDOUT,check=True,timeout=3600)
        run([base_python,'-m','venv','--system-site-packages',str(target)])
        run([str(python),'-m','pip','install','--no-cache-dir',
             f"torch=={plan['torch']}+{plan['family']}",f"torchvision=={plan['vision']}+{plan['family']}",
             '--index-url','https://download.pytorch.org/whl/'+plan['family']])
        constraints=target/'torch-constraints.txt'
        constraints.write_text(f"torch=={plan['torch']}+{plan['family']}\ntorchvision=={plan['vision']}+{plan['family']}\n")
        reqs=[ROOT/'deployment/native-repair.txt']
        comfy=Path(env['MINIMAX_COMFY_DIR'])/'requirements.txt'
        if comfy.exists():reqs.append(comfy)
        command=[str(python),'-m','pip','install','--no-cache-dir','-c',str(constraints)]
        for path in reqs:command.extend(['-r',str(path)])
        # Only essential dependencies are repaired; optional CUDA extensions stay disabled.
        run(command)
    result=probe(python,env,role)
    if not result['ready']:raise RuntimeError('补齐后的 CUDA 实测未通过：'+result.get('error','未知错误'))
    (target/'gpu-runtime-ready.json').write_text(json.dumps(result,indent=2))
    return str(python),result


def ensure_runtime(env, role, repair=True):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    with (STATE.parent/'gpu-runtime.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:state=json.loads(STATE.read_text())
        except (OSError,ValueError):state={'roles':{}}
        state.update(updated=time.time(),gpu=gpu_info(env))
        current=state.setdefault('roles',{}).get(role,{})
        default=env['MINIMAX_COMFY_PYTHON'] if role=='comfy' else str(ROOT/'venv/bin/python')
        candidates=list(dict.fromkeys([current.get('python',default),default]))
        diagnostics=[]
        for python in candidates:
            result=probe(python,env,role);diagnostics.append(result)
            if result['ready']:
                state['roles'][role]=dict(result,python=python)
                save(state)
                return state['roles'][role]
        try:
            plan=runtime_plan(state['gpu'])
            if not repair:raise RuntimeError('运行时异常，修复模式未启用')
            python,result=install_runtime(plan,env['MINIMAX_COMFY_PYTHON'],env,role)
            state['roles'][role]=dict(result,python=python,repaired=True,family=plan['family'])
        except Exception as error:
            state['roles'][role]={'ready':False,'python':default,'error':str(error),'diagnostics':diagnostics}
        save(state)
        return state['roles'][role]


def comfy_args(gpu):
    sys.path.insert(0,str(ROOT))
    from app.qwen_node_profile import capability
    from app.config import comfy_vram_reserve_gb
    args=['--lowvram','--reserve-vram',str(comfy_vram_reserve_gb())]
    if gpu['memory_mib'] <= 18*1024:
        args+=['--disable-smart-memory','--disable-comfy-compiler','--disable-cuda-graphs','--disable-xformers','--use-split-cross-attention']
    if 7.5 <= capability(gpu) < 8:
        args+=['--force-fp32','--fp32-vae','--fp32-text-enc','--cpu-vae','--disable-dynamic-vram']
        for flag in ['--disable-comfy-compiler','--disable-cuda-graphs','--disable-xformers','--use-split-cross-attention']:
            if flag not in args:args.append(flag)
    return args


def llama_target(gpu, toolkit_major, toolkit_minor=0):
    sys.path.insert(0,str(ROOT))
    from app.qwen_node_profile import capability
    cc=capability(gpu)
    if cc < 7.5:raise RuntimeError('辅助推理不支持 GTX 10 系列')
    driver=tuple(int(n) for n in gpu.get('driver','0').split('.'))
    # A CUDA 13 toolkit cannot produce a CUDA 12 binary for an older host driver.
    if not toolkit_major or (toolkit_major>=13 and driver<(580,65,6)):
        return 'cpu'
    if cc>=10 and (toolkit_major,toolkit_minor)<(12,8):
        return 'cpu'
    return str(round(cc*10))


def ensure_llama(env, repair=True):
    import re
    gpu=gpu_info(env)
    compiler=shutil.which('nvcc') or '/usr/local/cuda/bin/nvcc'
    toolkit=(0,0)
    if Path(compiler).exists():
        text=subprocess.check_output([compiler,'--version'],text=True,timeout=10)
        match=re.search(r'release (\d+)\.(\d+)',text)
        if match:toolkit=tuple(map(int,match.groups()))
    target=llama_target(gpu,*toolkit)
    binary=ROOT/'vendor/clone-llama/llama-server';receipt=binary.parent/'runtime-profile.json'
    try:record=json.loads(receipt.read_text())
    except (OSError,ValueError):record={}
    version_ok=binary.exists() and subprocess.run([str(binary),'--version'],capture_output=True,timeout=30).returncode==0
    legacy=ROOT/'vendor/llama-source/llama.cpp-b11100/build/bin/llama-server'
    if not record and version_ok and target=='120' and binary.resolve()==legacy.resolve():
        record={'target':'120','toolkit_major':13,'adopted_existing_build':True}
        receipt.write_text(json.dumps(record,indent=2))
    if not version_ok or record.get('target')!=target:
        if not repair:raise RuntimeError('辅助推理运行时需按当前显卡重新编译：'+target)
        subprocess.run([env['MINIMAX_COMFY_PYTHON'],str(ROOT/'deployment/build-llama.py')],env=env,check=True,timeout=7200)
    return {'ready':True,'target':target,'toolkit':toolkit,'python_runtime_independent':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check-only',action='store_true');args=parser.parse_args()
    env=dict(os.environ,**json.loads((ROOT/'deployment/node-env.json').read_text()))
    results={role:ensure_runtime(env,role,not args.check_only) for role in ('api','comfy')}
    print(json.dumps(results,ensure_ascii=False,indent=2))
    sys.exit(0 if all(x['ready'] for x in results.values()) else 1)
