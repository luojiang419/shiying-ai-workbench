"""按真实采样事件估算剩余耗时，单调时钟用于测量，墙钟用于展示。"""

import math
import time


class SamplingTiming:
    def __init__(self, tail_seconds=None):
        self.history_tail = tail_seconds
        self.started = None
        self.started_at = None
        self.last = None
        self.value = 0
        self.maximum = 0
        self.first_seconds = None
        self.intervals = []
        self.finished_at = None
        self.deadline = None
        self.stage = "sample"

    def start(self):
        if self.started is not None and self.stage == "sample":
            return
        self.__init__(self.history_tail)
        self.started = self.last = time.monotonic()
        self.started_at = time.time()

    def progress(self, value, maximum):
        if not all(math.isfinite(n) and n == int(n) for n in (value, maximum)):
            return False
        if maximum <= 0 or value < 0 or value > maximum:
            return False
        if self.started is None:
            # 未收到采样起点时，不能把首次收到的进度当作完整首步耗时。
            self.start()
            self.value = int(value)
            self.maximum = int(maximum)
            if value == maximum:
                self.finish("decode")
            return True
        if self.maximum and (maximum != self.maximum or value < self.value):
            self.__init__(self.history_tail)
            self.start()
            self.maximum = int(maximum)
            self.value = int(value)
            return True
        self.maximum = int(maximum)
        if value == self.value:
            return False
        now = time.monotonic()
        delta = value - self.value
        seconds = (now - self.last) / delta
        if seconds <= 0:
            return False
        if self.value == 0 and value == 1:
            self.first_seconds = now - self.started
        else:
            self.intervals.append(seconds)
            self.intervals = self.intervals[-4:]
        self.value = int(value)
        self.last = now
        mean = sum(self.intervals) / len(self.intervals) if self.intervals else self.first_seconds
        if mean is not None:
            weight = 1 / self.value if self.first_seconds is not None else 0
            per_step = (self.first_seconds or 0) * weight + mean * (1 - weight)
            self.deadline = time.time() + (maximum - value) * per_step + self.tail_seconds()
        if value == maximum:
            self.finished_at = time.time()
        return True

    def tail_seconds(self):
        if self.history_tail is not None:
            return self.history_tail
        return max(60, self.first_seconds or 0)

    def finish(self, stage):
        if self.started is None:
            return False
        changed = self.stage != stage
        if self.stage == "sample" and stage != "sample":
            self.finished_at = self.finished_at or time.time()
        self.stage = stage
        return changed

    def snapshot(self):
        return {
            "stage": self.stage,
            "sampling_started_at": self.started_at,
            "sampling_finished_at": self.finished_at,
            "completed_steps": self.value,
            "total_steps": self.maximum,
            "first_step_seconds": self.first_seconds,
            "average_step_seconds": sum(self.intervals) / len(self.intervals) if self.intervals else None,
            "estimated_completion_at": self.deadline,
            "tail_seconds": self.tail_seconds(),
            "tail_source": "history" if self.history_tail is not None else "reserve",
        }
