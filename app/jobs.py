from __future__ import annotations

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from threading import Lock
from time import time
from urllib.parse import unquote

from .acceleration import validate_acceleration_prompt
from .comfy_client import ComfyClient, ComfyError
from .config import (
    COMFY_DIR,
    REFERENCE_DIFFUSION_MODEL,
    ROOT_DIR,
    WEB_OUTPUT_DIR,
    acceleration_mode,
    free_gpu_cache_after_task,
    nsfw_enabled,
)
from .config import (
    nsfw_profile as configured_nsfw_profile,
)
from .h3_memory import (
    LOW_VRAM_GPU_LIMIT_GB,
    compatible_resolution,
    gpu_memory_total_gb,
    smaller_resolution,
)
from .h3_workflow import (
    H3Request,
    build_prompt,
    low_vram_depth_chunk_rows,
    memory_chunk_rows,
    resolve_nsfw_profile,
    sanitize_request,
)
from .job_store import JobStore
from .library import WorkItem, WorkLibrary
from .video_names import promote_generated_video


@dataclass
class Job:
    id: str
    status: str = "queued"
    message: str = "等待开始"
    created_at: float = field(default_factory=time)
    started_at: float | None = None
    updated_at: float = field(default_factory=time)
    output: str | None = None
    error: str | None = None
    completed_at: float | None = None
    elapsed_seconds: float | None = None
    mode: str = "keyframes"
    prompt: str = ""
    user_brief: str = ""
    prompt_plan: dict = field(default_factory=dict)
    resolution: str = ""
    requested_resolution: str = ""
    resolution_adjustment: str = ""
    duration: float = 0
    steps: int = 0
    seed: int | None = None
    first_frame: str | None = None
    last_frame: str | None = None
    reference_images: tuple[str, ...] = ()
    reference_videos: tuple[str, ...] = ()
    reference_audios: tuple[str, ...] = ()
    depth_control: bool = False
    control_video: str | None = None
    control_strength: float = 1.0
    reference_model: str = "ref2va"
    acceleration_mode: str | None = None
    nsfw_enabled: bool | None = None
    nsfw_profile: str | None = None
    progress: int = 0
    sampling_timing: dict | None = None


LONG_RUN_SECONDS = 20 * 60


