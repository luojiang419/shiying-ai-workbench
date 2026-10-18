from __future__ import annotations

import asyncio
import base64
import binascii
import json
import mimetypes
import os
import re
import shutil
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor
from io import BytesIO
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import psutil
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import (
    COMFY_INPUT_DIR,
    OUTPUT_DIR,
    ROOT_DIR,
    WEB_DIR,
    WEB_HOST,
    WEB_OUTPUT_DIR,
    WEB_PORT,
    acceleration_mode,
    comfy_vram_reserve_gb,
    nsfw_enabled,
    nsfw_profile,
)
from .h3_workflow import RESOLUTION_PRESETS
from .jobs import JobManager
from .prompt_optimizer import load_assistant_providers, optimize_preferred_prompt
from .public_release import enabled as public_release_enabled
from .thumbnails import video_thumbnail

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REFERENCE_INPUT_DIR = COMFY_INPUT_DIR / "h3_refs"
REFERENCE_INPUT_DIR.mkdir(parents=True, exist_ok=True)

REFERENCE_IMAGE_MAX_BYTES = 20 * 1024 * 1024
REFERENCE_VIDEO_MAX_BYTES = 50 * 1024 * 1024
REFERENCE_VIDEO_MIN_SECONDS = 2.0
REFERENCE_VIDEO_MAX_SECONDS = 15.0
REFERENCE_AUDIO_MAX_BYTES = 50 * 1024 * 1024
REFERENCE_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

app = FastAPI(title="MiniMax H3 Local Web")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
jobs = JobManager()
_upscale_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="node-upscale")
_upscale_tasks: dict[str, tuple[str, Future[Path]]] = {}
_upscale_lock = Lock()


@app.get("/api/node-info")
def node_info() -> dict:
    """Lightweight compatibility probe before routing the desktop UI to this API."""
    return {"api": "minimax-compute-node", "version": 1, "capabilities": ["h3", "clone", "works", "upscale", "image-studio"]}


@app.post("/api/upscale")
def start_node_upscale(request: dict) -> dict:
    filename = request.get("filename")
    if (not isinstance(filename, str) or not filename or any(char in filename for char in "/\\")
            or Path(filename).name != filename or not filename.lower().endswith(".mp4")):
        raise HTTPException(status_code=400, detail="超分原片文件名无效")
    if not (WEB_OUTPUT_DIR / filename).is_file():
        raise HTTPException(status_code=404, detail="节点上不存在该原片")
    from .upscale_task import upscale_saved_video

    with _upscale_lock:
        for task_id, (source, future) in _upscale_tasks.items():
            if source == filename and not future.done():
                return {"task_id": task_id, "status": "running"}
        task_id = str(uuid4())
        _upscale_tasks[task_id] = (filename, _upscale_executor.submit(upscale_saved_video, filename))
    return {"task_id": task_id, "status": "running"}


@app.get("/api/upscale/{task_id}")
def node_upscale_status(task_id: str) -> dict:
    with _upscale_lock:
        task = _upscale_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="超分任务不存在")
    future = task[1]
    if not future.done():
        return {"task_id": task_id, "status": "running"}
    try:
        path = future.result()
    except Exception as exc:
        return {"task_id": task_id, "status": "failed", "error": str(exc)}
    return {"task_id": task_id, "status": "done", "name": path.name, "output": f"/outputs/{path.name}"}

app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")
app.mount("/outputs", StaticFiles(directory=str(WEB_OUTPUT_DIR)), name="outputs")

GPU_WARN_FREE_GB = 2.0
GPU_DANGER_FREE_GB = 1.0
GPU_WARN_UTIL_PERCENT = 70.0
GPU_DANGER_UTIL_PERCENT = 90.0
COMPETITION_PROCESS_HINTS = {
    "lm studio": "LM Studio",
    "lmstudio": "LM Studio",
    "llama-server": "llama-server",
    "ollama": "Ollama",
    "quicklook": "QuickLook",
    "ffmpeg": "视频转码/预览工具",
    "davinci": "DaVinci Resolve",
    "resolve": "DaVinci Resolve",
    "premiere": "Adobe Premiere",
    "afterfx": "Adobe After Effects",
}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/generation-eta.js", include_in_schema=False)
def generation_eta_script() -> FileResponse:
    # 克隆页的 ../generation-eta.js 导入解析到根路径，桌面嵌入也使用此入口。
    return FileResponse(WEB_DIR / "generation-eta.js", media_type="text/javascript")


@app.get("/cloud-error-zh.js", include_in_schema=False)
def cloud_error_zh_script() -> FileResponse:
    return FileResponse(WEB_DIR / "cloud-error-zh.js", media_type="text/javascript")


@app.get("/generation-service.js", include_in_schema=False)
def generation_service_script() -> FileResponse:
    return FileResponse(WEB_DIR / "generation-service.js", media_type="text/javascript")


@app.get("/resolution-selectors.js", include_in_schema=False)
def resolution_selectors_script() -> FileResponse:
    return FileResponse(WEB_DIR / "resolution-selectors.js", media_type="text/javascript")


@app.get("/motion.js", include_in_schema=False)
def motion_script() -> FileResponse:
    return FileResponse(WEB_DIR / "motion.js", media_type="text/javascript")


