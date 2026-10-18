"""Bound rebuildable caches. Never recurse through links or delete application data."""
import argparse
import contextlib
import fcntl
import json
import os
import shutil
import stat
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'outputs/runtime/cache-cleanup.json'
POLICY=ROOT/'deployment/cache-policy.json'
MODEL_SUFFIXES={'.safetensors','.gguf','.ckpt','.pt','.pth','.bin','.onnx'}
PROTECTED_DIRS={'models','checkpoints','huggingface','hub','.locks'}


def default_roots():
    return [Path(p) for p in (
        '/root/.cache/pip','/root/.cache/torch/inductor',
        '/root/.cache/torch/torch_extensions','/root/.triton/cache','/root/.nv/ComputeCache',
        '/tmp/torchinductor_root','/root/minimax-comfy/temp',
        str(ROOT/'outputs/runtime/compiler-cache'),
        str(ROOT/'outputs/runtime/gpu-runtimes/tmp'),
        '/root/autodl-tmp/minimax-runtimes/tmp',
    )]


def allocated(s):
    return s.st_blocks*512 if hasattr(s,'st_blocks') else s.st_size


def fingerprint(s):
    return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink


def inventory(roots):
    entries=[];seen=set();skipped_roots=0
    for root in roots:
        root=Path(root)
        if root.is_symlink() or not root.is_dir():continue
        resolved=root.resolve()
        if resolved.is_relative_to(Path('/.autodl-model')):
            skipped_roots+=1;continue
        root_stat=root.stat()
        for directory,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
            for name in files:
                path=Path(directory)/name
                try:s=path.lstat()
                except OSError:continue
                if not stat.S_ISREG(s.st_mode):continue
                inode=(s.st_dev,s.st_ino)
                if inode in seen:continue
                seen.add(inode)
                relative=path.relative_to(root)
                protected=(path.suffix.lower() in MODEL_SUFFIXES or name.lower().endswith('.lock') or name.lower() in {'lock','__lock','.lock'} or any(p in PROTECTED_DIRS for p in relative.parts[:-1]))
                entries.append({'root':root,'relative':relative,'root_inode':(root_stat.st_dev,root_stat.st_ino),
                                'signature':fingerprint(s),'inode':inode,'bytes':allocated(s),'mtime':s.st_mtime,'protected':protected})
    return entries,skipped_roots


def in_use_inodes():
    """Include mmap-backed JIT libraries whose file descriptors are already closed."""
    result=set()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:
            for fd in (process/'fd').iterdir():
                try:
                    s=fd.stat();result.add((s.st_dev,s.st_ino))
                except OSError:pass
            with (process/'maps').open() as source:
                for line in source:
                    parts=line.split(None,5)
                    if len(parts)<5 or parts[4]=='0':continue
                    major,minor=parts[3].split(':')
                    result.add((os.makedev(int(major,16),int(minor,16)),int(parts[4])))
        except (FileNotFoundError,ProcessLookupError):continue
        except PermissionError:
            # An incomplete in-use scan cannot establish that deletion is safe.
            raise RuntimeError('无法完整检查进程正在使用的缓存，本轮暂缓清理')
    return result


def unlink_unchanged(entry):
    """Traverse approved directory descriptors with O_NOFOLLOW at every level."""
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    fd=os.open(entry['root'],flags)
    try:
        s=os.fstat(fd)
        if (s.st_dev,s.st_ino)!=entry['root_inode']:return False
        for part in entry['relative'].parts[:-1]:
            next_fd=os.open(part,flags,dir_fd=fd)
            os.close(fd);fd=next_fd
        name=entry['relative'].name
        s=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if not stat.S_ISREG(s.st_mode) or fingerprint(s)!=entry['signature'] or s.st_nlink>1:return False
        os.unlink(name,dir_fd=fd)
        return True
    finally:os.close(fd)


