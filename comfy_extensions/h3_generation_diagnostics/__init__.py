"""H3 真实阶段诊断与串行任务之间的缓存清理。"""

import functools
import gc
import logging
import time

import comfy.model_management as memory
from comfy_execution.utils import get_executing_context
import nodes
from server import PromptServer


class _StageProxy:
    def __init__(self, target, label, methods):
        self._target = target
        self._label = label
        self._methods = methods
        self._count = 0

    def __getattr__(self, name):
        method = getattr(self._target, name)
        if name not in self._methods:
            return method

        @functools.wraps(method)
        def measured(*args, **kwargs):
            self._count += 1
            stage = f"{self._label} {self._count}"
            context = get_executing_context()
            started = time.monotonic()
            def emit(state):
                if context is None:
                    return
                elapsed = time.monotonic() - started
                logging.info("H3 stage %s: prompt_id=%s node=%s stage=%s elapsed=%.2fs",
                             state, context.prompt_id, context.node_id, stage, elapsed)
                PromptServer.instance.send_sync("h3_stage", {
                    "prompt_id": context.prompt_id, "node": context.node_id,
                    "stage": stage, "state": state, "elapsed_seconds": elapsed,
                }, PromptServer.instance.client_id)
            emit("started")
            try:
                result = method(*args, **kwargs)
            except BaseException:
                emit("failed")
                raise
            emit("completed")
            return result
        return measured


def _instrument(node_class):
    original = node_class.execute
    if getattr(original, "_h3_diagnostics", False):
        return
    import inspect
    signature = inspect.signature(original)

    @functools.wraps(original)
    def execute(cls, *args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        for key, label, methods in (
            ("vae", "参考画面编码", {"encode"}),
            ("audio_vae", "参考音频编码", {"encode"}),
            ("clip", "文本与多模态编码", {"tokenize", "encode_from_tokens_scheduled"}),
        ):
            if key in bound.arguments:
                bound.arguments[key] = _StageProxy(bound.arguments[key], label, methods)
        return original(*bound.args, **bound.kwargs)

    execute._h3_diagnostics = True
    node_class.execute = classmethod(execute)


for _name in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo"):
    # ComfyUI 按文件路径动态加载内置节点；常规 import 会得到另一个类实例。
    _instrument(nodes.NODE_CLASS_MAPPINGS[_name])


class H3ReleaseBackendCache:
    """独立空依赖图使执行器丢弃上一张图的节点缓存，再释放权重。"""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"nonce": ("STRING", {"default": ""})}}

    RETURN_TYPES = ()
    FUNCTION = "release"
    OUTPUT_NODE = True
    CATEGORY = "MiniMax/maintenance"

    def release(self, nonce):
        memory.unload_all_models()
        gc.collect()
        memory.soft_empty_cache()
        logging.info("H3 backend cache release completed: %s", nonce)
        return ()


NODE_CLASS_MAPPINGS = {"H3ReleaseBackendCache": H3ReleaseBackendCache}