@app.get("/motion.css", include_in_schema=False)
def motion_style() -> FileResponse:
    return FileResponse(WEB_DIR / "motion.css", media_type="text/css")


@app.get("/api/config")
def config() -> dict:
    try:
        selected_mode = acceleration_mode()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "resolutions": list(RESOLUTION_PRESETS.keys()),
        "defaults": {
            "resolution": "0.2MP 16:9 - 608x352",
            "duration": 2,
            "steps": 12,
        },
        "acceleration_mode": selected_mode,
        "vram_reserve_gb": comfy_vram_reserve_gb(),
        "nsfw_enabled": nsfw_enabled(),
        "nsfw_profile": nsfw_profile(),
    }


@app.get("/api/status")
def status() -> dict:
    return jobs.status_summary()

@app.get('/api/runtime-health')
def runtime_health() -> dict:
    try:
        return json.loads((ROOT_DIR/'outputs/runtime/gpu-runtime.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'roles': {}, 'message': '运行时检测尚未完成'}



@app.get("/api/prompt-optimizer/status")
def prompt_optimizer_status() -> dict:
    try:
        return {"enabled": bool(load_assistant_providers())}
    except ValueError as exc:
        return {"enabled": False, "warning": str(exc)}


@app.post("/api/optimize-prompt")
async def optimize_prompt(request: dict) -> dict:
    """仅使用已启用的 AI 助手优化；未配置时原样返回提示词。"""
    try:
        return await asyncio.to_thread(optimize_preferred_prompt, request)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"自动优化提示词失败：{exc}") from exc


@app.get("/api/resources")
def resources() -> dict:
    return _system_resources()


@app.get("/api/preflight")
def preflight() -> dict:
    return _generation_preflight()


@app.post("/api/comfy/free")
def free_comfy_cache() -> dict:
    try:
        return jobs.free_backend_cache()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/comfy/start")
def start_comfy() -> dict:
    try:
        return jobs.request_backend_start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/generate")
def generate(payload: dict) -> dict:
    try:
        payload = _archive_api_reference_payload(payload)
        job = jobs.create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asdict(job)


@app.post("/api/reference-thumbnail")
async def reference_thumbnail(image: UploadFile = File(...)) -> Response:
    content = await image.read(REFERENCE_IMAGE_MAX_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="参考图为空")
    if len(content) > REFERENCE_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="参考图不能超过 20MB")
    try:
        thumbnail = await asyncio.to_thread(_create_reference_thumbnail, content)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="参考图格式无效") from exc
    return Response(content=thumbnail, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _create_reference_thumbnail(content: bytes) -> bytes:
    with Image.open(BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((384, 384), Image.Resampling.BILINEAR, reducing_gap=3.0)
        if image.mode not in {"RGB", "L"}:
            background = Image.new("RGB", image.size, "black")
            if "A" in image.getbands():
                background.paste(image, mask=image.getchannel("A"))
            else:
                background.paste(image)
            image = background
        elif image.mode == "L":
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=78, optimize=False)
        return output.getvalue()


@app.post("/api/generate-upload")
async def generate_upload(
    prompt: str = Form(...),
    user_brief: str = Form(""),
    prompt_plan: str = Form(""),
    mode: str = Form("keyframes"),
    reference_model: str = Form("ref2va"),
    resolution: str = Form("0.2MP 16:9 - 608x352"),
    duration: float = Form(2),
    steps: int = Form(12),
    seed: str = Form(""),
    first_frame: UploadFile | None = File(None),
    last_frame: UploadFile | None = File(None),
    reference_images: list[UploadFile] | None = File(None),
    reference_videos: list[UploadFile] | None = File(None),
    reference_audios: list[UploadFile] | None = File(None),
) -> dict:
    payload = {
        "mode": mode,
        "reference_model": reference_model,
        "prompt": prompt,
        "user_brief": user_brief,
        "resolution": resolution,
        "duration": duration,
        "steps": steps,
        "seed": seed,
    }
    if prompt_plan:
        try:
            parsed_plan = json.loads(prompt_plan)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="提示词规划数据不是有效 JSON") from exc
        if not isinstance(parsed_plan, dict) or len(prompt_plan) > 50000:
            raise HTTPException(status_code=400, detail="提示词规划数据无效或过长")
        payload["prompt_plan"] = parsed_plan
    if first_frame and first_frame.filename:
        payload["first_frame"] = await _save_reference_image(first_frame, "first")
    if last_frame and last_frame.filename:
        payload["last_frame"] = await _save_reference_image(last_frame, "last")
    saved_reference_images = []
    for image in reference_images or []:
        if image and image.filename:
            if len(saved_reference_images) >= 9:
                raise HTTPException(status_code=400, detail="多参考图最多支持 9 张")
            saved_reference_images.append(await _save_reference_image(image, "ref"))
    if saved_reference_images:
        payload["reference_images"] = saved_reference_images
    saved_reference_videos = []
    for video in reference_videos or []:
        if video and video.filename:
            if len(saved_reference_videos) >= 3:
                raise HTTPException(status_code=400, detail="多参考模式最多支持 3 个参考视频")
            saved_reference_videos.append(await _save_reference_video(video, "video"))
    if saved_reference_videos:
        payload["reference_videos"] = saved_reference_videos
    saved_reference_audios = []
    for audio in reference_audios or []:
        if audio and audio.filename:
            if len(saved_reference_audios) >= 3:
                raise HTTPException(status_code=400, detail="多参考模式最多支持 3 个参考音频")
            saved_reference_audios.append(await _save_reference_audio(audio, "audio"))
    if saved_reference_audios:
        payload["reference_audios"] = saved_reference_audios
    try:
        job = jobs.create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asdict(job)


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    data = asdict(job)
    data.update(jobs.queue_details(job_id))
    if data.get("error"):
        data["error"] = data["error"].splitlines()[0]
    return data


