"""订阅当前H3任务的真实节点进度，不用计时估算百分比。"""

import json
import math
import threading
import time
from urllib.parse import urlsplit

from websockets.exceptions import ConnectionClosed


class ProgressObserver:
    def __init__(self, url, callback, *, on_timing=None, tail_seconds=None):
        from websockets.sync.client import connect

        self.callback = callback
        from .generation_timing import SamplingTiming
        self.timing = SamplingTiming(tail_seconds)
        self.on_timing = on_timing
        self.prompt_id = None
        self.active = False
        self.stop = threading.Event()
        self._state_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._node = None
        self._stage = "等待节点执行"
        self._progress_key = None
        proxy = {"proxy": None} if urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"} else {}
        self.connection = connect(url, open_timeout=15, max_size=16 * 1024 * 1024, **proxy)
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        while not self.stop.is_set():
            try:
                raw = self.connection.recv(timeout=0.5)
            except TimeoutError:
                continue
            except (ConnectionClosed, OSError):
                break
            if not isinstance(raw, str):
                continue
            try:
                event = json.loads(raw)
                data = event.get("data", {})
                kind = event.get("type")
                if kind == "executing":
                    self.active = data.get("prompt_id") == self.prompt_id
                    if self.active and data.get("node"):
                        self._record(str(data["node"]), "节点执行", ("node", data["node"]))
                        node = str(data["node"])
                        if node == "14":
                            self.timing.start()
                            self._emit_timing()
                        elif node in {"10", "23", "91", "92"}:
                            if self.timing.finish("save" if node in {"91", "92"} else "decode"):
                                self._emit_timing()
                        self.callback(0, "H3正在处理节点 " + str(data["node"]))
                if kind == "h3_stage" and data.get("prompt_id") == self.prompt_id and self.prompt_id:
                    stage = str(data.get("stage", "节点执行"))
                    state = data.get("state")
                    self._record(str(data.get("node")), stage, (stage, state))
                    suffix = "完成" if state == "completed" else "失败" if state == "failed" else "处理中"
                    self.callback(0, f"H3 {stage} · {suffix}")
                if kind == "progress" and (
                    data.get("prompt_id") == self.prompt_id
                    and self.prompt_id
                    or self.active
                    and not data.get("prompt_id")
                ):
                    value = float(data["value"])
                    maximum = float(data["max"])
                    if math.isfinite(value) and math.isfinite(maximum) and maximum > 0 and 0 <= value <= maximum:
                        self._record(self._node, "采样", ("progress", value, maximum))
                        if self._node == "14" and self.timing.progress(value, maximum):
                            self._emit_timing()
                        self.callback(
                            round(100 * value / maximum),
                            "H3当前阶段 " + str(data["value"]) + "/" + str(data["max"]),
                        )
            except (ValueError, KeyError, TypeError):
                continue

    def _emit_timing(self):
        if self.on_timing is not None:
            self.on_timing(self.timing.snapshot())

    def _record(self, node, stage, key):
        with self._state_lock:
            if key != self._progress_key:
                self._last_activity = time.monotonic()
                self._progress_key = key
            self._node = node
            self._stage = stage

    def stalled_stage(self, conditioning_seconds=600, other_seconds=1800):
        with self._state_lock:
            limit = conditioning_seconds if self._node == "104" else other_seconds
            elapsed = time.monotonic() - self._last_activity
            if self._node and elapsed >= limit:
                return f"节点 {self._node} / {self._stage} 连续 {int(elapsed)} 秒没有新进展"
        return None

    def close(self):
        self.stop.set()
        self.connection.close()
        self.thread.join(timeout=2)