class JobManager:
    def __init__(
        self,
        store: JobStore | None = None,
        library: WorkLibrary | None = None,
        migrate_legacy: bool = True,
    ) -> None:
        self._jobs: dict[str, Job] = {}
        self._job_order: dict[str, int] = {}
        self._next_order = 0
        self._lock = Lock()
        self._prompt_ids: dict[str, str] = {}
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._comfy = ComfyClient()
        self._library = library or WorkLibrary()
        self._store = store or JobStore()
        if migrate_legacy:
            self._migrate_legacy_history()
        self._store.fail_incomplete()

    def create(self, data: dict) -> Job:
        req = sanitize_request(data)
        requested_resolution = req.resolution
        capacity = gpu_memory_total_gb()
        safe_resolution = compatible_resolution(
            req.resolution, req.duration, capacity, verified_depth=req.depth_control,
        )
        req = replace(req, resolution=safe_resolution)
        if capacity is None or capacity <= LOW_VRAM_GPU_LIMIT_GB:
            req = replace(req, memory_chunk_rows=low_vram_depth_chunk_rows(req) or 2048)
        adjustment = (
            f"显存保护：已从 {requested_resolution} 调整为 {req.resolution}，保留时长"
            if req.resolution != requested_resolution else ""
        )
        selected_mode = acceleration_mode()
        if req.mode == 'references' and selected_mode in {'turbo_8step','turbo_8step_sage'}:
            selected_mode = 'sage_attention' if selected_mode.endswith('sage') else 'standard'
        if memory_chunk_rows(req):
            selected_mode = 'turbo_8step' if selected_mode.startswith('turbo_8step') else 'standard'
        selected_nsfw_profile = (
            None if req.mode == "references" and req.reference_model == "fl2va"
            else resolve_nsfw_profile(req.mode, nsfw_enabled(), configured_nsfw_profile())
        )
        selected_nsfw = selected_nsfw_profile is not None
        if selected_mode in {"turbo_8step", "turbo_8step_sage"}:
            req = replace(req, steps=8)
        prompt = build_prompt(
            req,
            acceleration_mode=selected_mode,
            nsfw_enabled=selected_nsfw,
            nsfw_profile=selected_nsfw_profile,
        )
        validate_acceleration_prompt(
            prompt,
            selected_mode,
            nsfw_enabled=selected_nsfw,
            nsfw_profile=selected_nsfw_profile,
        )
        job = Job(
            id=str(uuid.uuid4()),
            mode=req.mode,
            prompt=req.prompt,
            user_brief=str(data.get("user_brief") or "")[:10000],
            prompt_plan=data.get("prompt_plan") if isinstance(data.get("prompt_plan"), dict) else {},
            resolution=req.resolution,
            requested_resolution=requested_resolution,
            resolution_adjustment=adjustment,
            message=adjustment or "等待开始",
            duration=req.duration,
            steps=req.steps,
            seed=req.seed,
            first_frame=req.first_frame,
            last_frame=req.last_frame,
            reference_images=req.reference_images,
            reference_videos=req.reference_videos,
            reference_audios=req.reference_audios,
            depth_control=req.depth_control,
            control_video=req.control_video,
            control_strength=req.control_strength,
            reference_model=req.reference_model,
            acceleration_mode=selected_mode,
            nsfw_enabled=selected_nsfw,
            nsfw_profile=selected_nsfw_profile,
        )
        self._store.save(asdict(job))
        with self._lock:
            self._job_order[job.id] = self._next_order
            self._next_order += 1
            self._jobs[job.id] = job
        self._executor.submit(self._run, job.id, prompt, req)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        record = self._store.get(job_id)
        return _job_from_record(record) if record else None

    def cancel(self, job_id: str) -> Job | None:
        prompt_id = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                record = None
            else:
                record = asdict(job)
            if job is None:
                pass
            elif job.status in {"done", "error", "canceled"}:
                return job
            else:
                job.status = "canceled"
                job.message = "已取消"
                job.completed_at = time()
                job.updated_at = time()
                prompt_id = self._prompt_ids.get(job_id)
                record = asdict(job)
        if job is None:
            stored = self._store.get(job_id)
            return _job_from_record(stored) if stored else None
        self._store.save(record)
        if prompt_id:
            try:
                self._comfy.cancel_prompt(prompt_id)
            except Exception:
                logging.exception("Failed to cancel ComfyUI prompt %s for job %s", prompt_id, job_id)
        return job

    def queue_details(self, job_id: str) -> dict:
        with self._lock:
            now = time()
            job = self._jobs.get(job_id)
            running_count = sum(1 for item in self._jobs.values() if item.status == "running")
            queued_jobs = sorted(
                (item for item in self._jobs.values() if item.status == "queued"),
                key=lambda item: self._job_order.get(item.id, 0),
            )
            queued_count = len(queued_jobs)

            if job is None:
                return {
                    "queue_position": None,
                    "jobs_ahead": None,
                    "queued_count": queued_count,
                    "running_count": running_count,
                    "run_seconds": None,
                    "warning": None,
                }

            if job.status == "queued":
                queue_position = next(
                    (index + 1 for index, item in enumerate(queued_jobs) if item.id == job_id),
                    None,
                )
                jobs_ahead = running_count + max(0, (queue_position or 1) - 1)
                run_seconds = None
            elif job.status == "running":
                queue_position = 0
                jobs_ahead = 0
                run_seconds = max(0, now - (job.started_at or now))
            else:
                queue_position = None
                jobs_ahead = None
                run_seconds = (
                    max(0, (job.completed_at or now) - job.started_at)
                    if job.started_at
                    else None
                )

            return {
                "queue_position": queue_position,
                "jobs_ahead": jobs_ahead,
                "queued_count": queued_count,
                "running_count": running_count,
                "run_seconds": run_seconds,
                "warning": _timing_warning(job.status, run_seconds),
            }

    def status_summary(self) -> dict:
        backend = self._comfy.probe()
        ready = backend["ready"]
        startup = self._comfy.startup_status()
        if startup.get("state") in {"preparing_dependencies", "failed"}:
            ready = False
        try:
            selected_mode = acceleration_mode()
            selected_nsfw = nsfw_enabled()
            selected_nsfw_profile = configured_nsfw_profile()
            acceleration_error = None
        except ValueError as exc:
            selected_mode = None
            selected_nsfw = None
            selected_nsfw_profile = None
            acceleration_error = str(exc)
        node_check = {"ok": False, "missing": [backend["message"]]}
        if acceleration_error:
            node_check = {"ok": False, "missing": [acceleration_error]}
        elif ready:
            try:
                node_check = self._comfy.check_h3_nodes(
                    acceleration_mode=selected_mode,
                    nsfw_enabled=selected_nsfw,
                    nsfw_profile=selected_nsfw_profile,
                    max_age=30,
                )
            except Exception as exc:
                node_check = {"ok": False, "missing": [str(exc)]}
        with self._lock:
            queued_count = sum(1 for item in self._jobs.values() if item.status == "queued")
            running_count = sum(1 for item in self._jobs.values() if item.status == "running")
        return {
            "ready": ready,
            "backend": backend,
            "backend_start": startup,
            "h3_nodes": node_check,
            "acceleration_mode": selected_mode,
            "acceleration_error": acceleration_error,
            "nsfw_enabled": selected_nsfw,
            "nsfw_profile": selected_nsfw_profile,
            "queue": {
                "queued_count": queued_count,
                "running_count": running_count,
                "unlimited_waiting": True,
            },
            "works_count": self._library.count_videos(),
        }

    def free_backend_cache(self) -> dict:
        with self._lock:
            running_count = sum(1 for item in self._jobs.values() if item.status == "running")
        if running_count:
            raise RuntimeError("当前有任务正在生成，请等待任务结束后再释放后端缓存")
        return self._comfy.free_memory()

    def start_backend(self) -> dict:
        self._comfy.ensure_running()
        node_check = self._comfy.check_h3_nodes()
        if not node_check["ok"]:
            raise RuntimeError("当前 ComfyUI 缺少 H3 节点: " + ", ".join(node_check["missing"]))
        return {
            "ok": True,
            "message": f"ComfyUI 已启动，当前使用 {_acceleration_label(node_check.get('acceleration_mode'))}方案",
            "h3_nodes": node_check,
        }

    def request_backend_start(self) -> dict:
        state = self._comfy.request_start()
        return {
            "ok": state["state"] != "failed",
            "message": state["message"],
            "backend_start": state,
        }

    def list_works(self) -> list[dict]:
        return self._library.list()

    def count_works(self) -> int:
        return self._library.count_videos()

    def list_videos(self, offset: int = 0, limit: int = 100, order: str = "desc") -> dict:
        return self._library.list_videos(offset=offset, limit=limit, order=order)

    def page_works(self, offset: int = 0, limit: int = 24, order: str = "desc", query: str = "", *, image_items: list[dict] | None = None, include_videos: bool = True) -> dict:
        allowed = self._store.matching_outputs(query) if query else None
        page = self._library.list_videos(offset=offset, limit=limit, order=order, allowed_outputs=allowed, query=query, extra_items=image_items, include_videos=include_videos)
        outputs = [item["output"] for item in page["items"] if item.get("media_type") != "image"]
        metadata = self._store.output_metadata(list(dict.fromkeys(outputs + [unquote(output) for output in outputs])))
        for item in page["items"]:
            if item.get("media_type") == "image":
                continue
            saved = metadata.get(item["output"]) or metadata.get(unquote(item["output"]))
            if saved:
                item.update({**saved, "filename": item["filename"], "output": item["output"], "has_metadata": True})
        return page

    def completed_since(self, since: float) -> int:
        return self._store.completed_since(since)

    def list_active_jobs(self) -> list[dict]:
        with self._lock:
            now = time()
            running_count = sum(1 for item in self._jobs.values() if item.status == "running")
            queued_jobs = sorted(
                (item for item in self._jobs.values() if item.status == "queued"),
                key=lambda item: self._job_order.get(item.id, 0),
            )
            queue_positions = {item.id: index + 1 for index, item in enumerate(queued_jobs)}
            active_jobs = sorted(
                (item for item in self._jobs.values() if item.status in {"queued", "running"}),
                key=lambda item: self._job_order.get(item.id, 0),
            )

            items = []
            for job in active_jobs:
                data = asdict(job)
                if job.status == "queued":
                    queue_position = queue_positions.get(job.id)
                    jobs_ahead = running_count + max(0, (queue_position or 1) - 1)
                    run_seconds = None
                else:
                    queue_position = 0
                    jobs_ahead = 0
                    run_seconds = max(0, now - (job.started_at or now))
                data.update(
                    {
                        "queue_position": queue_position,
                        "jobs_ahead": jobs_ahead,
                        "run_seconds": run_seconds,
                        "warning": _timing_warning(job.status, run_seconds),
                    }
                )
                items.append(data)
            return items

    def list_job_history(
        self,
        offset: int = 0,
        limit: int = 100,
        status: str | None = None,
        order: str = "desc",
    ) -> dict:
        page = self._store.list(offset=offset, limit=limit, status=status, order=order)
        for item in page["items"]:
            if item.get("output"):
                item["output_available"] = self._output_is_available(item["output"])
        return page

    @staticmethod
    def _output_is_available(output: str) -> bool:
        normalized = unquote(str(output)).strip().replace("\\", "/")
        prefix = "/outputs/"
        if not normalized.startswith(prefix):
            return False
        relative = normalized[len(prefix):]
        if not relative or ".." in relative.split("/"):
            return False
        root = WEB_OUTPUT_DIR.resolve()
        try:
            path = (WEB_OUTPUT_DIR / relative).resolve()
            path.relative_to(root)
        except ValueError:
            return False
        return path.is_file()

    def delete_work(self, work_id: str) -> bool:
        saved = self._store.get(work_id)
        fallback = None
        if saved and saved.get("status") == "done" and (saved.get("output") or "").startswith("/outputs/"):
            filename = unquote(saved["output"][len("/outputs/"):])
            path = (WEB_OUTPUT_DIR / filename).resolve()
            if path.parent == WEB_OUTPUT_DIR.resolve() and path.is_file():
                fallback = {"id": work_id, "filename": filename}
        elif work_id.startswith("file:"):
            filename = work_id[len("file:"):]
            path = (WEB_OUTPUT_DIR / filename).resolve()
            if path.parent == WEB_OUTPUT_DIR.resolve() and path.is_file() and path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}:
                fallback = {"id": work_id, "filename": filename}
        return self._library.delete(work_id, fallback=fallback)

    def _migrate_legacy_history(self) -> int:
        offset = 0
        imported = 0
        while True:
            page = self._library.list_videos(offset=offset, limit=100, order="asc")
            imported += self._store.import_completed(page["items"])
            if not page["has_more"]:
                return imported
            offset = page["next_offset"]

    def _run(self, job_id: str, prompt: dict, req: H3Request) -> None:
        from .config import OUTPUT_DIR
        from .process_lock import InterProcessLock
        try:
            lock_wait = 6 * 3600 if memory_chunk_rows(req) else 7200
            with InterProcessLock(OUTPUT_DIR / 'runtime/clone-gpu.lock', lock_wait):
                self._run_locked(job_id, prompt, req)
        except Exception as exc:
            self._update(job_id, status='error', message='等待GPU执行失败', error=str(exc))

    def _run_locked(self, job_id: str, prompt: dict, req: H3Request) -> None:
        if self._is_canceled(job_id):
            return
        self._update(
            job_id,
            status="running",
            message="正在准备 H3 生成任务",
            started_at=time(),
        )
        try:
            if req.mode == "references" and req.reference_model == "ref2va":
                model_path = COMFY_DIR / "models/diffusion_models" / REFERENCE_DIFFUSION_MODEL
                if not model_path.is_file():
                    from .clone_aux_runtime import prepare_auxiliary

                    self._update(job_id, message="正在补齐 Ref2VA 权重")
                    prepare_auxiliary(
                        ROOT_DIR, reference=True, comfy_dir=COMFY_DIR,
                        emit=lambda percent, message: self._update(job_id, progress=percent, message=message),
                        canceled=lambda: self._is_canceled(job_id),
                    )
                    if self._is_canceled(job_id):
                        return
                    self._update(job_id, progress=0, message="Ref2VA 权重已就绪，正在启动生成")
            with self._lock:
                selected_mode = self._jobs[job_id].acceleration_mode
                selected_nsfw = bool(self._jobs[job_id].nsfw_enabled)
                selected_nsfw_profile = self._jobs[job_id].nsfw_profile
            trial_prompt = prompt
            if memory_chunk_rows(req) and self._comfy.probe().get("ready") is True:
                self._comfy.free_memory()
            tail_seconds = self._store.generation_tail_seconds(asdict(self._jobs[job_id]))
            while True:
                try:
                    output = self._comfy.run_prompt(
                        trial_prompt,
                        timeout_seconds=6 * 3600 if memory_chunk_rows(req) else 7200,
                        expected_acceleration_mode=selected_mode,
                        expected_nsfw_enabled=selected_nsfw,
                        expected_nsfw_profile=selected_nsfw_profile,
                        on_submitted=lambda prompt_id: self._on_prompt_submitted(job_id, prompt_id),
                        on_progress=lambda percent, message: self._update(job_id, progress=percent, message=message),
                        on_timing=lambda timing, reference_model=req.reference_model: self._update(
                            job_id, sampling_timing={**timing, "reference_model": reference_model},
                        ),
                        tail_seconds=tail_seconds,
                    )
                    break
                except ComfyError as exc:
                    if self._is_canceled(job_id):
                        raise
                    if not any(marker in str(exc).lower() for marker in ("out of memory", "outofmemoryerror")):
                        raise
                    current_rows = trial_prompt.get("503", {}).get("inputs", {}).get("chunk_rows")
                    if current_rows is None:
                        # 大显存机器遇到竞争或峰值时，也能切入保守路径。
                        req = replace(req, memory_chunk_rows=1024)
                        selected_mode = 'turbo_8step' if selected_mode.startswith('turbo_8step') else 'standard'
                    elif current_rows > 256:
                        req = replace(req, memory_chunk_rows=current_rows // 2)
                    else:
                        lower = smaller_resolution(req.resolution)
                        if lower is None:
                            raise ComfyError("显存保护已达到最小同画幅分辨率与分块；请缩短视频或释放其他程序的显存后重试") from exc
                        req = replace(req, resolution=lower, memory_chunk_rows=256)
                    trial_prompt = build_prompt(
                        req, acceleration_mode=selected_mode,
                        nsfw_enabled=selected_nsfw, nsfw_profile=selected_nsfw_profile,
                    )
                    with self._lock:
                        original_resolution = self._jobs[job_id].requested_resolution or self._jobs[job_id].resolution or req.resolution
                    adjustment = (f"显存保护：已从 {original_resolution} 调整为 {req.resolution}，保留时长"
                                  if original_resolution != req.resolution else "")
                    self._update(
                        job_id, progress=0, sampling_timing=None,
                        resolution=req.resolution, acceleration_mode=selected_mode,
                        requested_resolution=original_resolution, resolution_adjustment=adjustment,
                        message=f"显存不足，释放缓存后以 {req.memory_chunk_rows} 行分块重试；{req.resolution}",
                    )
                    tail_seconds = None
                    try:
                        self._comfy.free_memory()
                    except ComfyError:
                        logging.exception("Could not free ComfyUI memory before OOM retry")
                    if self._is_canceled(job_id):
                        return
            if self._is_canceled(job_id):
                return
            output = promote_generated_video(Path(output), WEB_OUTPUT_DIR)
            output_url = f"/outputs/{Path(output).name}"
            completed_at = time()
            with self._lock:
                job = self._jobs[job_id]
                created_at = job.created_at
                started_at = job.started_at or completed_at
            elapsed_seconds = max(0, completed_at - started_at)
            self._library.add(
                WorkItem(
                    id=job_id,
                    output=output_url,
                    filename=Path(output).name,
                    prompt=req.prompt,
                    resolution=req.resolution,
                    duration=req.duration,
                    steps=req.steps,
                    seed=req.seed,
                    mode=req.mode,
                    first_frame=req.first_frame,
                    last_frame=req.last_frame,
                    reference_images=list(req.reference_images),
                    reference_videos=list(req.reference_videos),
                    reference_audios=list(req.reference_audios),
                    created_at=created_at,
                    completed_at=completed_at,
                    elapsed_seconds=elapsed_seconds,
                    user_brief=job.user_brief,
                    prompt_plan=job.prompt_plan,
                )
            )
            self._update(
                job_id,
                status="done",
                message="生成完成" + (f"；{job.resolution_adjustment}" if job.resolution_adjustment else ""),
                output=output_url,
                completed_at=completed_at,
                elapsed_seconds=elapsed_seconds,
            )
        except Exception as exc:
            if self._is_canceled(job_id):
                return
            completed_at = time()
            with self._lock:
                started_at = self._jobs[job_id].started_at
            self._update(
                job_id,
                status="error",
                message="生成失败",
                error=f"{exc}\n{traceback.format_exc()}",
                completed_at=completed_at,
                elapsed_seconds=max(0, completed_at - started_at) if started_at else None,
            )
        finally:
            # 仍持有跨进程 GPU 锁和单线程执行器，清理完成后下一条才会进入。
            if job_id in self._prompt_ids and free_gpu_cache_after_task():
                try:
                    self._comfy.release_cache_between_jobs()
                except Exception:
                    logging.exception("H3 cache cleanup failed for job %s", job_id)
            with self._lock:
                self._prompt_ids.pop(job_id, None)

    def _on_prompt_submitted(self, job_id: str, prompt_id: str) -> None:
        with self._lock:
            self._prompt_ids[job_id] = prompt_id
            canceled = self._jobs[job_id].status == "canceled"
        if canceled:
            self._comfy.cancel_prompt(prompt_id)
            return
        self._update(job_id, message="H3 已接收任务，正在生成视频")

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status == "canceled" and changes.get("status") != "canceled":
                return
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = time()
            record = asdict(job)
        self._store.save(record)

    def clear_failed_jobs(self) -> list[str]:
        missing_output_ids = [
            job_id
            for job_id, output in self._store.completed_outputs()
            if not self._output_is_available(output)
        ]
        with self._lock:
            deleted = self._store.clear_failed(missing_output_ids)
            for job_id in deleted:
                self._jobs.pop(job_id, None)
        return deleted

    def _is_canceled(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.get(job_id, None) is not None and self._jobs[job_id].status == "canceled"


def _timing_warning(status: str, run_seconds: float | None) -> dict | None:
    if status == "running" and run_seconds is not None and run_seconds >= LONG_RUN_SECONDS:
        return {
            "level": "slow",
            "kind": "long_running",
            "message": "当前任务已超出常规生成耗时，但 H3 仍可能最终完成，请结合 GPU 占用和 ComfyUI 日志判断",
        }
    return None


def _acceleration_label(mode: str | None) -> str:
    return {
        "standard": "标准",
        "sage_attention": "SageAttention",
        "turbo_8step": "Turbo 8-step LoRA",
        "turbo_8step_sage": "Turbo 8-step + SageAttention",
    }.get(mode or "", "标准")


def _job_from_record(record: dict) -> Job:
    values = {item.name: record.get(item.name) for item in fields(Job)}
    values["reference_images"] = tuple(values.get("reference_images") or ())
    values["reference_videos"] = tuple(values.get("reference_videos") or ())
    values["reference_audios"] = tuple(values.get("reference_audios") or ())
    return Job(**values)
