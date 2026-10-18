"""克隆中心：不可变素材/任务快照、串行模型处理及既有H3队列衔接。"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .clone_aux_runtime import TRACKING_MODEL_NAME, prepare_auxiliary
from .clone_depth_runtime import DEPTH_MODELS, find_tool, prepare_depth
from .config import (
    COMFY_DIR,
    COMFY_INPUT_DIR,
    OUTPUT_DIR,
    REFERENCE_DIFFUSION_MODEL,
    ROOT_DIR,
    WEB_OUTPUT_DIR,
)
from .h3_memory import compatible_resolution, gpu_memory_total_gb
from .h3_workflow import RESOLUTION_PRESETS
from .process_lock import InterProcessLock
from .upscale_settings import load_upscale_settings, upscale_output_suffix
from .video_names import reserve_video_name
from .worker_lifetime import WorkerLifetime

FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
GPU_LOCK = OUTPUT_DIR / "runtime/clone-gpu.lock"
CLONE_ROOT = OUTPUT_DIR / "clone"
FINAL = {"done", "error", "canceled"}
LOGGER = logging.getLogger(__name__)
CLONE_RESOLUTIONS = tuple(RESOLUTION_PRESETS)
DEFAULT_CLONE_SETTINGS = {
    "reference_mode": "extracted_depth",
    "depth_mode": "portrait",
    "depth_model": "small",
    "resolution": "0.2MP 16:9 - 608x352",
    "upscale": True,
    "steps": 20,
    "seed": "",
}
SUPPLEMENT_MAX_LENGTH = 600
CLOUD_REFERENCE_MAX_BYTES = 50 * 1024 * 1024
H3_REFERENCE_IMAGE_MAX_BYTES = 20 * 1024 * 1024
DEPTH_CONTROL_RANGES = {
    "far_point": (0, 99), "near_point": (1, 100), "midtone": (-100, 100),
    "contrast": (0, 300), "brightness": (-100, 100), "smooth": (0, 50),
}
REFERENCE_MODES = {"extracted_depth", "depth_video", "source_video", "region_video", "prompt_video"}
CLONE_MIN_STEPS = 4
CLONE_PIPELINE_VERSION = 2


def validate_depth_controls(value):
    if not isinstance(value, dict) or set(value) - (set(DEPTH_CONTROL_RANGES) | {"invert"}):
        raise ValueError("深度调参格式无效")
    controls = {}
    defaults = {"far_point": 0, "near_point": 100, "midtone": 0,
                "contrast": 100, "brightness": 0, "smooth": 0}
    for name, (minimum, maximum) in DEPTH_CONTROL_RANGES.items():
        raw = value.get(name, defaults[name])
        try:
            number = float(raw) if not isinstance(raw, bool) and isinstance(raw, (int, float)) else float("nan")
        except (OverflowError, ValueError):
            number = float("nan")
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"{name} 必须位于 {minimum}–{maximum}")
        controls[name] = number
    if controls["near_point"] <= controls["far_point"]:
        raise ValueError("近景白点必须大于远景黑点")
    if not isinstance(value.get("invert", False), bool):
        raise ValueError("反相参数必须为布尔值")
    controls["invert"] = value.get("invert", False)
    return controls


def _copy_h3_reference_image(source: Path, destination: Path) -> Path:
    """Keep prepared clone pictures within the H3 reference input limit."""
    if source.stat().st_size <= H3_REFERENCE_IMAGE_MAX_BYTES:
        target = destination.with_suffix(source.suffix)
        shutil.copy2(source, target)
        return target

    from PIL import Image, ImageOps

    target = destination.with_suffix(".jpg")
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        while True:
            image.save(target, format="JPEG", quality=90, optimize=True)
            if target.stat().st_size <= H3_REFERENCE_IMAGE_MAX_BYTES:
                return target
            if max(image.size) <= 1024:
                target.unlink(missing_ok=True)
                raise ValueError("人物参考图过大，无法压缩至 20MB 以内")
            image.thumbnail((max(1024, image.width * 3 // 4), max(1024, image.height * 3 // 4)))


def _prepare_closeup_reference(source: Path, destination: Path) -> Path:
    """从已核验的正面全身参考生成头肩细节图，供源片大近景保持身份。"""
    from PIL import Image, ImageOps

    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        crop = image.crop((0, 0, image.width, max(2, round(image.height * .4))))
        detail = ImageOps.pad(crop, (768, 768), method=Image.Resampling.LANCZOS,
                              color=(245, 245, 245), centering=(.5, .5))
        detail.save(destination, format="PNG", optimize=True)
    return destination


def cloud_reference_clip(clip: Path) -> Path:
    """Cache a bounded H.264 copy of a lossless clone clip for cloud upload."""
    if clip.stat().st_size <= CLOUD_REFERENCE_MAX_BYTES:
        return clip
    output = clip.with_name(f"{clip.stem}-cloud-reference-hq50.mp4")
    if output.is_file() and output.stat().st_mtime_ns >= clip.stat().st_mtime_ns \
            and output.stat().st_size <= CLOUD_REFERENCE_MAX_BYTES:
        return output
    ffmpeg = find_tool(ROOT_DIR, "ffmpeg") or "ffmpeg"
    duration = probe(clip)["duration"]
    scale = "scale=w='if(gte(iw,ih),-2,max(2,trunc(min(720,iw)/2)*2))':h='if(gte(iw,ih),max(2,trunc(min(720,ih)/2)*2),-2)'"
    base = [ffmpeg, "-v", "error", "-y", "-i", str(clip), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", scale, "-r", "24", "-c:v", "libx264", "-preset", "slow", "-pix_fmt", "yuv420p", "-map_metadata", "-1"]
    with tempfile.TemporaryDirectory(prefix="cloud-reference-", dir=clip.parent) as folder:
        temporary = Path(folder) / "output.mp4"
        def run(options):
            result = subprocess.run(
                base + options,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600, creationflags=FLAGS, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip()[-500:] or "ffmpeg 转码失败")
        try:
            run(["-crf", "17", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temporary)])
            if temporary.stat().st_size > CLOUD_REFERENCE_MAX_BYTES:
                total_rate = int((CLOUD_REFERENCE_MAX_BYTES - 64 * 1024) * 8 / duration * .97)
                audio_rate = 64000 if total_rate < 500000 else 192000
                bitrate = total_rate - audio_rate
                if bitrate < 100000:
                    raise RuntimeError("剩余容量不足以保留有效的视频质量，请减少其他参考素材")
                log = str(Path(folder) / "encode-pass")
                for _ in range(3):
                    run(["-b:v", str(bitrate), "-pass", "1", "-passlogfile", log, "-an", "-f", "null", os.devnull])
                    run(["-b:v", str(bitrate), "-pass", "2", "-passlogfile", log, "-c:a", "aac", "-b:a", str(audio_rate), "-movflags", "+faststart", str(temporary)])
                    size = temporary.stat().st_size
                    if size <= CLOUD_REFERENCE_MAX_BYTES:
                        break
                    bitrate = int(bitrate * CLOUD_REFERENCE_MAX_BYTES / size * .96)
            if not 0 < temporary.stat().st_size <= CLOUD_REFERENCE_MAX_BYTES:
                raise RuntimeError("云端参考视频无法压缩到 50 MiB 以内，请缩短片段")
            temporary.replace(output)
            return output
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"云端参考视频转码失败：{error}") from error


def asset_reference_clip(manager, asset_id, start, end, cloud=False):
    """Create a reusable 24 FPS clip for cloud/direct-reference submission."""
    asset = manager.asset(asset_id, "video")
    start, end = validate_interval(start, end, asset["duration"])
    key = f"reference-{round(start * 1000)}-{round(end * 1000)}.mp4"
    output = manager.folder(asset_id) / key
    source = manager.folder(asset_id) / asset["file"]
    if not output.is_file() or output.stat().st_mtime_ns < source.stat().st_mtime_ns:
        temporary = output.with_suffix(f".{uuid4().hex}.partial.mp4")
        ffmpeg = find_tool(ROOT_DIR, "ffmpeg") or "ffmpeg"
        count = math.floor((end - start) * 24 + 1e-6)
        try:
            result = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-ss", str(start), "-i", str(source),
                 "-vf", "fps=24", "-frames:v", str(count), "-an", "-c:v", "libx264",
                 "-crf", "0", str(temporary)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600, creationflags=FLAGS,
            )
            if result.returncode != 0 or not temporary.is_file():
                raise RuntimeError(result.stderr.strip()[-500:] or "参考视频裁切失败")
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    return cloud_reference_clip(output) if cloud else output


def _preset_aspect(name):
    """Use the H3 preset's named aspect, not its alignment-adjusted pixel ratio."""
    match = re.search(r"\b(\d+):(\d+)\b", name)
    if match:
        width, height = map(int, match.groups())
        return f"{width}:{height}", width / height
    if name.startswith("Square"):
        return "1:1", 1.0
    width, height = RESOLUTION_PRESETS[name]
    return name, width / height