@app.get("/api/job-history")
def job_history(
    offset: int = 0,
    limit: int = 100,
    status: str | None = None,
    order: str = "desc",
) -> dict:
    try:
        data = jobs.list_job_history(offset=offset, limit=limit, status=status, order=order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for item in data["items"]:
        if item.get("error"):
            item["error"] = item["error"].splitlines()[0]
    return data


@app.delete("/api/job-history/failed")
def clear_failed_job_history() -> dict:
    deleted = jobs.clear_failed_jobs()
    return {"deleted_count": len(deleted), "deleted_ids": deleted}


@app.get("/api/jobs/{job_id}/download")
def download_job_output(job_id: str) -> FileResponse:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not job.output:
        raise HTTPException(status_code=409, detail="任务尚未生成可下载文件")
    path = _resolve_job_output(job.output)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=410, detail="任务记录存在，但视频文件已不存在")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str) -> dict:
    job = jobs.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return asdict(job)


@app.get("/api/works")
def works() -> dict:
    return {"items": jobs.list_works(), "total": jobs.count_works()}


@app.get("/api/videos")
def videos(offset: int = 0, limit: int = 100, order: str = "desc") -> dict:
    try:
        return jobs.list_videos(offset=offset, limit=limit, order=order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/works/page")
def works_page(request: Request, offset: int = 0, limit: int = 24, order: str = "desc", query: str = "", refresh: bool = False, media_type: str = "video") -> dict:
    try:
        if media_type not in {"all", "video", "image"}:
            raise ValueError("作品类型只支持 all、video 或 image")
        if refresh:
            from .library import invalidate_video_index
            invalidate_video_index(WEB_OUTPUT_DIR)
        image_items = None
        if media_type != 'video' and image_studio is not None:
            principal = image_studio.keys.principal(request)
            image_items = [item for item in image_studio.work_items() if principal.role != 'api' or item.get('key_id') == principal.id]
        return jobs.page_works(offset=offset, limit=limit, order=order, query=query.strip()[:200],
                               image_items=image_items,
                               include_videos=media_type != "image")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/job-stats")
def job_stats(since: float = 0) -> dict:
    return {"completed": jobs.completed_since(since)}


@app.get("/api/work-thumbnail/{filename}")
def work_thumbnail(filename: str) -> FileResponse:
    path = _resolve_job_output(f"/outputs/{filename}")
    if not path.is_file() or path.suffix.lower() not in {".mp4", ".webm", ".mov", ".mkv"}:
        raise HTTPException(status_code=404, detail="作品不存在")
    try:
        thumbnail = video_thumbnail(path)
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail="预览暂不可用，请刷新重试") from exc
    return FileResponse(thumbnail, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/jobs")
def active_jobs() -> dict:
    return {"items": jobs.list_active_jobs()}


@app.delete("/api/works/{work_id}")
def delete_work(work_id: str) -> dict:
    try:
        if not jobs.delete_work(work_id):
            raise HTTPException(status_code=404, detail="作品不存在")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail="视频被其他程序占用或无删除权限，请关闭外部播放器后重试；作品记录已保留") from exc
    return {"ok": True}


@app.get("/api/reference-input/{asset_path:path}")
def reference_input(asset_path: str) -> FileResponse:
    path = _resolve_reference_input(asset_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="参考素材不存在")
    return FileResponse(path)


def _resolve_reference_input(asset_path: str) -> Path:
    parts = Path(asset_path).parts
    if not parts or parts[0] != "h3_refs":
        raise HTTPException(status_code=400, detail="参考素材路径无效")
    suffix = Path(asset_path).suffix.lower()
    if not _is_supported_image_suffix(suffix) and not _is_supported_video_suffix(suffix):
        raise HTTPException(status_code=400, detail="参考素材路径无效")

    root = COMFY_INPUT_DIR.resolve()
    path = (COMFY_INPUT_DIR / asset_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="参考素材路径无效") from exc
    return path


def _resolve_job_output(output: str) -> Path:
    normalized = unquote(str(output)).strip().replace("\\", "/")
    prefix = "/outputs/"
    if not normalized.startswith(prefix):
        raise HTTPException(status_code=400, detail="任务输出路径无效")
    relative = normalized[len(prefix):]
    if not relative or ".." in relative.split("/"):
        raise HTTPException(status_code=400, detail="任务输出路径无效")

    root = WEB_OUTPUT_DIR.resolve()
    path = (WEB_OUTPUT_DIR / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="任务输出路径无效") from exc
    return path


