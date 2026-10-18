"""公开镜像只读验收；不上传素材、不提交 GPU 任务。"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.h3_workflow import RESOLUTION_PRESETS

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base',default='http://127.0.0.1:7890')
parser.add_argument('--factory',action='store_true')
args=parser.parse_args()
def get(path):
    with urllib.request.urlopen(args.base.rstrip('/')+path,timeout=20) as response:return json.load(response)
edition=get('/api/public-release')
info=get('/api/node-info')
status=get('/api/status')
assert edition['edition']=='video-clone' and edition['imageStudio'] is False
assert {'h3','clone'}.issubset(info['capabilities']) and 'image-studio' not in info['capabilities']
assert status['ready'],'生成服务尚未就绪，请核对模型挂载及运行日志'
if args.factory:
    assert not edition['assistantConfigured'],'保存公开镜像前请清理个人Key'
    assert not get('/api/clone/tasks')['items'],'保存镜像前请清理验收任务'
    assert not get('/api/job-history?limit=5')['items'],'保存镜像前请清理视频历史'
print(json.dumps({'brand':edition['brand'],'edition':edition['edition'],'backend_ready':True,
    'resolution_presets':len(RESOLUTION_PRESETS),'assistant_configured':edition['assistantConfigured'],
    'read_only_check':True},ensure_ascii=False))
