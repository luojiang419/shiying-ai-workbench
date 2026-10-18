"""ComfyUI 文本格式化和 CUDA 内存快照的输入限制。"""

import pickle
import string


class NamedFormatter(string.Formatter):
    def get_field(self, field_name, args, kwargs):
        if not field_name.isidentifier():
            raise ValueError("文本格式化仅支持命名参数，禁止属性和索引访问")
        return kwargs[field_name], field_name


def safe_format(template, values):
    return NamedFormatter().vformat(template, (), values)


class SnapshotUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError("CUDA 内存快照禁止加载全局对象或执行函数")


def load_snapshot(stream):
    return SnapshotUnpickler(stream).load()
