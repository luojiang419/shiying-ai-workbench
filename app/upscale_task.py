"""按需对已保存的视频做 2× 超分，始终保留原片。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .clone_aux_runtime import prepare_auxiliary
from .config import ROOT_DIR
from .upscale_settings import load_upscale_settings, upscale_output_suffix


def upscale_saved_video(source_name: str, *, root: Path = ROOT_DIR, settings: dict | None = None) -> Path:
    if any(char in source_name for char in "/\\") or Path(source_name).name != source_name or not source_name.lower().endswith(".mp4"):
        raise ValueError("仅支持输出目录中的 MP4 原片")
    source = root / "outputs" / "web" / source_name
    if source.resolve().parent != (root / "outputs/web").resolve():
        raise ValueError("仅支持输出目录中的 MP4 原片")
    if not source.is_file():
        raise FileNotFoundError(f"原片不存在：{source_name}")
    if source.stem.endswith("-2x"):
        raise ValueError("不能重复超分已处理的视频")
    settings = settings or load_upscale_settings(root)
    suffix = upscale_output_suffix(settings)
    target = source.with_name(f"{source.stem}{suffix}.mp4")
    if target.is_file() and target.stat().st_size > 0:
        return target
    temporary = source.with_name(f"{source.stem}{suffix}.partial.mp4")
    temporary.unlink(missing_ok=True)
    try:
        if settings["engine"] == "topaz":
            from .topaz_upscale import run_topaz_upscale
            run_topaz_upscale(source, temporary, settings, emit=lambda percent, message: print(f"{percent}% {message}", flush=True))
        else:
            prepare_auxiliary(root, upscale=True, emit=lambda percent, message: print(f"{percent}% {message}", flush=True))
            from .clone_worker import upscale
            upscale({"source": str(source), "output": str(temporary), "upscale_settings": settings})
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("超分未生成可用视频")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_name", nargs="?")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--path", default=None)
    parser.add_argument("--save-settings")
    args = parser.parse_args()
    if args.inspect or args.save_settings is not None:
        from .upscale_settings import inspect_upscale_settings, save_upscale_settings
        if args.save_settings is not None:
            save_upscale_settings(json.loads(args.save_settings))
        print(json.dumps(inspect_upscale_settings(args.path), ensure_ascii=False), flush=True)
    elif args.source_name:
        result = upscale_saved_video(args.source_name)
        print(json.dumps({"name": result.name, "path": str(result), "output": f"/outputs/{result.name}"}, ensure_ascii=False), flush=True)
    else:
        parser.error("需要原片文件名或 --inspect")


if __name__ == "__main__":
    main()
