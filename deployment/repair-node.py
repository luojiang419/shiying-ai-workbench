"""Idempotent, hash-verified repair of a deployed Linux node."""
import fcntl,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
runtime=ROOT/'outputs/runtime';runtime.mkdir(parents=True,exist_ok=True)
lock=(runtime/'repair.lock').open('a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:sys.exit(0)
results={}
def run(name,cmd):
 p=subprocess.run(cmd,cwd=ROOT)
 results[name]=p.returncode==0
 (runtime/'repair-status.json').write_text(json.dumps({'updated':time.time(),'checks':results},indent=2))
 return p.returncode==0
from gpu_runtime import ensure_runtime,ensure_llama
env=dict(os.environ,**json.loads((ROOT/'deployment/node-env.json').read_text()))
api_runtime=ensure_runtime(env,'api')
comfy_runtime=ensure_runtime(env,'comfy')
results.update(api_runtime=api_runtime['ready'],comfy_runtime=comfy_runtime['ready'])
print('GPU_RUNTIME_REPAIR',json.dumps({'api':api_runtime,'comfy':comfy_runtime}),flush=True)
try:
 print('LLAMA_RUNTIME',json.dumps(ensure_llama(env)),flush=True);results['llama']=True
except Exception as error:
 print('LLAMA_RUNTIME_UNAVAILABLE',str(error),flush=True);results['llama']=False
run('models',[sys.executable,str(ROOT/'deployment/prepare_models.py')])
env=json.loads((ROOT/'deployment/node-env.json').read_text())
comfy=Path(env['MINIMAX_COMFY_DIR'])
for rel in json.loads((ROOT/'deployment/models.json').read_text())['assets']:
 if rel.startswith('comfy/'):
  target=comfy/'models'/rel[6:]; source=Path('/root/ComfyUI/models')/rel[6:]
  target.parent.mkdir(parents=True,exist_ok=True)
  if not target.exists() and not target.is_symlink():target.symlink_to(source)
sys.exit(0 if all(results.values()) else 1)
