"""Build the pinned official release against this node's glibc/CUDA."""
import hashlib,os,subprocess,tarfile,sys,json,re,shutil
from pathlib import Path
r=Path(__file__).resolve().parents[1]
from gpu_runtime import gpu_info,llama_target,storage_root
node_env=json.loads((r/'deployment/node-env.json').read_text())
env=dict(os.environ,**node_env)
nvcc=shutil.which('nvcc') or '/usr/local/cuda/bin/nvcc'
toolkit=0;toolkit_minor=0
if Path(nvcc).exists():
 text=subprocess.check_output([nvcc,'--version'],text=True)
 match=re.search(r'release (\d+)\.(\d+)',text)
 if match:toolkit,toolkit_minor=map(int,match.groups())
gpu=gpu_info(env);target=llama_target(gpu,toolkit,toolkit_minor)
a=r/'deployment/llama-source.tar.gz'
assert hashlib.file_digest(a.open('rb'),'sha256').hexdigest()=='bd1e58d041817cd2a010bd341bcccdd22960d0c51b9c214b6b835c33deb7ac73'
src=r/'vendor/llama-source'
src.mkdir(exist_ok=True)
with tarfile.open(a) as t:t.extractall(src,filter='data')
s=src/'llama.cpp-b11100'
b=storage_root(env,required_bytes=2*1024**3)/('llama-b11100-'+target+'-cuda'+str(toolkit)+'.'+str(toolkit_minor))
env.update(PATH=str(Path(nvcc).parent)+':'+os.environ['PATH'])
if target!='cpu':env['CUDACXX']=nvcc
cuda_args=['-DGGML_CUDA=OFF'] if target=='cpu' else ['-DGGML_CUDA=ON','-DCMAKE_CUDA_ARCHITECTURES='+target]
subprocess.run(['cmake','-S',str(s),'-B',str(b),*cuda_args,'-DGGML_NATIVE=OFF','-DLLAMA_BUILD_TESTS=OFF','-DLLAMA_BUILD_EXAMPLES=OFF','-DLLAMA_BUILD_SERVER=ON','-DLLAMA_CURL=OFF','-DCMAKE_BUILD_TYPE=Release'],env=env,check=True)
subprocess.run(['cmake','--build',str(b),'--config','Release','--target','llama-server','-j','6'],env=env,check=True)
dest=r/'vendor/clone-llama';dest.mkdir(exist_ok=True)
for p in (b/'bin').iterdir():
 if p.is_file() or p.is_symlink():
  d=dest/p.name
  if d.exists() and not d.is_symlink():d.rename(d.with_name(d.name+'.before-gpu-runtime'))
  temporary=d.with_name(d.name+'.new')
  if temporary.is_symlink():temporary.unlink()
  temporary.symlink_to(p);temporary.replace(d)
if target=='cpu':
 for old in dest.glob('libggml-cuda*.so*'):
  if old.is_symlink():old.rename(old.with_name(old.name+'.inactive'))
subprocess.run([str(dest/'llama-server'),'--version'],check=True)
(dest/'runtime-profile.json').write_text(json.dumps({'target':target,'toolkit_major':toolkit,'gpu':gpu,'source_sha256':'bd1e58d041817cd2a010bd341bcccdd22960d0c51b9c214b6b835c33deb7ac73'},indent=2))