def _archive_api_reference_payload(payload: dict) -> dict:
    archived = dict(payload)
    if "first_frame" in archived:
        archived["first_frame"] = _archive_api_reference_value(archived.get("first_frame"), "first")
    if "last_frame" in archived:
        archived["last_frame"] = _archive_api_reference_value(archived.get("last_frame"), "last")
    if "reference_images" in archived:
        raw_images = archived.get("reference_images")
        if raw_images in (None, ""):
            archived["reference_images"] = []
        elif not isinstance(raw_images, (list, tuple)):
            raise ValueError("多参考图参数格式不正确")
        else:
            if len(raw_images) > 9:
                raise ValueError("多参考图模式最多支持 9 张参考图")
            archived["reference_images"] = [
                image
                for image in (
                    _archive_api_reference_value(item, "ref")
                    for item in raw_images
                )
                if image
            ]
    if "reference_videos" in archived:
        raw_videos = archived.get("reference_videos")
        if raw_videos in (None, ""):
            archived["reference_videos"] = []
        elif not isinstance(raw_videos, (list, tuple)):
            raise ValueError("参考视频参数格式不正确")
        else:
            if len(raw_videos) > 3:
                raise ValueError("多参考模式最多支持 3 个参考视频")
            archived["reference_videos"] = [
                video
                for video in (
                    _archive_api_reference_video_value(item, "video")
                    for item in raw_videos
                )
                if video
            ]
    if "reference_audios" in archived:
        raw_audios = archived.get("reference_audios")
        if raw_audios in (None, ""):
            archived["reference_audios"] = []
        elif not isinstance(raw_audios, (list, tuple)):
            raise ValueError("参考音频参数格式不正确")
        else:
            if len(raw_audios) > 3:
                raise ValueError("多参考模式最多支持 3 个参考音频")
            archived["reference_audios"] = [
                audio
                for audio in (
                    _archive_api_reference_audio_value(item, "audio")
                    for item in raw_audios
                )
                if audio
            ]
    if "control_video" in archived:
        archived["control_video"] = _archive_api_reference_video_value(
            archived.get("control_video"), "control"
        )
    return archived


def _archive_api_reference_value(value: Any, prefix: str) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        filename = str(value.get("filename") or value.get("name") or f"{prefix}.png")
        content = value.get("data") or value.get("base64") or value.get("content")
        if not content:
            raise ValueError("API 参考图缺少 data/base64/content")
        if not value.get("filename") and not value.get("name") and str(content).startswith("data:image/"):
            filename = _data_url_filename(str(content), prefix)
        return _save_reference_bytes(_decode_image_payload(str(content), filename), filename, prefix)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("data:image/"):
            filename = _data_url_filename(text, prefix)
            return _save_reference_bytes(_decode_image_payload(text, filename), filename, prefix)
        return _archive_existing_reference_path(text, prefix)
    raise ValueError("参考图参数格式不正确")


def _archive_api_reference_video_value(value: Any, prefix: str) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        filename = str(value.get("filename") or value.get("name") or f"{prefix}.mp4")
        content = value.get("data") or value.get("base64") or value.get("content")
        if not content:
            raise ValueError("API 参考视频缺少 data/base64/content")
        if not value.get("filename") and not value.get("name") and str(content).startswith("data:video/"):
            filename = _video_data_url_filename(str(content), prefix)
        return _save_reference_video_bytes(
            _decode_video_payload(str(content), filename),
            filename,
            prefix,
        )
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("data:video/"):
            filename = _video_data_url_filename(text, prefix)
            return _save_reference_video_bytes(_decode_video_payload(text, filename), filename, prefix)
        return _archive_existing_reference_video_path(text, prefix)
    raise ValueError("参考视频参数格式不正确")


def _archive_api_reference_audio_value(value: Any, prefix: str) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        filename = str(value.get("filename") or value.get("name") or f"{prefix}.mp3")
        content = value.get("data") or value.get("base64") or value.get("content")
        if not content:
            raise ValueError("API 参考音频缺少 data/base64/content")
        return _save_reference_audio_bytes(_decode_audio_payload(str(content), filename), filename, prefix)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("data:audio/"):
            return _save_reference_audio_bytes(_decode_audio_payload(text, f"{prefix}.mp3"), f"{prefix}.mp3", prefix)
        return _archive_existing_reference_audio_path(text, prefix)
    raise ValueError("参考音频参数格式不正确")


def _archive_existing_reference_path(image_path: str, prefix: str) -> str:
    normalized = unquote(image_path).strip().replace("\\", "/")
    normalized = re.sub(r"^/api/reference-input/", "", normalized)
    normalized = normalized.lstrip("/")
    if not normalized or ".." in normalized.split("/") or Path(normalized).is_absolute():
        raise ValueError("参考图文件名不安全")
    if not _is_supported_image_suffix(Path(normalized).suffix):
        raise ValueError("参考图只支持 png、jpg、jpeg、webp")

    source = (COMFY_INPUT_DIR / normalized).resolve()
    root = COMFY_INPUT_DIR.resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("参考图文件名不安全") from exc
    if not source.exists() or not source.is_file():
        raise ValueError("参考图文件不存在")

    if normalized.startswith("h3_refs/"):
        _verify_reference_file(source)
        return normalized

    target = _next_reference_target(Path(normalized).name, prefix)
    shutil.copyfile(source, target)
    _verify_reference_file(target, remove_invalid=True)
    return f"h3_refs/{target.name}"


