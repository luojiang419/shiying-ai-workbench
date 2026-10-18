"""克隆结构控制依赖部署；权重固定校验，节点源码随项目携带。"""

import shutil
from pathlib import Path

MODEL_NAME = "minimax_h3_fun_controlnet_union_pruned_bf16.safetensors"
MODEL_SIZE = 4222169456
MODEL_SHA256 = "57fe1e64928a63a55e3cd4586b55cd5d0eb4980648b6f31e5d9dac16fe7f1c48"
MODEL_URL = (
    "https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/model_patches/"
    + MODEL_NAME
)


def prepare_control(root: Path):
    from scripts.clone_model_download import download_file
    from .config import COMFY_DIR

    source = root / "third_party/h3-fun-control"
    destination = COMFY_DIR / "custom_nodes/minimax_clone_depth_control"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "nodes.py", "control.py", "LICENSE", "UPSTREAM.md"):
        target = destination / name
        if not target.exists() or target.read_bytes() != (source / name).read_bytes():
            shutil.copy2(source / name, target)
    return download_file(
        [MODEL_URL],
        COMFY_DIR / "models/controlnet" / MODEL_NAME,
        MODEL_SIZE,
        MODEL_SHA256,
    )
