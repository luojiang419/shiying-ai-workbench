import json,os,sys
from pathlib import Path
r=Path(__file__).resolve().parents[1]
env=json.loads((r/'deployment/node-env.json').read_text())
os.environ.update(env)
from gpu_runtime import ensure_runtime,gpu_info,comfy_args
role='api' if sys.argv[1]=='api' else 'comfy'
runtime_env=dict(os.environ)
runtime=ensure_runtime(runtime_env,role)
print('GPU_RUNTIME '+json.dumps(runtime,ensure_ascii=False),flush=True)
if not runtime['ready'] and role=='comfy':
 raise RuntimeError(runtime.get('error','GPU runtime unavailable'))
selected_python=runtime['python']
gpu=gpu_info(runtime_env)
cache=r/'outputs/runtime/compiler-cache'/('sm-'+str(gpu.get('compute_cap','unknown')).replace('.','')+'-'+str(runtime.get('cuda','unknown')))
cache.mkdir(parents=True,exist_ok=True)
os.environ.update(CUDA_CACHE_PATH=str(cache/'cuda'),CUDA_CACHE_MAXSIZE=str(256*1024**2),TORCHINDUCTOR_CACHE_DIR=str(cache/'inductor'),TRITON_CACHE_DIR=str(cache/'triton'))
if sys.argv[1]=='api':
 os.chdir(r);os.execv(selected_python,[selected_python,'-m','app.public_server'])
else:
 c=Path(env['MINIMAX_COMFY_DIR']);os.chdir(c)
 os.execv(selected_python,[selected_python,str(c/'main.py'),'--listen','127.0.0.1','--port','8188',*comfy_args(gpu_info(runtime_env)),'--disable-auto-launch','--output-directory',str(c/'output')])