def _archive_existing_reference_video_path(video_path: str, prefix: str) -> str:
    normalized = unquote(video_path).strip().replace("\\", "/")
    normalized = re.sub(r"^/api/reference-input/", "", normalized)
    normalized = normalized.lstrip("/")
    if not normalized or ".." in normalized.split("/") or Path(normalized).is_absolute():
        raise ValueError("参考视频文件名不安全")
    if not _is_supported_video_suffix(Path(normalized).suffix):
        raise ValueError("参考视频只支持 mp4、mov")

    source = (COMFY_INPUT_DIR / normalized).resolve()
    root = COMFY_INPUT_DIR.resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("参考视频文件名不安全") from exc
    if not source.exists() or not source.is_file():
        raise ValueError("参考视频文件不存在")

    if normalized.startswith("h3_refs/"):
        _prepare_reference_video(source, normalize_fps=False)
        return normalized

    target = _next_reference_video_target(Path(normalized).name, prefix)
    shutil.copyfile(source, target)
    prepared = _prepare_reference_video(target, normalize_fps=True)
    return f"h3_refs/{prepared.name}"


def _archive_existing_reference_audio_path(audio_path: str, prefix: str) -> str:
    normalized = unquote(audio_path).strip().replace("\\", "/")
    normalized = re.sub(r"^/api/reference-input/", "", normalized).lstrip("/")
    if not normalized or ".." in normalized.split("/") or Path(normalized).is_absolute():
        raise ValueError("参考音频文件名不安全")
    if not _is_supported_audio_suffix(Path(normalized).suffix):
        raise ValueError("参考音频只支持 mp3、wav、m4a、aac、flac、ogg")
    source = (COMFY_INPUT_DIR / normalized).resolve()
    try:
        source.relative_to(COMFY_INPUT_DIR.resolve())
    except ValueError as exc:
        raise ValueError("参考音频文件名不安全") from exc
    if not source.is_file():
        raise ValueError("参考音频文件不存在")
    if normalized.startswith("h3_refs/"):
        _verify_reference_audio_file(source)
        return normalized
    target = _next_reference_audio_target(Path(normalized).name, prefix)
    shutil.copyfile(source, target)
    _verify_reference_audio_file(target, remove_invalid=True)
    return f"h3_refs/{target.name}"


def _decode_image_payload(content: str, filename: str) -> bytes:
    data = content.strip()
    if data.startswith("data:image/"):
        header, _, body = data.partition(",")
        if ";base64" not in header:
            raise ValueError("API 参考图 data URL 必须使用 base64")
        data = body
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"API 参考图 {filename} 不是有效 base64") from exc


def _decode_video_payload(content: str, filename: str) -> bytes:
    data = content.strip()
    if data.startswith("data:video/"):
        header, _, body = data.partition(",")
        if ";base64" not in header:
            raise ValueError("API 参考视频 data URL 必须使用 base64")
        data = body
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"API 参考视频 {filename} 不是有效 base64") from exc


def _decode_audio_payload(content: str, filename: str) -> bytes:
    data = content.strip()
    if data.startswith("data:audio/"):
        header, _, body = data.partition(",")
        if ";base64" not in header:
            raise ValueError("API 参考音频 data URL 必须使用 base64")
        data = body
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"API 参考音频 {filename} 不是有效 base64") from exc


def _data_url_filename(content: str, prefix: str) -> str:
    header = content.partition(",")[0]
    match = re.match(r"^data:image/(png|jpe?g|webp);base64$", header, re.IGNORECASE)
    if not match:
        raise ValueError("API 参考图 data URL 只支持 png、jpg、jpeg、webp")
    image_type = match.group(1).lower()
    suffix = ".jpg" if image_type in {"jpg", "jpeg"} else f".{image_type}"
    return f"{prefix}{suffix}"


def _video_data_url_filename(content: str, prefix: str) -> str:
    header = content.partition(",")[0]
    match = re.match(r"^data:video/(mp4|quicktime);base64$", header, re.IGNORECASE)
    if not match:
        raise ValueError("API 参考视频 data URL 只支持 mp4、mov")
    suffix = ".mov" if match.group(1).lower() == "quicktime" else ".mp4"
    return f"{prefix}{suffix}"


def _save_reference_bytes(content: bytes, filename: str, prefix: str) -> str:
    suffix = Path(filename or "").suffix.lower() or ".png"
    if not _is_supported_image_suffix(suffix):
        raise ValueError("参考图只支持 png、jpg、jpeg、webp")
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("参考图不能超过 20MB")
    target = _next_reference_target(filename, prefix)
    target.write_bytes(content)
    _verify_reference_file(target, remove_invalid=True)
    return f"h3_refs/{target.name}"


