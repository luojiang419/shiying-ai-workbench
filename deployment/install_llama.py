import json,hashlib,urllib.request,tarfile,os,subprocess,shutil
from pathlib import Path
r=Path(__file__).resolve().parents[1]
assets=json.loads((r/'deployment/llama-assets.json').read_text(encoding='utf-8-sig'))
dest=r/'vendor/clone-llama';dest.mkdir(parents=True,exist_ok=True)
cache=r/'outputs/runtime/llama-downloads';cache.mkdir(parents=True,exist_ok=True)
for spec in sorted(assets,key=lambda a:a['name'].startswith('cudart')):
 # Check available system CUDA libraries before downloading an additional runtime.
 if spec['name'].startswith('cudart'):
  binaries=list(dest.rglob('llama-server'))
  if binaries and subprocess.run([str(binaries[0]),'--version'],capture_output=True).returncode==0:continue
 archive=cache/spec['name'];print('DOWNLOAD',spec['name'],flush=True)
 if not archive.exists():
  with urllib.request.urlopen(spec['browser_download_url'],timeout=90) as src,archive.open('wb') as out:shutil.copyfileobj(src,out,16*1024*1024)
 if archive.stat().st_size!=spec['size'] or hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()!=spec['digest'].split(':')[1]:raise ValueError('Official llama release digest mismatch')
 with tarfile.open(archive) as t:t.extractall(dest,filter='data')
 archive.unlink()
binary=next(dest.rglob('llama-server'))
if binary.parent!=dest:
 for p in binary.parent.iterdir():
  link=dest/p.name
  if not link.exists():link.symlink_to(p.relative_to(dest))
print(subprocess.run([str(dest/'llama-server'),'--version'],capture_output=True,text=True).stdout,flush=True)
print(subprocess.run(['ldd',str(binary)],capture_output=True,text=True).stdout,flush=True)
