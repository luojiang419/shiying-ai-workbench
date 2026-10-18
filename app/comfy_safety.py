"""将已验证的输入限制安装到本地 ComfyUI。"""

import shutil
from pathlib import Path


def install_input_safety(comfy_dir: Path) -> None:
    patches = (
        ("comfy_extras/nodes_string.py", "from minimax_input_safety import safe_format\n",
         "f_string.format(**values)", "safe_format(f_string, values)"),
        ("custom_nodes/ComfyUI-KJNodes/nodes/model_optimization_nodes.py",
         "from minimax_input_safety import load_snapshot\n",
         "snapshot = pickle.load(f)", "snapshot = load_snapshot(f)"),
    )
    changes = []
    for relative, import_line, original, replacement in patches:
        target = comfy_dir / relative
        if not target.exists():
            continue
        text = target.read_text(encoding="utf-8")
        if original in text:
            text = text.replace(original, replacement)
        elif replacement not in text:
            raise RuntimeError(f"无法安装 ComfyUI 输入限制，请检查节点实现：{relative}")
        if import_line not in text:
            text = import_line + text
        changes.append((target, text))

    source = Path(__file__).resolve().parents[1] / "comfy_extensions/input_safety.py"
    helper = comfy_dir / "minimax_input_safety.py"
    if not helper.exists() or helper.read_bytes() != source.read_bytes():
        shutil.copy2(source, helper)
    for target, text in changes:
        if target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8")