def _save_reference_video_bytes(content: bytes, filename: str, prefix: str) -> str:
    suffix = Path(filename or "").suffix.lower() or ".mp4"
    if not _is_supported_video_suffix(suffix):
        raise ValueError("参考视频只支持 mp4、mov")
    if len(content) > REFERENCE_VIDEO_MAX_BYTES:
        raise ValueError("参考视频不能超过 50MB")
    target = _next_reference_video_target(filename, prefix)
    target.write_bytes(content)
    prepared = _prepare_reference_video(target, normalize_fps=True)
    return f"h3_refs/{prepared.name}"


def _save_reference_audio_bytes(content: bytes, filename: str, prefix: str) -> str:
    target = _next_reference_audio_target(filename, prefix)
    if len(content) > REFERENCE_AUDIO_MAX_BYTES:
        raise ValueError("参考音频不能超过 50MB")
    target.write_bytes(content)
    _verify_reference_audio_file(target, remove_invalid=True)
    return f"h3_refs/{target.name}"


def _next_reference_target(filename: str, prefix: str) -> Path:
    suffix = Path(filename or "").suffix.lower() or ".png"
    if not _is_supported_image_suffix(suffix):
        raise ValueError("参考图只支持 png、jpg、jpeg、webp")
    return REFERENCE_INPUT_DIR / f"{prefix}_{uuid4().hex}{suffix}"


def _next_reference_video_target(filename: str, prefix: str) -> Path:
    suffix = Path(filename or "").suffix.lower() or ".mp4"
    if not _is_supported_video_suffix(suffix):
        raise ValueError("参考视频只支持 mp4、mov")
    return REFERENCE_INPUT_DIR / f"{prefix}_{uuid4().hex}{suffix}"


def _next_reference_audio_target(filename: str, prefix: str) -> Path:
    suffix = Path(filename or "").suffix.lower() or ".mp3"
    if not _is_supported_audio_suffix(suffix):
        raise ValueError("参考音频只支持 mp3、wav、m4a、aac、flac、ogg")
    return REFERENCE_INPUT_DIR / f"{prefix}_{uuid4().hex}{suffix}"


def _verify_reference_file(path: Path, remove_invalid: bool = False) -> None:
    if path.stat().st_size > REFERENCE_IMAGE_MAX_BYTES:
        raise ValueError("参考图不能超过 20MB")
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as exc:
        if remove_invalid:
            path.unlink(missing_ok=True)
        raise ValueError("参考图不是有效图片") from exc


def _is_supported_image_suffix(suffix: str) -> bool:
    return suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}


def _is_supported_video_suffix(suffix: str) -> bool:
    return suffix.lower() in {".mp4", ".mov"}


def _is_supported_audio_suffix(suffix: str) -> bool:
    return suffix.lower() in REFERENCE_AUDIO_SUFFIXES


def _verify_reference_audio_file(path: Path, remove_invalid: bool = False) -> None:
    try:
        if path.stat().st_size == 0 or path.stat().st_size > REFERENCE_AUDIO_MAX_BYTES:
            raise ValueError("参考音频不能为空且不能超过 50MB")
    except Exception:
        if remove_invalid:
            path.unlink(missing_ok=True)
        raise


