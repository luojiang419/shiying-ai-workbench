"""Repair idle GPU services; leave active or queued user jobs alone."""
import fcntl,json,os,subprocess,time,urllib.request
from pathlib import Path
from gpu_runtime import ROOT,ensure_runtime


def get(path,port):
    return json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}'+path,timeout=5))


def idle():
    try:
        q=get('/queue',8188)
        if q.get('queue_running') or q.get('queue_pending'):return False
    except (OSError,ValueError):
        # A dead backend has no running GPU prompt; API job checks still apply.
        pass
    for path,field in [('/api/jobs','items'),('/api/image-studio/jobs','jobs')]:
        try:
            if any(j.get('status') in ('queued','running','pending','preparing') for j in get(path,7890).get(field,[])):return False
        except (OSError,ValueError):
            # Never restart a live, unresponsive API with unknown job state.
            ctl=['/root/miniconda3/bin/supervisorctl','-c',str(ROOT/'deployment/supervisord.ini'),'status','minimax-api']
            result=subprocess.run(ctl,capture_output=True,text=True,timeout=10)
            if 'RUNNING' in result.stdout:return False
    return True


def check():
    env=dict(os.environ,**json.loads((ROOT/'deployment/node-env.json').read_text()))
    lockpath=ROOT/'outputs/runtime/clone-gpu.lock';lockpath.parent.mkdir(parents=True,exist_ok=True)
    with lockpath.open('a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        if not idle():return
        ctl=['/root/miniconda3/bin/supervisorctl','-c',str(ROOT/'deployment/supervisord.ini')]
        for role,name,port,path in [('comfy','minimax-comfy',8188,'/system_stats'),('api','minimax-api',7890,'/api/status')]:
            before=subprocess.run(ctl+['pid',name],capture_output=True,text=True,timeout=10).stdout.strip()
            result=ensure_runtime(env,role)
            if not result['ready']:
                print('GPU_RUNTIME_UNAVAILABLE',role,result.get('error'),flush=True)
                continue
            healthy=True
            try:get(path,port)
            except (OSError,ValueError):healthy=False
            current_python=''
            if before.isdigit() and int(before)>0:
                try:current_python=Path(f'/proc/{before}/cmdline').read_bytes().split(b'\0')[0].decode()
                except OSError:pass
            # A separate deployment may temporarily own the port. Do not repeatedly
            # spawn a second service into a healthy listener owned outside Supervisor.
            if healthy and (not before.isdigit() or int(before)==0):
                continue
            if not healthy or current_python!=result['python']:
                if not idle():return
                print('GPU_RUNTIME_RECOVER',name,flush=True)
                subprocess.run(ctl+['restart',name],timeout=120,check=False)
                # Recheck job state on the next cycle, after the service has started.
                return


if __name__=='__main__':
    while True:
        try:check()
        except Exception as error:print('GPU_RUNTIME_WATCH_ERROR',str(error),flush=True)
        time.sleep(300)
