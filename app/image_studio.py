"""共用 H3 ComfyUI、GPU 锁和首帧素材的图片创作节点。"""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import logging
import os
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field
from websockets.exceptions import ConnectionClosed, WebSocketException

from .comfy_client import ComfyError
from .config import OUTPUT_DIR, ROOT_DIR, WEB_DIR
from .process_lock import InterProcessLock
from .image_processing import apply_mask, pack_references, validate_mask
from .qwen_keys import QwenKeys, QwenPrincipal, assert_visible
from .qwen_node_profile import LOADER
from .qwen_workflow import (
    CLIP,
    MODEL,
    VAE,
    comfy_prompt,
    decode_reference,
    generation_size,
    parse_size,
    png_bytes,
    prepare_native_references,
    steps_for_quality,
    validate_model,
)

logger = logging.getLogger(__name__)


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    model: str = MODEL
    size: str = "1024x1024"
    quality: str = "auto"
    steps: int | None = Field(default=None, ge=1, le=80)
    seed: int | None = Field(default=None, ge=0, lt=2**63)
    n: int = Field(default=1, ge=1, le=8)
    image: list[str] | str | None = None
    response_format: str = "url"
    transparent: bool = False
    mask: str | None = None
    extra_body: dict = Field(default_factory=dict)


class ImageStudio:
    def __init__(self, comfy, directory: Path | None = None):
        self.comfy = comfy
        self.directory = directory or Path(
            os.environ.get("MINIMAX_IMAGE_STUDIO_DIR", OUTPUT_DIR / "image-studio")
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / "jobs.sqlite3"
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="image-studio"
        )
        with closing(self.db()) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            for row in db.execute("SELECT id, data FROM jobs").fetchall():
                job = json.loads(row[1])
                if job["status"] in {"queued", "running"}:
                    job.update(
                        status="failed",
                        error="服务已重启，未完成任务已停止，请重新提交",
                        finished_at=time.time(),
                    )
                    db.execute(
                        "UPDATE jobs SET data=? WHERE id=?",
                        (json.dumps(job, ensure_ascii=False), row[0]),
                    )
            db.commit()

        self.keys = QwenKeys(self.db_path)

    def db(self):
        return sqlite3.connect(self.db_path, timeout=30)

    def save(self, table, item):
        assert table in {"jobs", "images"}
        with closing(self.db()) as db:
            db.execute(
                f"INSERT OR REPLACE INTO {table}(id,data) VALUES(?,?)",
                (item["id"], json.dumps(item, ensure_ascii=False)),
            )
            db.commit()

    def get(self, table, item_id):
        assert table in {"jobs", "images"}
        with closing(self.db()) as db:
            row = db.execute(
                f"SELECT data FROM {table} WHERE id=?", (item_id,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "图片或任务不存在")
        return json.loads(row[0])

    def list(self, table, limit=100, *, key_id=None):
        assert table in {"jobs", "images"}
        with closing(self.db()) as db:
            where = " WHERE json_extract(data,'$.key_id')=?" if key_id is not None else ''
            params = (key_id, limit) if key_id is not None else (limit,)
            items = [
                json.loads(row[0])
                for row in db.execute(
                    f"SELECT data FROM {table}{where} ORDER BY json_extract(data,'$.created_at') DESC,rowid DESC LIMIT ?", params
                )
            ]
            if table == "jobs":
                image_ids = {row[0] for row in db.execute("SELECT id FROM images")}
                for job in items:
                    job["images"] = [image for image in job.get("images", []) if image["id"] in image_ids]
            return items

    def image_path(self, image_id):
        self.get("images", image_id)
        # ID 由本服务生成，不能使用客户端或 ComfyUI 返回的路径读取本机文件。
        if len(image_id) != 32 or any(
            char not in "0123456789abcdef" for char in image_id
        ):
            raise HTTPException(404, "图片不存在")
        path = self.directory / f"{image_id}.png"
        if not path.is_file():
            raise HTTPException(404, "图片文件已丢失")
        return path

    def work_items(self):
        with closing(self.db()) as db:
            images = [json.loads(row[0]) for row in db.execute("SELECT data FROM images")]
        return [
            {**item, "media_type": "image", "filename": f"{item['id']}.png",
             "output": item["url"], "resolution": f"{item['width']}x{item['height']}",
             "mode": "图片创作", "has_metadata": True}
            for item in images
            if len(item["id"]) == 32 and all(char in "0123456789abcdef" for char in item["id"])
            and (self.directory / f"{item['id']}.png").is_file()
        ]

    def delete_image(self, image_id):
        image = self.get("images", image_id)
        # 同样允许清理文件已丢失的历史记录。
        if len(image_id) != 32 or any(char not in "0123456789abcdef" for char in image_id):
            raise HTTPException(404, "图片不存在")
        (self.directory / f"{image_id}.png").unlink(missing_ok=True)
        with closing(self.db()) as db:
            db.execute("DELETE FROM images WHERE id=?", (image_id,))
            row = db.execute("SELECT data FROM jobs WHERE id=?", (image.get("job_id"),)).fetchone()
            if row:
                job = json.loads(row[0])
                job["images"] = [item for item in job.get("images", []) if item["id"] != image_id]
                db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(job, ensure_ascii=False), job["id"]))
            db.commit()

    def reference_path(self, job_id, index):
        job = self.get("jobs", job_id)
        if (
            len(job_id) != 32
            or any(char not in "0123456789abcdef" for char in job_id)
            or index < 0
            or index >= len(job.get("references", []))
        ):
            raise HTTPException(404, "参考图不存在")
        path = self.directory / "references" / job_id / f"{index}.png"
        if not path.is_file():
            raise HTTPException(404, "原参考图文件已丢失，请重新添加")
        return path

    def status(self):
        required = {
            node["class_type"]
            for node in comfy_prompt("probe", 512, 512, 1, 1, []).values()
        }
        try:
            info = self.comfy._get_json("/object_info", timeout=5)
        except (OSError, ValueError, ComfyError):
            return {
                "ready": False,
                "backend_url": self.comfy.base_url,
                "message": "生成后端尚未启动，提交任务时将自动准备共用后端与图片模型",
            }
        missing = sorted(required - info.keys())
        if os.environ.get('MINIMAX_MANAGED_NODE') == '1':
            from .config import COMFY_DIR
            for category, filename in [('diffusion_models', MODEL), ('text_encoders', CLIP), ('vae', VAE)]:
                if not (COMFY_DIR / 'models' / category / filename).is_file():
                    missing.append(filename)
        for node, field, name in [
            (LOADER, "unet_name", MODEL),
            ("CLIPLoader", "clip_name", CLIP),
            ("VAELoader", "vae_name", VAE),
        ]:
            available = (
                info.get(node, {})
                .get("input", {})
                .get("required", {})
                .get(field, [[]])[0]
            )
            if name not in available:
                missing.append(name)
        return {
            "ready": not missing,
            "backend_url": self.comfy.base_url,
            "model": MODEL,
            "missing": missing,
            "message": "图片与视频共用 ComfyUI"
            if not missing
            else "首次图片任务将自动补齐本机依赖；当前缺少：" + "、".join(missing),
        }

    def create(self, request: ImageRequest, principal: QwenPrincipal | None = None):
        validate_model(request.model)
        prompt = request.prompt.strip()
        if not prompt:
            raise HTTPException(422, "请输入图片创作需求")
        width, height = parse_size(request.size)
        steps = steps_for_quality(request.quality, request.steps)
        if request.seed is not None and request.seed + request.n > 2**63:
            raise HTTPException(422, "seed 与批量数量组合超出 64 位种子范围")
        values = request.image or request.extra_body.get("image") or []
        values = [values] if isinstance(values, str) else values
        if (
            not isinstance(values, list)
            or len(values) > 20
            or not all(isinstance(item, str) for item in values)
        ):
            raise HTTPException(422, "支持最多 20 张 data URL 参考图")
        references = [decode_reference(value) for value in values]
        mask = decode_reference(request.mask) if request.mask else None
        if mask and not references:
            raise HTTPException(422, "蒙版需要至少一张参考图")
        if mask:
            validate_mask(mask)
        job_id = uuid.uuid4().hex
        saved_references = []
        if references:
            reference_dir = self.directory / "references" / job_id
            reference_dir.mkdir(parents=True, exist_ok=True)
            for index, raw in enumerate(references):
                (reference_dir / f"{index}.png").write_bytes(raw)
                saved_references.append({
                    "name": f"参考图 {index + 1}.png",
                    "url": f"/api/image-studio/jobs/{job_id}/references/{index}",
                })
        if mask:
            (self.directory / "references" / job_id / "mask.png").write_bytes(mask)
        job = {
            "id": job_id,
            "status": "queued",
            "phase": "queued",
            "message": "等待共用 GPU 队列",
            "prompt": prompt,
            "size": f"{width}x{height}",
            "steps": steps,
            "seed": request.seed,
            "total": request.n,
            "completed": 0,
            "images": [],
            "created_at": time.time(),
            "reference_count": len(references),
            "references": saved_references,
            "mask": bool(mask),
            "transparent": bool(request.transparent or request.extra_body.get("transparent")),
            "key_id": principal.id if principal and principal.role == 'api' else None,
            "key_name": principal.name if principal and principal.role == 'api' else None,
        }
        self.save("jobs", job)
        self.executor.submit(self.run, copy.deepcopy(job), references)
        return job

    def run(self, job, references):
        try:
            job.update(phase="prompt", message="正在准备提示词")
            self.save("jobs", job)
            job["prompt"] = job["prompt"].strip()
            # 模型下载不持有 GPU 锁，H3 视频仍可运行；只在正式托管的本机后端补齐。
            def emit(percent, message):
                job.update(phase="dependencies", message=message, dependency_progress=percent)
                self.save("jobs", job)
            prepare = getattr(self.comfy, "prepare_image_models", None)
            if prepare:
                prepare(emit)
            if job.get("transparent"):
                from .image_background import prepare_model
                prepare_model(emit)
            job.update(phase="queued", message="等待共用 GPU 队列")
            self.save("jobs", job)
            with InterProcessLock(OUTPUT_DIR / "runtime/clone-gpu.lock", 6 * 3600):
                prepare_backend = getattr(self.comfy, "prepare_image_backend", None)
                if prepare_backend:
                    prepare_backend(emit)
                self.comfy.ensure_running()
                state = self.status()
                if not state["ready"]:
                    raise RuntimeError(state["message"])
                job.update(
                    status="running",
                    phase="preparing",
                    message="正在准备图片模型与参考图",
                    started_at=time.time(),
                )
                self.save("jobs", job)
                width, height = parse_size(job["size"])
                mask = (self.directory / "references" / job["id"] / "mask.png").read_bytes() if job.get("mask") else None
                sample_w, sample_h = generation_size(width, height, max_pixels=1024 * 1024, max_edge=1536) if mask else generation_size(width, height)
                prepared, packed = pack_references(references, 15 if mask else 16)
                aligned = False
                if not mask:
                    prepared, aligned = prepare_native_references(prepared, sample_w, sample_h)
                prompt = job["prompt"]
                if packed:
                    prompt += "\nThe last reference is a contact sheet of additional numbered reference images."
                if aligned:
                    prompt = (
                        "在完整目标画布内编辑，首参考四周延展仅用于画幅对齐；用连续场景铺满，不保留空白边框。\n"
                        + prompt
                    )
                names = [self.upload(raw) for raw in prepared]
                if mask:
                    names.append(self.upload(mask))
                    prompt += "\nUse the final reference as an edit mask: white regions may change; preserve everything outside them."
                seed = (
                    job["seed"] if job["seed"] is not None else secrets.randbelow(2**53)
                )
                for index in range(job["total"]):
                    graph = comfy_prompt(
                        prompt, sample_w, sample_h, job["steps"], seed + index, names, native_references=not bool(mask)
                    )
                    raw = self.execute(graph, job)
                    if mask:
                        raw = apply_mask(references[0], raw, mask, width, height)
                    image_id = uuid.uuid4().hex
                    with Image.open(io.BytesIO(raw)) as source:
                        image = source.copy()
                    if image.size != (width, height):
                        image = image.resize((width, height), Image.Resampling.LANCZOS)
                    if job.get("transparent"):
                        from .image_background import make_transparent
                        job.update(phase='saving', message='正在生成透明背景并保存 PNG')
                        self.save('jobs', job)
                        buffer = io.BytesIO()
                        image.save(buffer, format="PNG")
                        raw = make_transparent(buffer.getvalue())
                        with Image.open(io.BytesIO(raw)) as transparent_image:
                            image = transparent_image.copy()
                    image.save(self.directory / f"{image_id}.png")
                    item = {
                        "id": image_id,
                        "job_id": job["id"],
                        "prompt": job["prompt"],
                        "width": width,
                        "height": height,
                        "steps": job["steps"],
                        "seed": seed + index,
                        "created_at": time.time(),
                        "sampling_size": f"{sample_w}x{sample_h}",
                        "native_resolution": (width, height) == (sample_w, sample_h),
                        "url": f"/api/image-studio/images/{image_id}/file",
                        "prompt_id": job.get("prompt_id"),
                        "backend_url": self.comfy.base_url,
                        "filename": f"{image_id}.png",
                        "favorite": False,
                        "batch_index": index + 1,
                        "transparent": job.get("transparent", False),
                        "masked": job.get("mask", False),
                        "key_id": job.get('key_id'),
                        "key_name": job.get('key_name'),
                    }
                    self.save("images", item)
                    job["images"].append(item)
                    job["completed"] += 1
                    self.save("jobs", job)
                job.update(
                    status="completed",
                    phase="completed",
                    message="图片已完成，可用作视频首帧",
                    finished_at=time.time(),
                )
                self.save("jobs", job)
        except Exception as exc:
            logger.exception("图片创作任务失败: %s", job["id"])
            job.update(
                status="failed",
                phase="failed",
                error=str(getattr(exc, "detail", exc)),
                message="图片生成失败",
                finished_at=time.time(),
            )
            self.save("jobs", job)

    def upload(self, raw):
        boundary = uuid.uuid4().hex
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="h3_image_{uuid.uuid4().hex}.png"\r\nContent-Type: image/png\r\n\r\n'
            ).encode()
            + raw
            + f"\r\n--{boundary}--\r\n".encode()
        )
        req = urllib.request.Request(
            self.comfy.base_url + "/upload/image",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            req, timeout=90
        ) as response:
            item = json.load(response)
        return "/".join(filter(None, [item.get("subfolder"), item["name"]]))

    def execute(self, graph, job):
        from websockets.sync.client import connect

        client_id = uuid.uuid4().hex
        ws = None
        prompt_id = None
        try:
            try:
                ws = connect(
                    self.comfy.base_url.replace("http", "ws", 1)
                    + "/ws?clientId="
                    + client_id,
                    open_timeout=5,
                    proxy=None,
                    max_size=16 * 1024 * 1024,
                )
            except (OSError, WebSocketException, TimeoutError):
                logger.info("图片事件连接不可用，仅报告队列确认的生成状态")
            result = self.comfy._post_json(
                "/prompt",
                json.dumps({"prompt": graph, "client_id": client_id}).encode(),
            )
            prompt_id = result.get("prompt_id")
            if not prompt_id:
                raise RuntimeError("ComfyUI 未返回图片任务 ID：" + str(result)[:600])
            job.update(
                prompt_id=prompt_id,
                phase="running",
                message="ComfyUI 正在生成图片",
                sample_step=None,
                sample_total=None,
            )
            self.save("jobs", job)
            deadline, next_poll, missing_since = time.monotonic() + 2400, 0, None
            while time.monotonic() < deadline:
                if ws:
                    try:
                        raw = ws.recv(timeout=0.3)
                        event = json.loads(raw) if isinstance(raw, str) else {}
                        data = event.get("data", {})
                        if data.get("prompt_id") == prompt_id:
                            if (
                                event.get("type") == "progress"
                                and data.get("max", 0) > 0
                            ):
                                job.update(
                                    phase="sampling",
                                    sample_step=data["value"],
                                    sample_total=data["max"],
                                    message=f"图片采样 {data['value']}/{data['max']}",
                                )
                                self.save("jobs", job)
                            elif event.get("type") == "executing" and data.get("node"):
                                node = str(data["node"])
                                phase, message = {
                                    "4": ("encoding", "正在理解提示词与参考画面"),
                                    "6": ("sampling", "正在采样图片"),
                                    "7": ("decoding", "正在解码图片"),
                                    "8": ("saving", "正在保存图片"),
                                }.get(node, ("preparing", "正在加载图片模型"))
                                job.update(
                                    phase=phase,
                                    message=message,
                                    sample_step=None,
                                    sample_total=None,
                                )
                                self.save("jobs", job)
                    except TimeoutError:
                        pass
                    except (ConnectionClosed, OSError, ValueError, KeyError, TypeError):
                        ws.close()
                        ws = None
                        job.update(sample_step=None, sample_total=None)
                        self.save("jobs", job)
                else:
                    time.sleep(0.5)
                if time.monotonic() < next_poll:
                    continue
                next_poll = time.monotonic() + 2
                history = self.comfy._get_json(f"/history/{prompt_id}", timeout=20).get(
                    prompt_id
                )
                if history:
                    status = history.get("status", {})
                    if status.get("status_str") != "success":
                        errors = [
                            value.get("exception_message", str(value))
                            for kind, value in status.get("messages", [])
                            if kind == "execution_error"
                        ]
                        raise RuntimeError(
                            "ComfyUI 图片生成失败：" + str(errors or status)[:1200]
                        )
                    images = history.get("outputs", {}).get("8", {}).get("images", [])
                    if not images:
                        raise RuntimeError("ComfyUI 任务完成但没有图片输出")
                    params = urllib.parse.urlencode(
                        {
                            "filename": images[0]["filename"],
                            "subfolder": images[0].get("subfolder", ""),
                            "type": "output",
                        }
                    )
                    with urllib.request.build_opener(
                        urllib.request.ProxyHandler({})
                    ).open(
                        self.comfy.base_url + "/view?" + params, timeout=90
                    ) as response:
                        return response.read()
                queue = self.comfy._get_json("/queue", timeout=10)
                present = any(
                    row[1] == prompt_id
                    for key in ("queue_running", "queue_pending")
                    for row in queue.get(key, [])
                )
                missing_since = None if present else missing_since or time.monotonic()
                if missing_since and time.monotonic() - missing_since > 30:
                    raise RuntimeError("图片任务已从 ComfyUI 队列消失，请重新提交")
            raise TimeoutError("图片生成超过 40 分钟，已请求停止该任务")
        except Exception:
            if prompt_id:
                # 仅取消本任务，不打断其他软件或 H3 的任务。
                try:
                    self.comfy.cancel_prompt(prompt_id)
                except Exception:
                    logger.exception("无法取消图片任务 %s", prompt_id)
            raise
        finally:
            if ws:
                ws.close()