async def _save_reference_image(upload: UploadFile, prefix: str) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if not _is_supported_image_suffix(suffix):
        raise HTTPException(status_code=400, detail="参考图只支持 png、jpg、jpeg、webp")

    target = _next_reference_target(upload.filename or f"{prefix}.png", prefix)
    content = await upload.read()
    if len(content) > REFERENCE_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="参考图不能超过 20MB")
    target.write_bytes(content)

    try:
        _verify_reference_file(target, remove_invalid=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return f"h3_refs/{target.name}"


async def _save_reference_video(upload: UploadFile, prefix: str) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if not _is_supported_video_suffix(suffix):
        raise HTTPException(status_code=400, detail="参考视频只支持 mp4、mov")

    target = _next_reference_video_target(upload.filename or f"{prefix}.mp4", prefix)
    content = await upload.read(REFERENCE_VIDEO_MAX_BYTES + 1)
    if len(content) > REFERENCE_VIDEO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="参考视频不能超过 50MB")
    target.write_bytes(content)

    try:
        prepared = await asyncio.to_thread(_prepare_reference_video, target, True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return f"h3_refs/{prepared.name}"


async def _save_reference_audio(upload: UploadFile, prefix: str) -> str:
    target = _next_reference_audio_target(upload.filename or f"{prefix}.mp3", prefix)
    content = await upload.read(REFERENCE_AUDIO_MAX_BYTES + 1)
    if len(content) > REFERENCE_AUDIO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="参考音频不能超过 50MB")
    target.write_bytes(content)
    try:
        _verify_reference_audio_file(target, remove_invalid=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return f"h3_refs/{target.name}"


def _prepare_reference_video(path: Path, normalize_fps: bool) -> Path:
    try:
        info = _probe_reference_video(path)
        duration = info["duration"]
        fps = info["fps"]
        if duration < REFERENCE_VIDEO_MIN_SECONDS or duration > REFERENCE_VIDEO_MAX_SECONDS:
            raise ValueError("参考视频时长必须在 2 到 15 秒之间")
        if 23.9 <= fps <= 24.1:
            return path
        if not normalize_fps:
            raise ValueError("参考视频必须为 24 FPS，请重新上传以自动转换")
        return _transcode_reference_video_to_24fps(path)
    except Exception as exc:
        if isinstance(exc, ValueError):
            if normalize_fps:
                path.unlink(missing_ok=True)
            raise
        if normalize_fps:
            path.unlink(missing_ok=True)
        raise ValueError("参考视频无法读取或格式无效") from exc


def _probe_reference_video(path: Path) -> dict[str, float]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("未找到 ffprobe，无法校验参考视频")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,r_frame_rate:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True, creationflags=NO_WINDOW)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        duration = float(data["format"]["duration"])
        fps = _parse_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
    except (OSError, subprocess.SubprocessError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("参考视频无法读取或格式无效") from exc
    if duration <= 0 or fps <= 0:
        raise ValueError("参考视频无法读取或格式无效")
    return {"duration": duration, "fps": fps}


def _parse_frame_rate(value: object) -> float:
    text = str(value or "0").strip()
    if "/" not in text:
        return float(text)
    numerator, denominator = text.split("/", 1)
    denominator_value = float(denominator)
    return float(numerator) / denominator_value if denominator_value else 0.0


def _transcode_reference_video_to_24fps(path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("未找到 ffmpeg，无法将参考视频转换为 24 FPS")
    target = path.with_name(f"{path.stem}_24fps.mp4")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        "fps=24",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(target),
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=180, check=True, creationflags=NO_WINDOW)
        normalized = _probe_reference_video(target)
        if not 23.9 <= normalized["fps"] <= 24.1:
            raise ValueError("参考视频转换为 24 FPS 失败")
        if target.stat().st_size > REFERENCE_VIDEO_MAX_BYTES:
            raise ValueError("参考视频转换后超过 50MB")
    except (OSError, subprocess.SubprocessError) as exc:
        target.unlink(missing_ok=True)
        raise ValueError("参考视频转换为 24 FPS 失败") from exc
    except ValueError:
        target.unlink(missing_ok=True)
        raise
    path.unlink(missing_ok=True)
    return target


def _system_resources() -> dict:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(OUTPUT_DIR))
    reserve_gb = comfy_vram_reserve_gb()
    gpu = _gpu_resources()
    for device in gpu:
        total_gb = float(device.get("memory_total_gb") or 0)
        device["model_vram_budget_gb"] = round(max(0.0, total_gb - reserve_gb), 1)
    return {
        "cpu": {"percent": round(psutil.cpu_percent(interval=None), 1)},
        "memory": {
            "percent": round(memory.percent, 1),
            "used_gb": round(memory.used / (1024**3), 1),
            "available_gb": round(memory.available / (1024**3), 1),
            "total_gb": round(memory.total / (1024**3), 1),
        },
        "disk": {
            "percent": round(disk.percent, 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "total_gb": round(disk.total / (1024**3), 1),
        },
        "gpu": gpu,
        "policy": {
            "vram_reserve_gb": reserve_gb,
            "dynamic_vram_headroom_gb": reserve_gb,
            "model_residency": "pinned_system_ram_dynamic_vram",
            "disk_offload": False,
            "description": "模型优先驻留系统内存；模型预算和 DynamicVRAM 外部压力余量均按预留值执行",
        },
    }


def _gpu_resources() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    command = [
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=2, check=True, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []

    devices = []
    for line in (result.stdout or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            continue
        name, utilization, memory_used, memory_total, temperature = parts
        try:
            used_mb = float(memory_used)
            total_mb = float(memory_total)
            memory_percent = (used_mb / total_mb * 100) if total_mb else 0
            devices.append(
                {
                    "name": name,
                    "percent": round(float(utilization), 1),
                    "memory_percent": round(memory_percent, 1),
                    "memory_used_gb": round(used_mb / 1024, 1),
                    "memory_free_gb": round((total_mb - used_mb) / 1024, 1),
                    "memory_total_gb": round(total_mb / 1024, 1),
                    "temperature": round(float(temperature), 1),
                }
            )
        except ValueError:
            continue
    return devices


def _generation_preflight() -> dict:
    resources = _system_resources()
    reasons: list[dict] = []

    policy = resources.get("policy") or {}
    reserve_gb = float(policy.get("vram_reserve_gb") or 0)
    gpu = (resources.get("gpu") or [None])[0]
    if gpu:
        free_gb = float(gpu.get("memory_free_gb") or 0)
        utilization = float(gpu.get("percent") or 0)
        danger_free_gb = max(GPU_DANGER_FREE_GB, reserve_gb)
        warn_free_gb = max(GPU_WARN_FREE_GB, reserve_gb + 0.5)
        if free_gb < danger_free_gb:
            reasons.append(
                {
                    "level": "danger",
                    "kind": "gpu_vram_low",
                    "message": f"GPU 空闲显存约 {free_gb:.1f}GB，显存预留目标 {reserve_gb:.1f}GB；H3 低显存换入换出可能明显变慢",
                }
            )
        elif free_gb < warn_free_gb:
            reasons.append(
                {
                    "level": "warn",
                    "kind": "gpu_vram_low",
                    "message": f"GPU 空闲显存约 {free_gb:.1f}GB，若后端缓存较碎可能触发长等待",
                }
            )
        if utilization >= GPU_DANGER_UTIL_PERCENT:
            reasons.append(
                {
                    "level": "danger",
                    "kind": "gpu_busy",
                    "message": f"GPU 利用率 {utilization:.1f}%，当前可能有其他任务占用",
                }
            )
        elif utilization >= GPU_WARN_UTIL_PERCENT:
            reasons.append(
                {
                    "level": "warn",
                    "kind": "gpu_busy",
                    "message": f"GPU 利用率 {utilization:.1f}%，生成可能进入排队或慢路径",
                }
            )
    else:
        reasons.append(
            {
                "level": "warn",
                "kind": "gpu_unknown",
                "message": "未检测到 NVIDIA GPU 资源数据，无法提前判断显存风险",
            }
        )

    competitors = _competition_processes()
    if competitors:
        names = _format_competition_names(competitors)
        reasons.append(
            {
                "level": "warn",
                "kind": "gpu_competition_process",
                "message": f"检测到可能竞争 GPU/显存的进程：{names}",
            }
        )

    level = _highest_risk_level(reasons)
    return {
        "ok": level == "ok",
        "level": level,
        "summary": _preflight_summary(level),
        "reasons": reasons,
        "actions": _preflight_actions(level),
        "competitors": competitors,
        "resources": resources,
        "policy": policy,
    }


def _highest_risk_level(reasons: list[dict]) -> str:
    if any(reason.get("level") == "danger" for reason in reasons):
        return "danger"
    if any(reason.get("level") == "warn" for reason in reasons):
        return "warn"
    return "ok"


def _preflight_summary(level: str) -> str:
    if level == "danger":
        return "当前资源风险较高，继续生成可能出现明显慢速或长等待"
    if level == "warn":
        return "当前资源有轻微风险，继续生成通常可行但可能比平时更慢"
    return "当前资源状态正常，可以开始生成"


def _preflight_actions(level: str) -> list[str]:
    if level == "ok":
        return ["continue"]
    return ["continue", "cancel", "free_cache"]


def _competition_processes() -> list[dict]:
    found: dict[str, dict] = {}
    for item in _gpu_compute_processes() + _common_competition_processes():
        key = f"{item.get('pid')}:{item.get('label')}"
        found[key] = item
    return sorted(found.values(), key=lambda item: (str(item.get("label")), int(item.get("pid") or 0)))


def _format_competition_names(processes: list[dict]) -> str:
    counts: dict[str, int] = {}
    for item in processes:
        label = str(item.get("label") or item.get("name") or "未知进程")
        counts[label] = counts.get(label, 0) + 1
    names = []
    for label, count in sorted(counts.items()):
        names.append(f"{label} x{count}" if count > 1 else label)
    return "、".join(names[:4])


def _gpu_compute_processes() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    command = [
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=2, check=True, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []

    processes = []
    for line in (result.stdout or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            continue
        pid, process_name, used_memory = parts
        label = _competition_label(process_name)
        if not label:
            continue
        try:
            processes.append(
                {
                    "pid": int(pid),
                    "name": Path(process_name).name,
                    "label": label,
                    "gpu_memory_mb": int(float(used_memory)),
                }
            )
        except ValueError:
            continue
    return processes


def _common_competition_processes() -> list[dict]:
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        label = _competition_label(f"{name} {cmdline}")
        if label:
            processes.append({"pid": proc.info.get("pid"), "name": name, "label": label})
    return processes


def _competition_label(text: str) -> str | None:
    normalized = text.lower()
    for needle, label in COMPETITION_PROCESS_HINTS.items():
        if needle in normalized:
            return label
    return None


def main() -> None:
    import uvicorn

    from .config import COMFY_PORT
    from .service_endpoints import bind_available, save_endpoints

    # 后台先补齐依赖，Web 立即可连接并显示进度；轻量客户端跳过。
    from .config import COMFY_DIR
    if (COMFY_DIR / "main.py").is_file():
        jobs._comfy.request_dependency_prepare()

    with bind_available(WEB_HOST, WEB_PORT) as listener:
        port = listener.getsockname()[1]
        save_endpoints(ROOT_DIR, port, COMFY_PORT)
        print(f"MiniMax H3 Web: http://127.0.0.1:{port}; ComfyUI: {COMFY_PORT}", flush=True)
        server = uvicorn.Server(uvicorn.Config(app, host=WEB_HOST, port=port, reload=False))
        from .qwen_legacy_listeners import bind_legacy_listeners
        extra = bind_legacy_listeners(image_studio.keys) if image_studio is not None else []
        try:
            server.run(sockets=[listener, *extra])
        finally:
            for socket in extra:
                socket.close()


from .clone_center import install_routes as install_clone_routes
from .image_studio import install_routes as install_image_routes
from .browser_workbench import install_routes as install_browser_routes
image_studio = None if public_release_enabled() else install_image_routes(app, jobs)
clone_manager = install_clone_routes(app, jobs, _archive_api_reference_payload)
install_browser_routes(app, clone_manager=clone_manager)
app.mount("/clone-center", StaticFiles(directory=str(WEB_DIR / "clone-center"), html=True), name="clone-center")

if __name__ == "__main__":
    main()
