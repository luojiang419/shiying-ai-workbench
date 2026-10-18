"""Reuse shared model files by SHA-256, download only missing exact assets."""
import concurrent.futures,hashlib,json,os,shutil,sys,time,urllib.request,threading,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMFY=Path(os.environ.get('MINIMAX_COMFY_DIR','/root/ComfyUI'))
manifest=json.loads((ROOT/'deployment/models.json').read_text())['assets']
state=ROOT/'outputs/runtime/model-audit.json'
state.parent.mkdir(parents=True,exist_ok=True)
process_lock=(state.parent/'models-prepare.lock').open('a')
fcntl.flock(process_lock,fcntl.LOCK_EX)
if '--qwen-only' in sys.argv:
 manifest={k:v for k,v in manifest.items() if k.startswith('comfy/') and ('qwen-image' in k or 'qwen3vl_8b' in k or 'qwen_image_2.1_' in k)}
 state=state.parent/'qwen-model-audit.json'
cache={}
cache_lock=threading.Lock()
try:cache=json.loads((state.parent/'model-hash-cache.json').read_text())
except (OSError,ValueError):pass
def digest(p):
 s=p.stat();key=str(p.resolve())
 old=cache.get(key,{})
 sig=[s.st_size,s.st_mtime_ns,s.st_ino]
 if old.get('signature')==sig:return old['sha256']
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(16*1024*1024),b''):h.update(b)
 value=h.hexdigest()
 with cache_lock:
  cache[key]={'signature':sig,'sha256':value}
  (state.parent/'model-hash-cache.json').write_text(json.dumps(cache))
 return value
def valid(p,spec):
 return p.is_file() and (not spec.get('size') or p.stat().st_size==spec['size']) and digest(p)==spec['sha256']
print('INDEXING_SHARED_MODELS',flush=True)
sizes={}
for base in ['/.autodl-model/data','/root/ComfyUI/models','/root/autodl-tmp/models']:
 for path,dirs,files in os.walk(base):
  for n in files:
   p=Path(path)/n
   try:
    size=p.stat().st_size
    if any(v.get('size')==size for v in manifest.values()):sizes.setdefault(size,[]).append(str(p))
   except OSError:pass
def fetch(urls,dest,spec):
 dest.parent.mkdir(parents=True,exist_ok=True)
 partial=dest.with_name(dest.name+'.downloading')
 last=None
 for url in urls*2:
  try:
   if shutil.disk_usage(dest.parent).free<spec['size']+1024**3:raise RuntimeError('Insufficient free disk space')
   offset=partial.stat().st_size if partial.exists() else 0
   if offset>=spec['size']:
    if valid(partial,spec):partial.replace(dest);return
    partial.unlink();offset=0
   req=urllib.request.Request(url,headers={'Range':f'bytes={offset}-'} if offset else {})
   with urllib.request.urlopen(req,timeout=90) as response:
    append=offset and response.status==206
    if append and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):raise ValueError('Invalid range response')
    with partial.open('ab' if append else 'wb') as out:shutil.copyfileobj(response,out,16*1024*1024)
   if not valid(partial,spec):raise ValueError('Downloaded asset hash mismatch')
   partial.replace(dest);return
  except Exception as e:last=e
 raise RuntimeError(str(last))
report=[]
for rel,spec in manifest.items():
 target=(COMFY/'models'/rel[6:]) if rel.startswith('comfy/') else ROOT/rel[4:]
 print('CHECK',rel,flush=True)
 try:
  if valid(target,spec) and (not spec.get('public_only') or target.resolve().is_relative_to(Path('/.autodl-model/data'))):source=str(target.resolve());action='existing_verified'
  else:
   candidates=list(dict.fromkeys(spec.get('candidates',[])+sizes.get(spec.get('size'),[])))
   if spec.get('public_only'):
    candidates=[p for p in candidates if Path(p).resolve().is_relative_to(Path('/.autodl-model/data'))]
   match=next((Path(p) for p in candidates if valid(Path(p),spec)),None)
   if match:
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() or target.is_symlink():target.rename(target.with_name(target.name+'.before-minimax'))
    target.symlink_to(match.resolve());source=str(match.resolve());action='shared_link_verified'
   elif spec.get('public_only'):
    source=None;action='missing';print('PUBLIC_MODEL_MISSING: mount AutoDL public library; download disabled',rel,flush=True)
   elif '--audit-only' in sys.argv:
    source=None;action='missing'
   else:
    if spec.get('parts'):
     target.parent.mkdir(parents=True,exist_ok=True)
     tmp=target.with_name(target.name+'.assembling')
     if shutil.disk_usage(target.parent).free<spec['size']+2*1024**3:raise RuntimeError('Insufficient disk space for assembly')
     # Preserve a verified first part from an interrupted older serial assembly.
     first=spec['parts'][0]
     if tmp.exists() and valid(tmp,first):tmp.replace(target.with_name(first['name']))
     def get_part(part):
      pp=target.with_name(part['name'])
      if not valid(pp,part):fetch(part['urls'],pp,part)
      return pp
     with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool,tmp.open('wb') as out:
      pending={i:pool.submit(get_part,p) for i,p in enumerate(spec['parts'][:3])}
      for i,part in enumerate(spec['parts']):
       pp=pending.pop(i).result()
       with pp.open('rb') as f:shutil.copyfileobj(f,out,16*1024*1024)
       pp.unlink();print('ASSEMBLED',i+1,len(spec['parts']),flush=True)
       if i+3<len(spec['parts']):pending[i+3]=pool.submit(get_part,spec['parts'][i+3])
     if not valid(tmp,spec):raise ValueError('Assembled model hash mismatch')
     tmp.replace(target)
    else:fetch(spec['urls'],target,spec)
    source=str(target);action='downloaded_verified'
  row={'target':str(target),'source':source,'action':action,'sha256':spec['sha256']}
 except Exception as e:row={'target':str(target),'action':'error','error':str(e)}
 report.append(row);state.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(row),flush=True)
if any(x['action'] in ('error','missing') for x in report):sys.exit(1)