def install_routes(app, jobs):
    studio = ImageStudio(jobs._comfy)
    source = ROOT_DIR.parent / 'qwen2.1/data/gallery.sqlite3'
    if not os.environ.get('MINIMAX_IMAGE_STUDIO_DIR') and studio.directory.resolve() == (OUTPUT_DIR / 'image-studio').resolve() and source.is_file():
        from .qwen_key_migration import migrate_legacy
        try:
            migrate_legacy(studio, source)
        except Exception as exc:
            studio.keys.migration_error = str(exc)
            logger.error('旧 Qwen 密钥迁移未完成：%s', exc)
    router = APIRouter()

    @router.get("/image-studio/")
    def page():
        return FileResponse(WEB_DIR / "image-studio/index.html")

    @router.get("/api/image-studio/status")
    def status(principal: QwenPrincipal = Depends(studio.keys.principal)):
        return studio.status()

    @router.post("/api/image-studio/jobs", status_code=202)
    def create(request: ImageRequest, principal: QwenPrincipal = Depends(studio.keys.principal)):
        return studio.create(request, principal)

    @router.get("/api/image-studio/jobs")
    def list_jobs(principal: QwenPrincipal = Depends(studio.keys.principal)):
        return {"jobs": studio.list('jobs', key_id=principal.id if principal.role == 'api' else None)}

    @router.get("/api/image-studio/jobs/{job_id}")
    def get_job(job_id: str, principal: QwenPrincipal = Depends(studio.keys.principal)):
        item = studio.get("jobs", job_id)
        assert_visible(item, principal)
        return item

    @router.get("/api/image-studio/jobs/{job_id}/references/{index}")
    def reference(job_id: str, index: int, principal: QwenPrincipal = Depends(studio.keys.principal)):
        assert_visible(studio.get('jobs', job_id), principal)
        return FileResponse(studio.reference_path(job_id, index), media_type="image/png")

    @router.get("/api/image-studio/images")
    def images(principal: QwenPrincipal = Depends(studio.keys.principal)):
        return {"images": studio.list('images', key_id=principal.id if principal.role == 'api' else None)}

    @router.get("/api/image-studio/images/{image_id}")
    def image(image_id: str, principal: QwenPrincipal = Depends(studio.keys.principal)):
        item = studio.get("images", image_id)
        assert_visible(item, principal)
        return item

    @router.get("/api/image-studio/images/{image_id}/file")
    def file(image_id: str, principal: QwenPrincipal = Depends(studio.keys.principal)):
        assert_visible(studio.get('images', image_id), principal)
        return FileResponse(studio.image_path(image_id), media_type="image/png")

    @router.delete("/api/image-studio/images/{image_id}")
    def delete_image(image_id: str, principal: QwenPrincipal = Depends(studio.keys.principal)):
        assert_visible(studio.get('images', image_id), principal)
        studio.delete_image(image_id)
        return {"deleted": True}

    @router.get("/v1/models")
    def models(principal: QwenPrincipal = Depends(studio.keys.principal)):
        return {
            "object": "list",
            "data": [
                {
                    "id": MODEL,
                    "object": "model",
                    "owned_by": "local",
                    "native_max_pixels": 2048 * 2048,
                    "metadata": {"native_max_pixels": 2048 * 2048, "reference_canvas": True,
                                 "mask_max_pixels": 1024 * 1024, "kv_cache": "cpu-lossless",
                                 "max_request_references": 20, "transparent_background": True},
                }
            ],
        }

    def result_url(request, item):
        prefix = request.scope.get('root_path', '').rstrip('/')
        path = f"{prefix}/api/images/{item['id']}/file" if prefix.endswith('/qwen') or request.scope.get('qwen_legacy') else item['url']
        return str(request.url.replace(path=path, query=''))

    async def openai_result(payload, request):
        response_format = payload.extra_body.get(
            "response_format", payload.response_format
        )
        if response_format not in {"url", "b64_json"}:
            raise HTTPException(422, "response_format 仅支持 url 或 b64_json")
        job = studio.create(payload, studio.keys.principal(request))
        while job["status"] in {"queued", "running"}:
            await asyncio.sleep(0.5)
            job = studio.get("jobs", job["id"])
        if job["status"] != "completed":
            raise HTTPException(502, job.get("error", "图片生成失败"))
        data = []
        for item in job["images"]:
            result = (
                {
                    "b64_json": base64.b64encode(
                        studio.image_path(item["id"]).read_bytes()
                    ).decode()
                }
                if response_format == "b64_json"
                else {"url": result_url(request, item)}
            )
            result.update(
                sampling_size=item["sampling_size"],
                native_resolution=item["native_resolution"],
            )
            data.append(result)
        return {"created": int(job["finished_at"]), "data": data, "job_id": job["id"]}

    @router.post("/v1/images/generations")
    async def generate(payload: ImageRequest, request: Request):
        return await openai_result(payload, request)

    @router.post("/v1/images/edits")
    async def edit(
        request: Request,
        prompt: Annotated[str, Form(min_length=1, max_length=12000)],
        image: Annotated[list[UploadFile] | None, File()] = None,
        image_array: Annotated[list[UploadFile] | None, File(alias="image[]")] = None,
        model: str = Form(MODEL),
        size: str = Form("1024x1024"),
        quality: str = Form("auto"),
        n: int = Form(1, ge=1, le=8),
        response_format: str = Form("url"),
        steps: int | None = Form(None, ge=1, le=80),
        seed: int | None = Form(None, ge=0, lt=2**63),
        transparent: bool = Form(False),
        mask: Annotated[UploadFile | None, File()] = None,
    ):
        uploads = (image or []) + (image_array or [])
        if not uploads:
            raise HTTPException(422, "请通过 image 或 image[] 上传至少一张参考图")
        if len(uploads) > 20:
            raise HTTPException(422, "参考图最多 20 张")
        references = []
        for upload in uploads:
            raw = await upload.read(24 * 1024 * 1024 + 1)
            references.append(
                "data:image/png;base64," + base64.b64encode(png_bytes(raw)).decode()
            )
        payload = ImageRequest(
            prompt=prompt,
            model=model,
            size=size,
            quality=quality,
            n=n,
            response_format=response_format,
            image=references,
            steps=steps,
            seed=seed,
            transparent=transparent,
            mask="data:image/png;base64," + base64.b64encode(png_bytes(await mask.read(24 * 1024 * 1024 + 1))).decode() if mask else None,
        )
        return await openai_result(payload, request)

    app.include_router(router)
    from .qwen_api import install_qwen_api
    install_qwen_api(app, studio, ImageRequest, generate, edit, models)
    return studio