def _closest_clone_resolution(width, height, preferred):
    """Match the nearest supported aspect while keeping the preferred pixel tier."""
    if width <= 0 or height <= 0:
        return preferred, None
    source_aspect = width / height
    aspects = dict(_preset_aspect(name) for name in RESOLUTION_PRESETS)
    label = min(aspects, key=lambda name: abs(math.log(source_aspect / aspects[name])))
    preferred_area = math.prod(RESOLUTION_PRESETS[preferred])
    candidates = [
        name for name in RESOLUTION_PRESETS if _preset_aspect(name)[0] == label
    ]
    selected = min(
        candidates,
        key=lambda name: abs(math.log(math.prod(RESOLUTION_PRESETS[name]) / preferred_area)),
    )
    return selected, label


def _gpu_memory_total_gb():
    return gpu_memory_total_gb()


def _compatible_clone_resolution(resolution, duration, gpu_memory_gb):
    return compatible_resolution(resolution, duration, gpu_memory_gb, verified_depth=True)


def probe(path):
    try:
        result = subprocess.run(
            [
                find_tool(ROOT_DIR, "ffprobe") or "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                # 老版 Linux ffprobe 不一定识别 stream_side_data 选择器；
                # 完整流信息同样包含旋转元数据，兼容节点已有 FFmpeg。
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=FLAGS,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "ffprobe 未返回具体原因").strip()[-1000:]
        raise ValueError(f"无法读取参考视频：{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("读取参考视频信息超时，请检查视频文件后重试") from exc
    data = json.loads(result.stdout)
    s = data["streams"][0]
    a, b = s["avg_frame_rate"].split("/")
    rotation = s.get("tags", {}).get("rotate", 0)
    for item in s.get("side_data_list", []):
        if "rotation" in item:
            rotation = item["rotation"]
    width, height = int(s["width"]), int(s["height"])
    display_rotation = round(float(rotation))
    if display_rotation % 180 == 90:
        width, height = height, width
    return {
        "width": width,
        "height": height,
        "rotation": display_rotation,
        "fps": float(a) / float(b),
        "duration": float(data["format"]["duration"]),
    }


def validate_interval(start, end, duration):
    start, end = float(start), float(end)
    if (
        not all(math.isfinite(x) for x in (start, end, duration))
        or start < 0
        or end > duration + 0.02
        or not 2 <= end - start <= 15
    ):
        raise ValueError("请选择源视频内连续2–15秒的有效区间")
    return start, end


def validate_supplement(value, people):
    """Normalize browser input into a task-bound, unambiguous supplement request."""
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("补充需求格式无效")  # noqa: TRY004 - API统一将参数错误映射为400
    if not isinstance(value.get("text", ""), str):
        raise ValueError("补充需求必须是文本")  # noqa: TRY004 - 保持API错误合同
    text = re.sub(r"[\r\n\t]+", " ", value.get("text", "")).strip()
    if not text:
        return None
    if len(text) > SUPPLEMENT_MAX_LENGTH or re.search(r"[\x00-\x1f<>]", text):
        raise ValueError("补充需求应为1–600个普通字符，且不能包含素材标签")
    target = value.get("target") or {"kind": "all"}
    if not isinstance(target, dict) or target.get("kind") not in {"all", "subject"}:
        raise ValueError("补充需求的作用对象无效")
    if target["kind"] == "all":
        return {"text": text, "target": {"kind": "all"}}
    try:
        if type(target["subject_index"]) is not int or type(target["track_id"]) is not int:
            raise ValueError("人物编号必须为整数")
        index, track = target["subject_index"], target["track_id"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("补充需求的人物对象无效") from exc
    if not 1 <= index <= len(people) or int(people[index - 1].get("track_id", index)) != track:
        raise ValueError("补充需求的人物对象与当前槽位不匹配")
    return {"text": text, "target": {"kind": "subject", "subject_index": index, "track_id": track}}


def validate_supplement_plan(raw, supplement, people):
    from .clone_supplement import validate_plan

    return validate_plan(raw, supplement, people)


def validate_picture_references(prompt, image_count):
    """Reject compiled prompts whose picture tags cannot map to submitted images."""
    numbers = {int(value) for value in re.findall(r"<Picture\s+(\d+)>", str(prompt or ""), re.I)}
    invalid = sorted(number for number in numbers if number < 1 or number > image_count)
    if invalid:
        raise ValueError(
            f"提示词引用了未提交的 <Picture {invalid[0]}>；当前仅有 {image_count} 张人物参考图"
        )


def validate_video_references(prompt, video_count):
    """Reject prompts whose semantic video tags would point at control-only media."""
    numbers = {int(value) for value in re.findall(r"<Video\s+(\d+)>", str(prompt or ""), re.I)}
    invalid = sorted(number for number in numbers if number < 1 or number > video_count)
    if invalid:
        raise ValueError(
            f"提示词引用了未提交的 <Video {invalid[0]}>；当前模式有 {video_count} 个语义参考视频，"
            "深度控制视频不能作为 <Video N> 引用"
        )


def prompt_for(
    people, duration, depth_mode, observations=None,
    scene_description="", motion_observations=None, reference_mode="extracted_depth",
):
    from .clone_prompt_compiler import compile_prompt

    return compile_prompt(
        people, duration, depth_mode, observations, scene_description, motion_observations,
        reference_mode=reference_mode,
    )["prompt"]


class CloneManager:
    def __init__(self, jobs, archive, root=CLONE_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path = (
            ROOT_DIR / "config/clone-settings.json"
            if self.root == CLONE_ROOT
            else self.root / "settings.json"
        )
        legacy_settings = self.root / "settings.json"
        if not self.settings_path.exists() and legacy_settings.is_file():
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_settings, self.settings_path)
        self.jobs = jobs
        self.archive = archive
        self.lock = threading.RLock()
        self.tasks = {}
        self.in_flight = set()
        self.pending_cleanup = set()
        self.paused = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clone")
        for path in self.root.glob("*/task.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if item["status"] not in FINAL:
                    item.update(
                        status="error", message="服务重启，未完成任务已停止；可重新提交"
                    )
                    path.write_text(
                        json.dumps(item, ensure_ascii=False), encoding="utf-8"
                    )
                self.tasks[item["id"]] = item
            except (ValueError, KeyError, OSError):
                continue

    def folder(self, id):
        if not re.fullmatch(r"[a-f0-9]{32}", str(id)):
            raise ValueError("无效素材或任务编号")
        return self.root / id

    def asset(self, id, kind=None):
        path = self.folder(id) / "asset.json"
        if not path.is_file():
            raise ValueError("素材不存在，请重新上传")
        data = json.loads(path.read_text(encoding="utf-8"))
        if kind and data["kind"] != kind:
            raise ValueError("素材类型不匹配")
        return data

    def get(self, id):
        with self.lock:
            if id not in self.tasks:
                raise ValueError("任务不存在")
            return copy.deepcopy(self.tasks[id])

    def update(self, id, **data):
        with self.lock:
            item = self.tasks[id]
            if item["status"] == "canceled" and data.get("status") != "canceled":
                return
            item.update(data, updated_at=time.time())
            target = self.folder(id) / "task.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(
                json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp.replace(target)

    def resolve_reference(self, payload):
        """校验并规范化复刻视频来源，同时兼容未带模式的历史任务。"""
        mode = payload.get("reference_mode", "extracted_depth")
        if mode not in REFERENCE_MODES:
            raise ValueError("视频参考方式无效")
        payload["reference_mode"] = mode
        if mode == "region_video":
            region = self.get(payload.get("region_id"))
            if region["kind"] != "region" or region["status"] != "done":
                raise ValueError("演员遮罩尚未准备完成")
            if payload.get("region_confirmed") is not True:
                raise ValueError("请预览并确认当前演员遮罩")
            if region["result"].get("missing_frames", 0):
                raise ValueError("演员遮罩有缺失帧，请修正范围或使用范围补全后重新准备")
            request = region["request"]
            for key in ("source_id", "start", "end"):
                if key in payload and payload[key] != request[key]:
                    raise ValueError("演员遮罩与当前视频区间不匹配，请重新准备")
                payload[key] = request[key]
            return {"mode": mode, "depth": None, "region": region,
                    "duration": region["result"]["duration"], "depth_mode": "portrait",
                    **{key: request[key] for key in ("source_id", "start", "end")}}
        if mode == "extracted_depth":
            depth = self.get(payload.get("depth_id"))
            if depth["kind"] != "depth" or depth["status"] != "done":
                raise ValueError("深度视频尚未准备完成")
            depth_request = depth.get("request", {})
            return {
                "mode": mode,
                "depth": depth,
                "duration": float(depth["result"]["duration"]),
                "depth_mode": depth_request.get("depth_mode", "professional"),
                "source_id": depth_request.get("source_id"),
                "start": depth_request.get("start"),
                "end": depth_request.get("end"),
            }

        asset = self.asset(payload.get("source_id"), "video")
        start, end = validate_interval(
            payload.get("start"), payload.get("end"), asset["duration"]
        )
        depth_mode = payload.get("depth_mode", "professional")
        if depth_mode not in ("portrait", "professional"):
            raise ValueError("深度模式无效")
        payload.update(start=start, end=end, depth_mode=depth_mode)
        payload.pop("depth_id", None)
        return {
            "mode": mode,
            "depth": None,
            "duration": end - start,
            "depth_mode": depth_mode,
            "source_id": asset["id"],
            "start": start,
            "end": end,
        }

    def trim_reference_clip(self, task_id, payload, destination, env=None):
        context = self.resolve_reference(payload)
        if context["mode"] == "extracted_depth":
            source = self.folder(payload["depth_id"]) / "clip.mp4"
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            return context
        asset = self.asset(context["source_id"], "video")
        source = self.folder(asset["id"]) / asset["file"]
        count = math.floor(context["duration"] * 24 + 1e-6)
        ffmpeg = find_tool(ROOT_DIR, "ffmpeg") or "ffmpeg"
        self.run_command(task_id, [
            ffmpeg, "-v", "error", "-y", "-ss", str(context["start"]),
            "-i", str(source), "-vf", "fps=24", "-frames:v", str(count),
            "-an", "-c:v", "libx264", "-crf", "0", str(destination),
        ], env)
        return context

    def canceled(self, id):
        return self.get(id)["status"] == "canceled"

    def submit(self, kind, payload):
        payload = copy.deepcopy(payload)
        if kind == "generate-prepared":
            kind = "generate"
            payload["prepare_prompt"] = True
        if payload.get("prepare_prompt") and payload.get("input_mode") != "materials":
            raise ValueError("后台提示词准备仅用于统一素材克隆")
        if kind == "preserve":
            raise ValueError("对象蒙板合成已停用，请从历史任务选择「重新生成」使用无蒙板流程")
        snapshot = None
        if kind == "generate" and payload.get("retry_of"):
            retry = payload
            original = self.get(payload["retry_of"])
            if original["kind"] != "generate" or original["status"] in {"queued", "running"}:
                raise ValueError("仅已结束的复刻任务可以再次入队")
            payload = copy.deepcopy(original["request"])
            # Retry the immutable prompt/material snapshot, but allow generation-only
            # controls to be lowered after OOM without rebuilding Qwen references.
            for key in ("resolution", "steps", "seed", "upscale"):
                if key in retry:
                    payload[key] = retry[key]
            if payload.get("compiled_snapshot"):
                snapshot = copy.deepcopy(payload)
            elif payload.get("base_prompt"):
                payload["prompt"] = payload["base_prompt"]
        payload.pop("_reuse_prompt", None)
        payload.pop("compiled_snapshot", None)
        payload.pop("base_prompt", None)
        if kind == "region":
            from .clone_region import validate_regions

            source = self.asset(payload.get("source_id"), "video")
            start, end = validate_interval(payload.get("start"), payload.get("end"), source["duration"])
            payload.update(start=start, end=end, regions=validate_regions(payload.get("regions"), end - start))
        elif kind == "depth":
            if payload.get("adjusted_from"):
                original = self.get(payload["adjusted_from"])
                if original["kind"] != "depth" or original["status"] != "done":
                    raise ValueError("请先完成深度视频提取")
                controls = validate_depth_controls(payload.get("controls"))
                raw_id = original["request"].get("raw_depth_id", original["id"])
                if not (self.folder(raw_id) / "depth/depth-relative-normalized.npz").is_file():
                    raise ValueError("原始深度数据已不存在，请重新提取")
                payload = {**original["request"], "adjusted_from": original["id"],
                           "raw_depth_id": raw_id, "controls": controls}
            a = self.asset(payload.get("source_id"), "video")
            validate_interval(payload.get("start"), payload.get("end"), a["duration"])
            if payload.get("depth_mode") not in ("portrait", "professional"):
                raise ValueError("深度模式无效")
            payload.setdefault("depth_model", "small")
            if payload["depth_model"] not in DEPTH_MODELS:
                raise ValueError("深度模型无效")
            if not isinstance(payload.get("remove_background", False), bool):
                raise ValueError("人物遮罩开关必须为布尔值")
            # 人像复刻是生成意图，不等于强制对深度图进行人物抠像。
            payload["remove_background"] = payload[
                "depth_mode"
            ] == "portrait" and payload.get("remove_background", False)
            if payload["remove_background"]:
                # 调参复用已保存的掩码；旧任务没有档位字段时，它实际使用 lite。
                payload["person_mask_model"] = (
                    payload.get("person_mask_model", "lite") if payload.get("adjusted_from") else "quality"
                )
            else:
                payload.pop("person_mask_model", None)
        elif kind == "analyze":
            if self.resolve_reference(payload)["mode"] == "prompt_video":
                raise ValueError("纯提示词轻量克隆无需人数解析，请手工配置人物槽位与起始位置")
        elif kind == "generate":
            if payload.get("preservation") or payload.get("preservation_settings"):
                raise ValueError("旧任务包含蒙板合成设置，请选择「重新生成」重新编译无蒙板提示词")
            self.validate_generation(payload)
            if snapshot:
                payload["supplement_plan"] = snapshot.get("supplement_plan")
                payload["_reuse_prompt"] = True
                payload["compiled_snapshot"] = True
        else:
            raise ValueError("不支持的任务")
        id = uuid4().hex
        self.folder(id).mkdir()
        with self.lock:
            self.tasks[id] = {
                "id": id,
                "kind": kind,
                "status": "queued",
                "percent": 0,
                "message": "等待执行",
                "created_at": time.time(),
                "request": payload,
            }
        self.update(id)
        with self.lock:
            self.in_flight.add(id)
        try:
            self.executor.submit(self.run, id)
        except RuntimeError:
            with self.lock:
                self.in_flight.discard(id)
            raise
        return self.get(id)

    def validate_generation(self, p):
        if p.get("input_mode") == "materials":
            from .clone_materials import validate_material_request

            manifest = validate_material_request(self, p)
            from .clone_edit_contract import (
                replacement_contract,
                replacement_prompt_issues,
            )

            issues = replacement_prompt_issues(p["prompt"], replacement_contract(p))
            if issues:
                raise ValueError("；".join(issues) + "；请编辑原始需求后重新生成")
            context = self.resolve_reference(p)
            for key in ("people", "analysis_id", "supplement", "supplement_plan", "region_id"):
                p.pop(key, None)
            p["auto_prompt"] = False
            p["user_prompt"] = p.get("user_prompt", p["prompt"])
            p["material_manifest"] = manifest
            p["input_contract"] = {"identity": "user_assigned_references", "motion": "prompt_video_reference",
                                   "source_rgb_to_generator": True, "fidelity": "approximate", "auxiliary_models": False}
            self._validate_generation_settings(p, context)
            return
        self._validate_legacy_generation(p)

    def _validate_legacy_generation(self, p):
        context = self.resolve_reference(p)
        lightweight = context["mode"] == "prompt_video"
        if lightweight:
            p.pop("analysis_id", None)
            p.pop("region_id", None)
        goal = p.get("generation_goal", "reference" if context["mode"] == "source_video" else "replace")
        if goal not in {"replace", "reference"}:
            raise ValueError("克隆目标无效")
        if goal == "replace" and context["mode"] == "source_video":
            raise ValueError("人物替换需要深度结构控制，请选择自动深度复刻；原视频模式仅适合自由参考")
        p["generation_goal"] = goal
        # 实际输入决定能力快照，忽略客户端伪造的能力说明。
        p["input_contract"] = {
            "identity": "assigned_pictures",
            "motion": "semantic_reference" if context["mode"] == "source_video" else "depth_control",
            "source_rgb_to_generator": context["mode"] == "source_video",
            "fidelity": "approximate" if context["mode"] == "source_video" else "structure_guided",
        }
        if context["mode"] == "region_video":
            p["input_contract"].update(motion="region_source_reference", source_rgb_to_generator=True,
                                       fidelity="region_guided", unselected_shots="original_frames")
            if len(p.get("people", [])) != 1:
                raise ValueError("演员区域替换当前仅支持一个目标人物")
            if p.get("supplement"):
                raise ValueError("演员区域替换请通过人物参考图配置外观，暂不支持补充改衣需求")
        if lightweight:
            p["input_contract"].update(
                motion="prompt_video_reference", source_rgb_to_generator=True,
                fidelity="approximate", auxiliary_models=False,
                unselected_shots="semantic_preservation",
            )
        d = context["depth"]
        p.pop("requested_resolution", None)
        p.pop("resolution_adjustment", None)
        people = p.get("people")
        if not isinstance(people, list) or not 1 <= len(people) <= 9:
            raise ValueError("请配置1–9位人物")
        if sum(len(x.get("images", [])) for x in people) > 9:
            raise ValueError("H3最多支持9张参考图片")
        tracks = []
        for i, person in enumerate(people, 1):
            if person.get("position") not in (
                "left",
                "center",
                "right",
            ) or not person.get("images"):
                raise ValueError("每个人物需要空间位置和至少1张参考图")
            for aid in person["images"]:
                self.asset(aid, "image")
            tracks.append(int(person.get("track_id", i)))
        if len(set(tracks)) != len(tracks):
            raise ValueError("人物轨迹编号不能重复")
        observations = []
        if p.get("analysis_id"):
            analysis = self.get(p["analysis_id"])
            analyzed_request = analysis["request"] if analysis["kind"] == "analyze" else {}
            analyzed_mode = analyzed_request.get("reference_mode", "extracted_depth")
            same_clip = analyzed_mode == context["mode"]
            if same_clip and context["mode"] == "extracted_depth":
                analyzed_depth_id = analyzed_request.get("depth_id")
                same_clip = analyzed_depth_id == p.get("depth_id")
                if analyzed_depth_id and not same_clip:
                    try:
                        analyzed_depth = self.get(analyzed_depth_id)
                    except ValueError:
                        analyzed_depth = None
                    same_clip = analyzed_depth is not None and d["request"].get("source_id") is not None and all(
                        analyzed_depth["request"].get(key) == d["request"].get(key)
                        for key in ("source_id", "start", "end")
                    )
            elif same_clip:
                same_clip = all(
                    analyzed_request.get(key) == p.get(key)
                    for key in ("source_id", "start", "end")
                )
            if (
                analysis["status"] != "done"
                or analysis["kind"] != "analyze"
                or not same_clip
            ):
                raise ValueError("人物解析与当前区间不匹配，请重新解析")
            observations = analysis["result"]["frames"]
        elif len(people) > 1 and not lightweight:
            raise ValueError("多人复刻请先解析视频人数")
        p.pop("supplement_plan", None)
        p["auto_prompt"] = p.get("auto_prompt", not bool(p.get("prompt")))
        supplement = validate_supplement(p.get("supplement"), people)
        if supplement:
            p["supplement"] = supplement
        else:
            p.pop("supplement", None)
        p["prompt"] = str(
            p.get("prompt")
            or prompt_for(
                people,
                context["duration"],
                context["depth_mode"],
                observations,
                reference_mode="source_video" if context["mode"] == "region_video" else context["mode"],
            )
        ).strip()
        if supplement and not p["auto_prompt"]:
            from .clone_supplement import split_prompt
            split_prompt(p["prompt"])
        if len(p["prompt"]) > 30000:
            raise ValueError("提示词过长")
        self._validate_generation_settings(p, context)

    def _validate_generation_settings(self, p, context):
        from .h3_workflow import sanitize_request

        raw_steps = p.get("steps", DEFAULT_CLONE_SETTINGS["steps"])
        if isinstance(raw_steps, bool):
            raise ValueError("采样步数无效")
        try:
            requested_steps = int(raw_steps)
        except (TypeError, ValueError) as error:
            raise ValueError("采样步数无效") from error
        p["steps"] = requested_steps
        p.pop("requested_steps", None)
        p.pop("quality_adjustment", None)
        request = sanitize_request(
            {
                "mode": "references",
                "prompt": p["prompt"],
                "duration": context["duration"],
                "resolution": p.get("resolution"),
                "steps": p.get("steps", 12),
                "seed": p.get("seed"),
                "reference_images": ["validation.png"] if p.get("input_mode") != "materials" or p.get("reference_images") else [],
                "reference_videos": ["validation.mp4"] if p.get("input_mode") == "materials" else [],
                "control_strength": p.get("control_strength", 1.0),
            }
        )
        matched_resolution = request.resolution
        matched_aspect = None
        if context["source_id"]:
            source = self.asset(context["source_id"], "video")
            if "rotation" not in source:
                source_path = self.folder(source["id"]) / source["file"]
                if source_path.is_file():
                    try:
                        source = {**source, **probe(source_path)}
                    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError, subprocess.SubprocessError):
                        pass
            matched_resolution, matched_aspect = _closest_clone_resolution(
                source.get("width", 0), source.get("height", 0), request.resolution
            )
        # Ref2VA source-video references still scale temporal attention memory with
        # resolution and clip length. Keep every clone mode inside the same
        # verified 16 GB budget, even when ControlNet itself is disabled.
        compatible_resolution = _compatible_clone_resolution(
            matched_resolution, request.duration, _gpu_memory_total_gb()
        )
        p["resolution"] = compatible_resolution
        if compatible_resolution != request.resolution:
            p["requested_resolution"] = request.resolution
            adjustments = []
            if matched_resolution != request.resolution:
                adjustments.append(
                    f"源视频最接近 H3 {matched_aspect} 画幅，已从 {request.resolution} 调整为 {matched_resolution}"
                )
            if compatible_resolution != matched_resolution:
                adjustments.append(
                    f"检测到 {request.duration:.1f} 秒复刻在 16GB 显存上的采样峰值风险，"
                    f"已调整为 {compatible_resolution}，保留原始时长"
                )
            p["resolution_adjustment"] = "；".join(adjustments)

    def run_command(self, id, command, env=None):
        lines = queue.Queue()
        tail = []
        result = None
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=FLAGS,
        )

        def read():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + 3600
        lifetime = None
        try:
            if process.poll() is None:
                try:
                    lifetime = WorkerLifetime(process.pid)
                except OSError:
                    if process.poll() is None:
                        raise
            while True:
                if self.canceled(id):
                    raise RuntimeError("任务已取消")
                if time.monotonic() > deadline:
                    raise TimeoutError("处理超时")
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                tail.append(line)
                tail = tail[-30:]
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "progress":
                    self.update(id, percent=event["percent"], message=event["message"])
                if event.get("type") == "result":
                    result = event["result"]
                if event.get("type") == "error":
                    raise RuntimeError(event.get("error", "模型处理失败"))
            if process.wait(timeout=10) != 0:
                raise RuntimeError("模型处理失败：" + "".join(tail)[-2200:])
            return result
        finally:
            if lifetime is not None:
                lifetime.close()
            if process.poll() is None:
                import psutil

                try:
                    descendants = psutil.Process(process.pid).children(recursive=True)
                    for child in reversed(descendants):
                        try:
                            child.kill()
                        except psutil.Error:
                            pass
                except psutil.Error:
                    pass
                process.kill()
                process.wait(timeout=15)

    def gpu_work(self, id, callback):
        self.update(id, message="等待其他模型任务释放显存")
        with InterProcessLock(GPU_LOCK, 7200):
            if self.canceled(id):
                return None
            if self.jobs._comfy.probe()["ready"]:
                self.jobs._comfy.free_memory()
            return callback()

    def run(self, id):
        try:
            self._run(id)
        finally:
            with self.lock:
                self.in_flight.discard(id)
                if id in self.pending_cleanup:
                    self.pending_cleanup.discard(id)
                    if id in self.tasks and self._cleanup_reason(self.tasks[id]):
                        try:
                            self._archive_task_locked(id)
                        except OSError:
                            LOGGER.exception("克隆任务 %s 延迟清理失败", id)

    def _run(self, id):
        while self.paused and not self.canceled(id):
            time.sleep(0.3)
        if self.canceled(id):
            return
        self.update(id, status="running", started_at=time.time())
        try:
            t = self.get(id)
            p = t["request"]
            if t["kind"] == "generate" and p.pop("prepare_prompt", False):
                from .clone_materials import compile_material_prompt

                self.update(id, phase="prompt", percent=0, message="正在准备提示词")
                compiled = compile_material_prompt(self, p)
                if self.canceled(id):
                    return
                p.update(prompt=compiled["prompt"], optimizer=compiled["optimizer"],
                         material_manifest=compiled["manifest"], prompt_plan=compiled.get("plan", {}))
                self.validate_generation(p)
                self.update(id, request=p)
            if p.get("preservation") or p.get("preservation_settings"):
                raise ValueError("旧蒙板任务已停用，请选择「重新生成」重新编译无蒙板提示词")
            directory = self.folder(id)
            env = {
                **os.environ,
                "PYTHONUTF8": "1",
                "SHIYIN_VIDEO_DEPTH_MODEL_ROOT": str(ROOT_DIR / "models/clone/depth"),
                "SHIYIN_VIDEO_DEPTH_SOURCE_ROOT": str(ROOT_DIR / "third_party"),
            }
            if t["kind"] == "region":
                from .clone_region import validate_regions

                directory.mkdir(parents=True, exist_ok=True)
                self.update(id, phase="region", message="正在准备演员区域参考")
                # 使用普通源片裁切；确认后的区域任务是不可变的素材快照。
                self.trim_reference_clip(id, {**p, "reference_mode": "source_video"}, directory / "clip.mp4", env)
                uses_quality_mask = any(r["fill"] in {"auto", "crop"} for r in p["regions"])
                uses_tracking = any(r["fill"] == "track" for r in p["regions"])
                if uses_quality_mask or uses_tracking:
                    self.update(id, phase="preparing", percent=0)
                    runtime = prepare_depth(ROOT_DIR, "small", True, person_mask_model="quality",
                                            emit=lambda percent, message: self.update(id, percent=percent, message=message),
                                            canceled=lambda: self.canceled(id))
                    env = runtime["env"]
                if uses_tracking:
                    self.update(id, phase="preparing", percent=0, message="正在准备指定演员视频跟踪")
                    tracking_runtime = prepare_auxiliary(
                        ROOT_DIR,
                        tracking=True,
                        emit=lambda percent, message: self.update(id, percent=percent, message=message),
                        canceled=lambda: self.canceled(id),
                    )
                    env = {
                        **env,
                        "PYTHONPATH": tracking_runtime["tracking_python"]
                        + os.pathsep
                        + env.get("PYTHONPATH", ""),
                    }
                asset = self.asset(p["source_id"], "video")
                width, height = asset.get("width", 512), asset.get("height", 384)
                scale = min(1, 768 / max(width, height))
                size = [max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)]
                args = directory / "region-request.json"
                args.write_text(json.dumps({"source": str((directory / "clip.mp4").resolve()), "directory": str(directory.resolve()),
                                            "regions": validate_regions(p["regions"], p["end"] - p["start"]), "size": size,
                                            "tracking_model": str((ROOT_DIR / "models/clone" / TRACKING_MODEL_NAME).resolve())}), encoding="utf-8")
                self.update(id, phase="region", percent=0, message="正在跟踪所选演员并生成遮罩预览")
                result = self.gpu_work(id, lambda: self.run_command(id, [sys.executable, str(ROOT_DIR / "app/clone_region_worker.py"), str(args)], env))
                result.update(video=f"/api/clone/tasks/{id}/region-preview", reference=f"/api/clone/tasks/{id}/region-reference")
            elif t["kind"] == "depth":
                model = p.get("depth_model", "small")
                self.update(
                    id, phase="preparing", percent=0, message="正在准备深度模型与运行时"
                )
                runtime = prepare_depth(
                    ROOT_DIR,
                    model,
                    p.get("remove_background", False) and not p.get("adjusted_from"),
                    person_mask_model=p.get("person_mask_model", "lite"),
                    emit=lambda percent, message: self.update(
                        id, percent=percent, message=message
                    ),
                    canceled=lambda: self.canceled(id),
                )
                env = runtime["env"]
                a = self.asset(p["source_id"], "video")
                source = self.folder(a["id"]) / a["file"]
                start, end = validate_interval(p["start"], p["end"], a["duration"])
                count = math.floor((end - start) * 24 + 1e-6)
                clip = directory / "clip.mp4"
                if p.get("adjusted_from"):
                    original = self.get(p["adjusted_from"])
                    shutil.copy2(self.folder(original["id"]) / "clip.mp4", clip)
                    raw = self.folder(p["raw_depth_id"]) / "depth/depth-relative-normalized.npz"
                    output = directory / "depth/depth-preview.mp4"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    self.update(id, phase="depth", percent=1, message="正在应用深度调参")
                    metadata = self.run_command(id, [*runtime["command"], "postprocess",
                        "--raw", str(raw), "--output", str(output),
                        "--params-json", json.dumps(p["controls"])], env)
                else:
                    self.update(id, phase="depth", percent=1,
                                message="正在原分辨率无损裁切并对齐24 FPS")
                    self.run_command(id, [runtime["ffmpeg"], "-v", "error", "-y", "-ss",
                        str(start), "-i", str(source), "-vf", "fps=24", "-frames:v",
                        str(count), "-an", "-c:v", "libx264", "-crf", "0", str(clip)], env)
                    command = [*runtime["command"], "infer", "--model",
                        DEPTH_MODELS[model]["key"], "--input", str(clip),
                        "--output-dir", str(directory / "depth"), "--input-size", "322",
                        "--max-resolution", "-1", "--extraction-mode",
                        "person" if p["depth_mode"] == "portrait" and p.get("remove_background", False)
                        else "professional", "--person-mask-model", p.get("person_mask_model", "lite")]
                    metadata = self.gpu_work(id, lambda: self.run_command(id, command, env))
                output = directory / "depth/depth-preview.mp4"
                if not output.is_file():
                    raise RuntimeError("深度模型未输出视频")
                result = {
                    "video": f"/api/clone/tasks/{id}/media",
                    "duration": count / 24,
                    "metadata": metadata,
                }
            elif t["kind"] == "analyze":
                self.update(id, phase="preparing", percent=0, message="正在准备 Qwen 视觉模型")
                prepare_auxiliary(
                    ROOT_DIR, qwen=True,
                    emit=lambda percent, message: self.update(id, percent=percent, message=message),
                    canceled=lambda: self.canceled(id),
                )
                source = directory / "clip.mp4"
                self.trim_reference_clip(id, p, source, env)
                args = directory / "request.json"
                args.write_text(
                    json.dumps(
                        {
                            "source": str(source),
                            "directory": str(directory),
                            "gpu_layers": 99,
                        }
                    ),
                    encoding="utf-8",
                )
                result = self.gpu_work(
                    id,
                    lambda: self.run_command(
                        id,
                        [
                            sys.executable,
                            str(ROOT_DIR / "app/clone_worker.py"),
                            "analyze",
                            str(args),
                        ],
                        env,
                    ),
                )
            else:
                context = self.resolve_reference(p)
                duration = context["duration"]
                uses_depth = context["mode"] in {"extracted_depth", "depth_video"}
                reference_clip = (
                    self.folder(p["depth_id"]) / "clip.mp4"
                    if context["mode"] == "extracted_depth"
                    else directory / "clip.mp4"
                )
                if context["mode"] != "extracted_depth":
                    self.update(id, phase="prepare", message="正在裁切并对齐参考视频")
                    self.trim_reference_clip(id, p, reference_clip, env)
                paths = []
                lightweight = context["mode"] == "prompt_video"
                needs_qwen = not lightweight and (p.get("auto_prompt") or p.get("supplement")) and not p.get("_reuse_prompt")
                upscale_settings = load_upscale_settings(ROOT_DIR) if p.get("upscale") else {"engine": "realesrgan"}
                self.update(id, phase="preparing", percent=0, message="正在准备复刻所需模型")
                prepare_auxiliary(
                    ROOT_DIR, qwen=bool(needs_qwen), upscale=bool(p.get("upscale")) and upscale_settings["engine"] == "realesrgan", reference=True,
                    comfy_dir=COMFY_DIR,
                    emit=lambda percent, message: self.update(id, percent=percent, message=message),
                    canceled=lambda: self.canceled(id),
                )
                if self.canceled(id):
                    return
                self.update(id, diffusion_model=REFERENCE_DIFFUSION_MODEL)
                if uses_depth:
                    from .clone_control_runtime import prepare_control

                    self.update(id, phase="prepare", message="正在核验深度结构控制模型")
                    prepare_control(ROOT_DIR)
                    self.jobs._comfy.ensure_running()
                    node_info = self.jobs._comfy._get_json(
                        "/object_info/MiniMaxCloneDepthControlApply"
                    )
                    if "MiniMaxCloneDepthControlApply" not in node_info:
                        raise RuntimeError(
                            "深度结构控制节点已部署，请重启本机生成服务后重试"
                        )

                material_input = p.get("input_mode") == "materials"
                groups = [{"images": p["reference_images"], "prepared_images": p.get("prepared_images", [])}] if material_input else p["people"]
                for person in groups:
                    if p.get("_reuse_prompt"):
                        if not all(Path(path).is_file() for path in person.get("prepared_images", [])) or (person["images"] and not person.get("prepared_images")):
                            raise ValueError("原任务引用快照文件缺失，请重新创建任务")
                        continue
                    prepared = []
                    for aid in person["images"]:
                        asset = self.asset(aid, "image")
                        prepared.append(self.folder(aid) / asset["file"])
                    person["prepared_images"] = [str(path) for path in prepared]
                if material_input:
                    p["prepared_images"] = groups[0]["prepared_images"]
                if sum(len(person["prepared_images"]) for person in groups) > 9:
                    raise ValueError(
                        "实际引用超过9张参考图，请减少每个人物的重复参考图"
                    )
                compiled = None
                if lightweight and not material_input and not p.get("_reuse_prompt"):
                    from .clone_prompt_compiler import (
                        append_lightweight_instruction,
                        compile_lightweight_prompt,
                    )

                    compiled = compile_lightweight_prompt(p["people"], duration, p.get("supplement"))
                    if p.get("auto_prompt"):
                        p["prompt"] = compiled["prompt"]
                    elif p.get("supplement"):
                        p["prompt"] = append_lightweight_instruction(p["prompt"], p["supplement"])
                    compiled["prompt"] = p["prompt"]
                    (directory / "prompt-plan.json").write_text(json.dumps(compiled, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.update(id, request=p, prompt_schema=compiled["schema"], message="纯提示词已就绪，直接提交原片与人物图")
                if not lightweight and p.get("auto_prompt") and not p.get("_reuse_prompt"):
                    self.update(
                        id,
                        phase="appearance",
                        message="正在解析目标人物外观并规划替换指令",
                    )
                    images = []
                    reference_groups = []
                    for person in p["people"]:
                        asset = self.asset(person["images"][0], "image")
                        images.append(str(self.folder(asset["id"]) / asset["file"]))
                        reference_groups.append(
                            [{"asset_id": aid, "path": str(
                                self.folder(aid) / self.asset(aid, "image")["file"]
                            )} for aid in person["images"]]
                        )
                    args = directory / "appearance.json"
                    args.write_text(
                        json.dumps(
                            {
                                "reference_images": images,
                                "reference_groups": reference_groups,
                                "motion_source": str(reference_clip),
                                "scene_source": str(
                                    reference_clip
                                )
                                if context["depth_mode"] == "professional" and context["mode"] != "depth_video"
                                else None,
                                "directory": str(directory),
                                "gpu_layers": 99,
                            }
                        ),
                        encoding="utf-8",
                    )
                    appearance = self.gpu_work(
                        id,
                        lambda: self.run_command(
                            id,
                            [
                                sys.executable,
                                str(ROOT_DIR / "app/clone_worker.py"),
                                "analyze",
                                str(args),
                            ],
                            env,
                        ),
                    )
                    if self.canceled(id):
                        return
                    by_id = {
                        row["id"]: row["description"] for row in appearance["people"]
                    }
                    attributes_by_id = {
                        row["id"]: row.get("appearance_attributes", {})
                        for row in appearance["people"]
                    }
                    for i, person in enumerate(p["people"], 1):
                        if i not in by_id:
                            raise ValueError("人物参考图未识别到清晰主体，请更换参考图")
                        person["description"] = by_id[i]
                        person["appearance_attributes"] = attributes_by_id[i]
                        group = appearance["reference_plan"]["groups"][i - 1]
                        person["prepared_views"] = group["views"]
                        person["prepared_images"] = [
                            view["path"] for view in group["views"]
                        ]
                    observations = (
                        self.get(p["analysis_id"])["result"]["frames"]
                        if p.get("analysis_id")
                        else []
                    )
                    from .clone_prompt_compiler import compile_prompt

                    compiled = compile_prompt(
                        p["people"],
                        duration,
                        context["depth_mode"],
                        observations,
                        appearance.get("scene_description", ""),
                        appearance.get("motion_observations", []),
                        reference_mode="source_video" if context["mode"] == "region_video" else context["mode"],
                    )
                    p["prompt"] = compiled["prompt"]
                    (directory / "prompt-plan.json").write_text(
                        json.dumps(compiled, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    p["reference_summary"] = [
                        entry["layout"]["kind"] for entry in appearance["reference_plan"]["assets"]
                    ]
                    self.update(
                        id, request=p, reference_plan=appearance["reference_plan"],
                        prompt_schema=compiled["schema"],
                    )
                if not lightweight and p.get("supplement") and not p.get("_reuse_prompt"):
                    p["base_prompt"] = p["prompt"]
                    self.update(id, phase="supplement", message="正在转译本次补充需求")
                    supplement_path = directory / "supplement-request.json"
                    supplement_path.write_text(
                        json.dumps({"supplement": p["supplement"], "base_prompt": p["prompt"], "manual_prompt": not p.get("auto_prompt"), "people": [{"subject_index": i, "track_id": person.get("track_id", i), "position": person["position"]} for i, person in enumerate(p["people"], 1)]}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    raw_plan = self.gpu_work(id, lambda: self.run_command(id, [sys.executable, str(ROOT_DIR / "app/clone_worker.py"), "translate-supplement", str(supplement_path)], env))
                    if self.canceled(id):
                        return
                    plan = validate_supplement_plan(raw_plan, p["supplement"], p["people"])
                    (directory / "supplement-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
                    if p.get("auto_prompt"):
                        compiled = compile_prompt(p["people"], duration, context["depth_mode"], observations, appearance.get("scene_description", ""), appearance.get("motion_observations", []), plan, reference_mode=context["mode"])
                        p["prompt"] = compiled["prompt"]
                        (directory / "prompt-plan.json").write_text(json.dumps(compiled, ensure_ascii=False, indent=2), encoding="utf-8")
                        self.update(id, prompt_schema=compiled["schema"])
                    else:
                        from .clone_supplement import merge_prompt
                        p["prompt"] = merge_prompt(p["prompt"], plan)
                    p["supplement_plan"] = plan
                    self.update(id, request=p, supplement_plan=plan, message="补充需求已合并到本次提示词")
                if context["mode"] == "region_video" and not p.get("_reuse_prompt"):
                    from .clone_region import region_prompt

                    region_rows = context["region"]["request"]["regions"]
                    person = p["people"][0]
                    if (
                        any((r["box"][2] - r["box"][0]) * (r["box"][3] - r["box"][1]) >= .55 for r in region_rows)
                        and len(person["prepared_images"]) < 9
                    ):
                        detail = _prepare_closeup_reference(
                            Path(person["prepared_images"][0]),
                            directory / "references" / "person-1-closeup-detail.png",
                        )
                        person["prepared_images"].append(str(detail))
                        person["detail_reference_index"] = len(person["prepared_images"])
                    p["prompt"] = region_prompt(
                        person,
                        duration,
                        region_rows,
                        source_shots=compiled.get("shots", []) if compiled else None,
                    )
                    self.update(id, prompt_schema="clone.region-prompt/v1")
                    (directory / "region-prompt.txt").write_text(p["prompt"], encoding="utf-8")
                p["compiled_snapshot"] = True
                self.update(id, request=p)
                references = COMFY_INPUT_DIR / "h3_refs"
                references.mkdir(parents=True, exist_ok=True)
                for person in groups:
                    for prepared in person["prepared_images"]:
                        base = references / f"clone-{id}-{len(paths) + 1}"
                        target = _copy_h3_reference_image(Path(prepared), base)
                        paths.append("h3_refs/" + target.name)
                validate_picture_references(p["prompt"], len(paths))
                from .h3_workflow import duration_to_frame_length

                width, height = RESOLUTION_PRESETS[
                    p.get("resolution", "0.2MP 16:9 - 608x352")
                ]
                reference_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
                    "tpad=stop_mode=clone:stop_duration=1"
                )
                semantic_video = None
                control_video = None
                media_inputs = []
                if context["mode"] in {"source_video", "region_video", "prompt_video"}:
                    semantic_video = f"clone-{id}-source.mp4"
                    semantic_source = self.folder(p["region_id"]) / "region.mp4" if context["mode"] == "region_video" else reference_clip
                    media_inputs.append((semantic_source, semantic_video))
                if uses_depth:
                    control_video = f"clone-{id}-control-depth.mp4"
                    control_source = (
                        self.folder(p["depth_id"]) / "depth/depth-preview.mp4"
                        if context["mode"] == "extracted_depth" else reference_clip
                    )
                    media_inputs.append((control_source, control_video))
                for media_source, media_name in media_inputs:
                    self.run_command(
                        id,
                        [
                            "ffmpeg", "-v", "error", "-y", "-i", str(media_source),
                            "-vf", reference_filter,
                            "-frames:v", str(duration_to_frame_length(duration)),
                            "-an", "-crf", "0", str(references / media_name),
                        ],
                    )
                videos = ["h3_refs/" + semantic_video] if semantic_video else []
                validate_video_references(p["prompt"], len(videos))
                data = self.archive(
                    {
                        "mode": "references",
                        "reference_model": "ref2va",
                        "prompt": p["prompt"],
                        "user_brief": p.get("user_prompt", ""),
                        "prompt_plan": p.get("prompt_plan", {}),
                        "duration": duration,
                        "resolution": p.get("resolution", "0.2MP 16:9 - 608x352"),
                        "steps": p.get("steps", 12),
                        "seed": p.get("seed"),
                        "reference_images": paths,
                        "reference_videos": videos,
                        "depth_control": uses_depth,
                        "control_video": "h3_refs/" + control_video if control_video else None,
                        "control_strength": p.get("control_strength", 1.0),
                    }
                )
                if self.canceled(id):
                    return
                job = self.jobs.create(data)
                self.update(id, h3_job_id=job.id, message="H3任务已入队", percent=0)
                while True:
                    if self.canceled(id):
                        self.jobs.cancel(job.id)
                        return
                    h3 = self.jobs.get(job.id)
                    self.update(
                        id,
                        message=h3.message,
                        percent=getattr(h3, "progress", 0),
                        phase="h3",
                        sampling_timing=getattr(h3, "sampling_timing", None),
                    )
                    if h3.status == "done":
                        break
                    if h3.status in ("error", "canceled"):
                        raise RuntimeError(h3.error or h3.message)
                    time.sleep(0.5)
                # H3按17帧网格补齐长度，导出时恢复用户选定的精确区间长度。
                from urllib.parse import unquote

                raw = WEB_OUTPUT_DIR / Path(unquote(h3.output)).name
                trimmed = WEB_OUTPUT_DIR / reserve_video_name(WEB_OUTPUT_DIR)
                temporary_trimmed = directory / "trimmed.partial.mp4"
                self.update(id, percent=0, sampling_timing=None, message="正在对齐选定时长")
                self.run_command(
                    id,
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(raw),
                        "-t",
                        str(duration),
                        "-map",
                        "0:v",
                        "-map",
                        "0:a?",
                        "-c:v",
                        "libx264",
                        "-crf",
                        "18",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                        str(temporary_trimmed),
                    ],
                )
                temporary_trimmed.replace(trimmed)
                alignment = None
                if context["mode"] == "region_video":
                    region_request = context["region"]["request"]
                    uses_tracking = any(region["fill"] == "track" for region in region_request["regions"])
                    if uses_tracking:
                        self.update(id, phase="preparing", message="正在校验指定演员跟踪模型")
                        quality_runtime = prepare_depth(
                            ROOT_DIR,
                            "small",
                            True,
                            person_mask_model="quality",
                            emit=lambda percent, message: self.update(id, percent=percent, message=message),
                            canceled=lambda: self.canceled(id),
                        )
                        env = quality_runtime["env"]
                        tracking_runtime = prepare_auxiliary(
                            ROOT_DIR,
                            tracking=True,
                            emit=lambda percent, message: self.update(id, percent=percent, message=message),
                            canceled=lambda: self.canceled(id),
                        )
                        env = {
                            **env,
                            "PYTHONPATH": tracking_runtime["tracking_python"]
                            + os.pathsep
                            + env.get("PYTHONPATH", ""),
                        }
                    self.update(id, phase="alignment", message="正在跟踪生成人物并执行局部合成")
                    aligned = trimmed.with_name(trimmed.stem + "-region.mp4")
                    args = directory / "alignment-request.json"
                    args.write_text(json.dumps({"operation": "align", "source": str(reference_clip.resolve()),
                                                "generated": str(trimmed.resolve()), "output": str(aligned.resolve()),
                                                "directory": str(directory.resolve()), "size": [width, height],
                                                "source_masks": str((self.folder(p["region_id"]) / "masks.npz").resolve()),
                                                "tracking_model": str((ROOT_DIR / "models/clone" / TRACKING_MODEL_NAME).resolve()),
                                                "source_audio": str((self.folder(context["source_id"]) / self.asset(context["source_id"], "video")["file"]).resolve()),
                                                "audio_start": context["start"],
                                                "regions": region_request["regions"]}), encoding="utf-8")
                    alignment = self.run_command(id, [sys.executable, str(ROOT_DIR / "app/clone_region_worker.py"), str(args)], env)
                    # 保留未对齐成片供审计与恢复。
                    unaligned = trimmed
                    trimmed = aligned
                result = {
                    "video": f"/outputs/{trimmed.name}",
                    "h3_job_id": job.id,
                    "original_video": f"/outputs/{trimmed.name}",
                    "raw_h3_video": h3.output,
                }
                if alignment:
                    result["region_alignment"] = alignment
                    result["unaligned_video"] = f"/outputs/{unaligned.name}"
                if p.get("upscale"):
                    self.update(
                        id, phase="upscale", percent=0, message="准备进行2×超分"
                    )
                    src = trimmed
                    out = trimmed.with_name(f"{trimmed.stem}{upscale_output_suffix(upscale_settings)}.mp4")
                    args = directory / "upscale.json"
                    args.write_text(
                        json.dumps({"source": str(src), "output": str(out), "upscale_settings": upscale_settings}),
                        encoding="utf-8",
                    )
                    upscale_info = self.gpu_work(
                        id,
                        lambda: self.run_command(
                            id,
                            [
                                sys.executable,
                                str(ROOT_DIR / "app/clone_worker.py"),
                                "upscale",
                                str(args),
                            ],
                            env,
                        ),
                    )
                    result["video"] = f"/outputs/{out.name}"
                    result["upscaled_video"] = f"/outputs/{out.name}"
                    result["upscale_info"] = upscale_info
            warnings = result.get("region_alignment", {}).get("tracking_warnings", [])
            message = "处理完成 · 请重点复核轮廓突变镜头" if warnings else "处理完成"
            self.update(id, status="done", percent=100, message=message, result=result)
        except Exception as exc:
            LOGGER.exception("克隆任务 %s 失败", id)
            self.update(id, status="error", message=str(exc), error=str(exc))

    def cancel(self, id):
        with self.lock:
            task = self.get(id)
            if task["status"] not in FINAL:
                self.update(id, status="canceled", message="任务已取消")
                if task.get("h3_job_id"):
                    self.jobs.cancel(task["h3_job_id"])
            return self.get(id)

    @staticmethod
    def _cleanup_reason(task):
        if task.get("kind") != "generate":
            return None
        status = task.get("status")
        if status in {"error", "canceled"}:
            return status
        if status != "done":
            return None
        result = task.get("result")
        video = result.get("video") if isinstance(result, dict) else None
        if not isinstance(video, str) or not re.fullmatch(r"/outputs/[^/\\]+\.mp4", video):
            return "missing_video"
        path = WEB_OUTPUT_DIR / video.removeprefix("/outputs/")
        try:
            return None if path.is_file() and path.stat().st_size > 0 else "missing_video"
        except OSError:
            return "missing_video"

    def _archive_task_locked(self, id):
        record = self.folder(id) / "task.json"
        if record.is_file():
            archive = self.root / ".cleanup-archive"
            archive.mkdir(exist_ok=True)
            record.replace(archive / f"{id}-{uuid4().hex}.json")
        del self.tasks[id]

    def cleanup_abnormal(self):
        reasons = {"error": 0, "canceled": 0, "missing_video": 0}
        deferred = 0
        with self.lock:
            for id, task in list(self.tasks.items()):
                reason = self._cleanup_reason(task)
                if not reason:
                    continue
                if id in self.in_flight:
                    self.pending_cleanup.add(id)
                    deferred += 1
                    continue
                self._archive_task_locked(id)
                reasons[reason] += 1
        return {"cleaned": sum(reasons.values()), "deferred": deferred, "by_reason": reasons}


def install_routes(app, jobs, archive, *, manager=None):
    manager = manager or CloneManager(jobs, archive)
    router = APIRouter(prefix="/api/clone")

    def action(fn):
        try:
            return fn()
        except (ValueError, TypeError, KeyError) as e:
            raise HTTPException(400, str(e)) from e

    def task_response(data):
        return JSONResponse(
            data,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    @router.get("/config")
    def config():
        files = {
            "h3_ref2va": COMFY_DIR
            / "models/diffusion_models"
            / REFERENCE_DIFFUSION_MODEL,
            "depth": ROOT_DIR
            / "models/clone/depth/video-depth-anything-small/video_depth_anything_vits.pth",
            "qwen": ROOT_DIR / "models/clone/qwen/Qwen3-VL-4B-Q4_K_M.gguf",
            "projector": ROOT_DIR / "models/clone/qwen/mmproj.gguf",
            "llama": ROOT_DIR / "vendor/clone-llama/llama-server.exe",
            "upscale": ROOT_DIR / "models/clone/upscale/RealESRGAN_x2plus.pth",
        }
        settings = DEFAULT_CLONE_SETTINGS.copy()
        stored = {}
        if manager.settings_path.exists():
            try:
                stored = json.loads(manager.settings_path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    settings.update({key: stored[key] for key in settings if key in stored})
            except (OSError, ValueError):
                pass
        if settings["depth_model"] not in DEPTH_MODELS:
            settings["depth_model"] = "small"
        if settings["reference_mode"] not in REFERENCE_MODES:
            settings["reference_mode"] = "extracted_depth"
        pipeline_version = stored.get("pipeline_version") if isinstance(stored, dict) else None
        if not isinstance(pipeline_version, int) or pipeline_version < CLONE_PIPELINE_VERSION:
            # 旧版“原视频参考”曾暗示动作可直接复刻，但实际没有逐帧控制。
            # 首次升级回到推荐的混合深度链路；用户之后仍可明确选择快速模式。
            settings["reference_mode"] = "extracted_depth"
        if type(settings["steps"]) is not int or not CLONE_MIN_STEPS <= settings["steps"] <= 50:
            settings["steps"] = DEFAULT_CLONE_SETTINGS["steps"]
        files["depth"] = ROOT_DIR / "models/clone/depth" / DEPTH_MODELS[settings["depth_model"]]["file"]
        return {
            "models": {k: p.is_file() for k, p in files.items()},
            "settings": settings,
            "depth_models": [{"id": key, "label": value["label"], "license": value["license"],
                              "installed": (ROOT_DIR / "models/clone/depth" / value["file"]).is_file()}
                             for key, value in DEPTH_MODELS.items()],
            "depth_auto_install": True,
            "aux_auto_install": True,
            "resolutions": list(CLONE_RESOLUTIONS),
            "min_duration": 2,
            "max_duration": 15,
            "max_images": 9,
        }

    @router.post("/settings")
    def settings(data: dict):
        if (
            data.get("reference_mode", "extracted_depth") not in REFERENCE_MODES
            or data.get("depth_mode") not in ("portrait", "professional")
            or data.get("depth_model", "small") not in DEPTH_MODELS
            or data.get("resolution") not in CLONE_RESOLUTIONS
            or not isinstance(data.get("upscale"), bool)
            or type(data.get("steps")) is not int
            or not CLONE_MIN_STEPS <= data["steps"] <= 50
            or not isinstance(data.get("seed"), str)
            or (data["seed"] and (not data["seed"].isdigit() or int(data["seed"]) < 0))
        ):
            raise HTTPException(400, "设置无效")
        manager.settings_path.parent.mkdir(parents=True, exist_ok=True)
        manager.settings_path.write_text(
            json.dumps(
                {
                    "reference_mode": data.get("reference_mode", "extracted_depth"),
                    "depth_mode": data["depth_mode"],
                    "depth_model": data.get("depth_model", "small"),
                    "resolution": data["resolution"],
                    "upscale": data["upscale"],
                    "steps": data["steps"],
                    "seed": data["seed"],
                    "pipeline_version": CLONE_PIPELINE_VERSION,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {"ok": True}

    @router.post("/assets")
    async def upload(file: Annotated[UploadFile, File()], kind: Annotated[str, Form()]):
        if kind not in ("video", "image"):
            raise HTTPException(400, "素材类型无效")
        id = uuid4().hex
        folder = manager.folder(id)
        folder.mkdir()
        path = folder / ("source.mp4" if kind == "video" else "image.png")
        size = 0
        try:
            with path.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > (
                        1024 * 1024 * 1024 if kind == "video" else 20 * 1024 * 1024
                    ):
                        raise ValueError("视频最大1GB，参考图最大20MB")
                    out.write(chunk)
            info = {}
            if kind == "video":
                import asyncio

                info = await asyncio.to_thread(probe, path)
                if not 2 <= info["duration"] <= 7200:
                    raise ValueError("源视频时长须为2秒至2小时")
            else:
                from PIL import Image, ImageOps

                with Image.open(path) as img:
                    normalized = ImageOps.exif_transpose(img).convert("RGB")
                normalized.save(path, format="PNG")
            data = {
                "id": id,
                "name": file.filename,
                "kind": kind,
                "file": path.name,
                **info,
                "url": f"/api/clone/assets/{id}/media",
            }
            (folder / "asset.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            return data
        except Exception as e:
            path.unlink(missing_ok=True)
            raise HTTPException(400, str(e)) from e

    @router.get("/assets/{id}")
    def asset_info(id: str):
        return action(lambda: manager.asset(id))

    @router.get("/assets/{id}/media")
    def asset_media(id: str):
        a = action(lambda: manager.asset(id))
        return FileResponse(manager.folder(id) / a["file"])

    @router.get("/assets/{id}/clip")
    def asset_clip(id: str, start: float, end: float, cloud: bool = False):
        try:
            clip = asset_reference_clip(manager, id, start, end, cloud)
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        return FileResponse(clip, media_type="video/mp4")

    @router.post("/compile-prompt")
    def compile_materials(request: dict):
        from .clone_materials import compile_material_prompt

        return action(lambda: compile_material_prompt(manager, request))

    @router.post("/cloud-plan")
    def cloud_plan(request: dict):
        from .clone_cloud_plan import compile_depth_plan, validate_manual_picture_labels

        if request.get("reference_mode") == "region_video":
            raise HTTPException(400, "演员区域替换当前仅支持本地引擎")
        if request.get("reference_type") != "depth":
            raise HTTPException(400, "仅深度视频专业模式需要 Qwen 引用规划")
        context = action(lambda: manager.resolve_reference(request))
        if context["mode"] in {"source_video", "prompt_video"}:
            raise HTTPException(400, "原视频直传模式不需要深度场景规划")
        people = request.get("people")
        if not isinstance(people, list) or not 1 <= len(people) <= 9:
            raise HTTPException(400, "请配置1–9位人物")
        image_ids = []
        for person in people:
            if not isinstance(person, dict) or not person.get("images"):
                raise HTTPException(400, "每个人物至少需要一张参考图")
            for asset_id in person["images"]:
                action(lambda asset_id=asset_id: manager.asset(asset_id, "image"))
                image_ids.append(asset_id)
        if len(image_ids) > 8:
            raise HTTPException(400, "深度模式最多8张人物图，须为背景图保留1个名额")
        background = action(lambda: manager.asset(request.get("background_image_id"), "image"))
        scene_text = request.get("scene_text")
        if not isinstance(scene_text, str) or not scene_text.strip() or len(scene_text) > 1200:
            raise HTTPException(400, "请填写1200字以内的场景与素材说明")
        if request.get("manual_prompt") and len(str(request.get("prompt", ""))) > 5000:
            raise HTTPException(400, "专业模式手写提示词最多5000字")
        if request.get("supplement") is not None:
            action(lambda: validate_supplement(request["supplement"], people))
        request["scene_text"] = scene_text.strip()
        manual_text = str(request.get("prompt", "")) if request.get("manual_prompt") else ""
        supplement_text = request.get("supplement", {}).get("text", "") if isinstance(request.get("supplement"), dict) else ""
        action(lambda: validate_manual_picture_labels("\n".join((scene_text, manual_text, supplement_text)), len(image_ids)))
        needed = [ROOT_DIR / "models/clone/qwen/Qwen3-VL-4B-Q4_K_M.gguf",
                  ROOT_DIR / "models/clone/qwen/mmproj.gguf", ROOT_DIR / "vendor/clone-llama/llama-server.exe"]
        if not all(path.is_file() for path in needed):
            raise HTTPException(503, "本机 Qwen 视觉模型未就绪，请先安装克隆辅助运行时")
        bindings = [{"label": f"人物图 {index}", "asset_id": asset_id}
                    for index, asset_id in enumerate(image_ids, 1)]
        bindings.extend([{"label": "场景图A", "asset_id": background["id"]},
                         {"label": "深度视频", "asset_id": request.get("depth_id") or request.get("source_id")}])
        with tempfile.TemporaryDirectory(prefix="cloud-plan-", dir=manager.root) as temporary:
            input_path = Path(temporary) / "request.json"
            input_path.write_text(json.dumps({
                "scene_text": request["scene_text"],
                "manual_prompt": request.get("prompt", "") if request.get("manual_prompt") else "",
                "supplement": request.get("supplement"),
                "scene_image": str(manager.folder(background["id"]) / background["file"]),
                "bindings": bindings,
            }, ensure_ascii=False), encoding="utf-8")
            try:
                with InterProcessLock(GPU_LOCK, 600):
                    process = subprocess.run(
                        [sys.executable, str(ROOT_DIR / "app/clone_worker.py"), "plan-cloud-scene", str(input_path)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=420, creationflags=FLAGS,
                        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                    )
            except (OSError, TimeoutError, subprocess.SubprocessError) as error:
                raise HTTPException(503, f"Qwen引用规划暂不可用：{error}") from error
        result = None
        for line in reversed(process.stdout.splitlines()):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if parsed.get("type") == "result":
                result = parsed["result"]
                break
            if parsed.get("type") == "error":
                raise HTTPException(502, "Qwen引用规划失败：" + str(parsed.get("error", "未知错误"))[:300])
        if process.returncode or not isinstance(result, dict):
            raise HTTPException(502, "Qwen未能返回有效的引用规划")
        return action(lambda: compile_depth_plan(request, result, context["duration"]))

    @router.get("/tasks")
    def tasks():
        with manager.lock:
            return task_response({
                "items": sorted(
                    copy.deepcopy(list(manager.tasks.values())),
                    key=lambda x: x["created_at"],
                    reverse=True,
                ),
                "paused": manager.paused,
            })

    @router.post("/tasks/cleanup")
    def cleanup_tasks():
        return action(manager.cleanup_abnormal)

    @router.post("/tasks/{kind}")
    def create(kind: str, data: dict):
        return action(lambda: manager.submit(kind, data))

    @router.post("/prompt")
    def prompt(data: dict):
        action(lambda: manager.validate_generation(data))
        return {"prompt": data["prompt"]}

    @router.get("/tasks/{id}")
    def task(id: str):
        return task_response(action(lambda: manager.get(id)))

    @router.delete("/tasks/{id}")
    def cancel(id: str):
        return action(lambda: manager.cancel(id))

    @router.post("/queue")
    def pause(data: dict):
        manager.paused = bool(data.get("paused"))
        return {"paused": manager.paused}

    @router.get("/tasks/{id}/media")
    def depth_media(id: str):
        t = action(lambda: manager.get(id))
        if t["kind"] != "depth" or t["status"] != "done":
            raise HTTPException(404, "深度视频未生成")
        return FileResponse(
            manager.folder(id) / "depth/depth-preview.mp4", media_type="video/mp4"
        )

    @router.get("/tasks/{id}/region-preview")
    @router.get("/tasks/{id}/region-reference")
    def region_media(id: str, request: Request):
        t = action(lambda: manager.get(id))
        if t["kind"] != "region" or t["status"] != "done":
            raise HTTPException(404, "演员遮罩尚未准备完成")
        name = "preview.mp4" if request.url.path.endswith("region-preview") else "region.mp4"
        return FileResponse(manager.folder(id) / name, media_type="video/mp4")

    @router.get("/tasks/{id}/source")
    def source_media(id: str, cloud: bool = False):
        t = action(lambda: manager.get(id))
        if t["kind"] not in {"depth", "generate"} or t["status"] != "done":
            raise HTTPException(404, "裁剪参考视频未生成")
        if t["kind"] == "depth":
            clip = manager.folder(id) / "clip.mp4"
        elif (manager.folder(id) / "clip.mp4").is_file():
            clip = manager.folder(id) / "clip.mp4"
        elif t["request"].get("depth_id"):
            clip = manager.folder(t["request"]["depth_id"]) / "clip.mp4"
        else:
            raise HTTPException(404, "裁剪参考视频已不存在")
        if cloud:
            try:
                clip = cloud_reference_clip(clip)
            except (OSError, RuntimeError) as error:
                raise HTTPException(500, str(error)) from error
        return FileResponse(clip, media_type="video/mp4")

    @router.get("/tasks/{id}/cloud-depth")
    def cloud_depth_media(id: str):
        task = action(lambda: manager.get(id))
        if task["kind"] != "depth" or task["status"] != "done":
            raise HTTPException(404, "深度视频尚未生成")
        try:
            clip = cloud_reference_clip(manager.folder(id) / "depth/depth-preview.mp4")
        except (OSError, RuntimeError) as error:
            raise HTTPException(500, str(error)) from error
        return FileResponse(clip, media_type="video/mp4")

    app.include_router(router)
    return manager