def prune(roots,threshold,target,min_age,in_use,now=None,dry_run=False):
    entries,skipped=inventory(roots);total=sum(x['bytes'] for x in entries)
    report={'bytes_before':total,'bytes_after':total,'removed_files':0,'removed_bytes':0,
            'protected_files':0,'skipped_changed_or_locked':0,'skipped_roots':skipped,'status':'below_threshold'}
    if total<=threshold:return report
    report['status']='dry_run' if dry_run else 'cleaned'
    now=time.time() if now is None else now
    for entry in sorted(entries,key=lambda x:x['mtime']):
        if report['bytes_after']<=target:break
        if entry['protected'] or entry['signature'][-1]>1 or entry['inode'] in in_use or now-entry['mtime']<min_age:
            report['protected_files']+=1;continue
        try:removed=dry_run or unlink_unchanged(entry)
        except OSError:removed=False
        if not removed:
            report['skipped_changed_or_locked']+=1;continue
        report['bytes_after']-=entry['bytes'];report['removed_bytes']+=entry['bytes'];report['removed_files']+=1
    if not dry_run:
        remaining,_=inventory(roots);report['bytes_after']=sum(x['bytes'] for x in remaining)
        if report['bytes_after']>target:report['status']='limited_by_protected_files'
    return report


def active_installer():
    import psutil
    for process in psutil.process_iter(['cmdline']):
        args=process.info['cmdline'] or []
        if not args:continue
        name=Path(args[0]).name
        if name in {'pip','pip3','uv'} and any(a in args for a in ('install','download','wheel','sync')):return True
        if '-m' in args:
            index=args.index('-m')
            if args[index+1:index+2]==['pip'] and any(a in args for a in ('install','download','wheel')):return True
    return False


def write_state(report,threshold,target):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    try:previous=json.loads(STATE.read_text())
    except (OSError,ValueError):previous={}
    report.update(checked_at=time.time(),threshold_bytes=threshold,target_bytes=target)
    if report.get('removed_files') and not report.get('dry_run'):
        report['last_cleanup']={k:report[k] for k in ('checked_at','removed_files','removed_bytes','bytes_after')}
    elif 'last_cleanup' in previous:report['last_cleanup']=previous['last_cleanup']
    temp=STATE.with_name(STATE.name+'.'+str(os.getpid())+'.tmp');temp.write_text(json.dumps(report,ensure_ascii=False,indent=2));temp.replace(STATE)


def check_once(dry_run=False):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    policy=json.loads(POLICY.read_text())
    threshold=int(policy['threshold_gib']*1024**3);target=int(policy['target_gib']*1024**3)
    if not 0<=target<threshold:raise ValueError('缓存目标必须低于触发阈值')
    roots=default_roots();entries,_=inventory(roots);total=sum(x['bytes'] for x in entries)
    report={'bytes_before':total,'bytes_after':total,'removed_files':0,'removed_bytes':0,'dry_run':dry_run,'status':'below_threshold'}
    if total>threshold:
        try:
            with contextlib.ExitStack() as stack:
                for name in ('cache-cleanup.lock','clone-gpu.lock','gpu-runtime.lock','models-prepare.lock','repair.lock'):
                    lock=stack.enter_context((ROOT/'outputs/runtime'/name).open('a+b'))
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                from runtime_watchdog import idle
                if not idle() or active_installer():report['status']='deferred_busy'
                else:
                    before_free=shutil.disk_usage(ROOT).free
                    report=prune(roots,threshold,target,policy['min_age_seconds'],in_use_inodes(),dry_run=dry_run)
                    report['dry_run']=dry_run
                    report['filesystem_freed_bytes']=max(0,shutil.disk_usage(ROOT).free-before_free)
        except BlockingIOError:report['status']='deferred_locked'
        except RuntimeError as error:report.update(status='deferred_in_use_scan',reason=str(error))
    write_state(report,threshold,target)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--watch',action='store_true');parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    last=None
    while True:
        try:
            result=check_once(args.dry_run)
            signature=(result['status'],bool(result['removed_files']))
            if signature!=last or result['removed_files']:
                print('CACHE_CLEANUP '+json.dumps(result,ensure_ascii=False),flush=True)
            last=signature
        except Exception as error:
            print('CACHE_CLEANUP_ERROR '+str(error),flush=True)
            if not args.watch:raise
        if not args.watch:break
        time.sleep(60)
