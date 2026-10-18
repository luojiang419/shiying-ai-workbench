from __future__ import annotations

import json
import logging
import os
import socket
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Callable

import psutil

from .config import (
    ACCELERATION_MODES,
    AFTERMIDNIGHT_PROFILE,
    COMFY_DIR,
    COMFY_HOST,
    COMFY_OUTPUT_DIR,
    COMFY_PORT,
    HF_ENDPOINT,
    HERETIC_TEXT_ENCODER_MODEL,
    LOG_DIR,
    NAUGHTYTIMES_LORA_MODEL,
    NAUGHTYTIMES_PROFILE,
    NAUGHTYTIMES_PROFILES,
    NAUGHTYTIMES_STANDARD_PROFILE,
    NSFW_LORA_MODEL,
    NSFW_PROFILES,
    ROOT_DIR,
    RUNTIME_DIR,
    SAGE_ATTENTION_NODE_CLASS,
    TURBO_LORA_MODEL,
    TURBO_LORA_NODE_CLASS,
    UNBOUNDED_SCHEDULER_NODE_CLASS,
    WEB_OUTPUT_DIR,
    acceleration_mode as get_acceleration_mode,
    comfy_vram_reserve_gb,
    nsfw_enabled as get_nsfw_enabled,
    nsfw_profile as get_nsfw_profile,
)
from .acceleration import validate_acceleration_prompt
from .process_lock import InterProcessLock
from .comfy_safety import install_input_safety


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self) -> None:
        self.base_url = f"http://{COMFY_HOST}:{COMFY_PORT}"
        self._process: subprocess.Popen | None = None
        self._lock = Lock()
        self._startup_lock = Lock()
        self._node_info_lock = Lock()
        self._node_info_cache = None
        self._node_info_loaded_at = 0.0
        self._dependency_lock = Lock()
        self._dependencies_prepared = False
        self._dependency_future: Future | None = None
        self._startup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="comfy-start")
        self._startup_future: Future | None = None
        self._startup_attempt = 0
        self._startup_state = {
            "state": "stopped",
            "message": "ComfyUI 尚未启动",
            "attempt": 0,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "h3_nodes": None,
        }

    def request_start(self) -> dict:
        with self._startup_lock:
            if self._startup_future is not None and not self._startup_future.done():
                return dict(self._startup_state)

            self._startup_attempt += 1
            self._startup_state = {
                "state": "starting",
                "message": "正在确认 ComfyUI 单实例并启动后端",
                "attempt": self._startup_attempt,
                "started_at": time.time(),
                "completed_at": None,
                "error": None,
                "h3_nodes": None,
            }
            self._startup_future = self._startup_executor.submit(self._start_in_background)
            return dict(self._startup_state)

    def startup_status(self) -> dict:
        with self._startup_lock:
            return dict(self._startup_state)

    def _start_in_background(self) -> None:
        try:
            self.ensure_running()
            selected_mode = get_acceleration_mode()
            self._update_startup_state(
                state="checking_nodes",
                message=f"ComfyUI 已连接，正在检查 H3 与{_acceleration_label(selected_mode)}节点",
            )
            node_check = self.check_h3_nodes(require_memory_optimization=True)
            if not node_check["ok"]:
                raise ComfyError("当前 ComfyUI 缺少 H3 节点: " + ", ".join(node_check["missing"]))
            self._update_startup_state(
                state="ready",
                message=f"ComfyUI 已启动，H3 与{_acceleration_label(node_check.get('acceleration_mode', selected_mode))}节点可用",
                completed_at=time.time(),
                h3_nodes=node_check,
            )
        except Exception as exc:
            self._update_startup_state(
                state="failed",
                message="ComfyUI 启动失败",
                completed_at=time.time(),
                error=str(exc),
            )

    def _update_startup_state(self, **changes) -> None:
        with self._startup_lock:
            self._startup_state.update(changes)

    def request_dependency_prepare(self) -> None:
        """Web 服务启动后立即补齐，关闭模型预启动时也执行，但不加载模型。"""
        with self._startup_lock:
            if self._dependency_future is None:
                self._dependency_future = self._startup_executor.submit(self._prepare_only_in_background)

    def _prepare_only_in_background(self) -> None:
        try:
            self._prepare_dependencies()
            with self._startup_lock:
                if self._startup_future is None:
                    self._startup_state.update(state="stopped", message="本地 H3 依赖已就绪，等待启动生成后端")
        except Exception as exc:
            self._update_startup_state(state="failed", message="H3 依赖补齐失败，可点击重试后端", error=str(exc))

    def _prepare_dependencies(self) -> None:
        if COMFY_HOST not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            return
        with self._dependency_lock:
            if self._dependencies_prepared:
                return
            from .h3_dependencies import prepare_h3_dependencies

            def emit(percent, message):
                self._update_startup_state(state="preparing_dependencies", message=message,
                                          dependency_progress=percent)

            def before_install():
                if not _is_tcp_port_in_use(COMFY_HOST, COMFY_PORT):
                    return
                queue = self._get_json("/queue", timeout=5)
                if queue.get("queue_running") or queue.get("queue_pending"):
                    raise ComfyError("依赖已下载；生成后端仍有任务，请等待完成后重试补齐")
                self._stop_exclusive_local_backend()

            prepare_h3_dependencies(ROOT_DIR, COMFY_DIR, emit, before_install)
            self._dependencies_prepared = True
            self._update_startup_state(state="starting", message="H3 依赖已就绪，正在连接生成后端", error=None)

    def ensure_running(self) -> None:
        self._prepare_dependencies()
        if self._is_ready():
            return

        with self._lock:
            try:
                with InterProcessLock(RUNTIME_DIR / "comfy-start.lock", timeout_seconds=185):
                    self._ensure_running_locked()
            except TimeoutError as exc:
                raise ComfyError("等待其他进程启动 ComfyUI 超时，请查看后端状态") from exc

    def prepare_image_models(self, emit=lambda *_: None) -> None:
        if COMFY_HOST not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            return
        from .qwen_dependencies import prepare_qwen_models
        prepare_qwen_models(ROOT_DIR, COMFY_DIR, emit)
        with self._node_info_lock:
            self._node_info_cache = None

    def prepare_image_backend(self, emit=lambda *_: None) -> None:
        """首次图片任务安装定向兼容包；此前视频仍使用原来的 H3 稳定依赖。"""
        if COMFY_HOST not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or COMFY_DIR != ROOT_DIR / "vendor/ComfyUI":
            return
        from .h3_dependencies import load_manifest, prepare_h3_dependencies
        manifest_name = "scripts/qwen21-dependencies.json"
        with self._lock, self._dependency_lock:
            def before_install():
                if not _is_tcp_port_in_use(COMFY_HOST, COMFY_PORT):
                    return
                queue = self._get_json("/queue", timeout=5)
                if queue.get("queue_running") or queue.get("queue_pending"):
                    raise ComfyError("图片兼容依赖已准备；后端仍有任务，请完成后重试")
                self._stop_exclusive_local_backend()
            prepare_h3_dependencies(ROOT_DIR, COMFY_DIR, emit, before_install, manifest_name)
            marker = RUNTIME_DIR / "qwen-compat-active.json"
            marker.parent.mkdir(parents=True, exist_ok=True)
            temporary = marker.with_suffix(".tmp")
            temporary.write_text(json.dumps({"version": load_manifest(ROOT_DIR, manifest_name)["version"]}), encoding="utf-8")
            temporary.replace(marker)
            self._dependencies_prepared = True
            with self._node_info_lock:
                self._node_info_cache = None

    def _ensure_running_locked(self) -> None:
        global COMFY_PORT
        if self._is_ready():
            return

        if self._process is not None and self._process.poll() is None:
            self._wait_until_ready(self._process)
            return

        if _is_tcp_port_in_use(COMFY_HOST, COMFY_PORT):
            from .service_endpoints import (
                owned_listener,
                read_endpoints,
                save_endpoints,
                select_comfy_port,
            )
            if COMFY_HOST not in {"localhost", "127.0.0.1", "0.0.0.0"} or owned_listener(ROOT_DIR, COMFY_PORT, "comfy"):
                self._wait_until_ready()
                return
            COMFY_PORT = select_comfy_port(ROOT_DIR, COMFY_HOST, COMFY_PORT)
            self.base_url = f"http://{COMFY_HOST}:{COMFY_PORT}"
            endpoints = read_endpoints(ROOT_DIR)
            if endpoints.get("web_port"):
                save_endpoints(ROOT_DIR, endpoints["web_port"], COMFY_PORT)

        if not (COMFY_DIR / "main.py").exists():
            raise ComfyError("未找到 vendor/ComfyUI/main.py，请先运行 scripts/prepare_env.ps1")

        _install_h3_diagnostics()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        COMFY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        log_file = open(LOG_DIR / "comfyui.log", "a", encoding="utf-8")
        env = _clean_network_env()
        command = _build_comfy_command()
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(COMFY_DIR),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        finally:
            log_file.close()
        self._wait_until_ready(self._process)

    def _wait_until_ready(self, process: subprocess.Popen | None = None) -> None:
        deadline = time.time() + 180
        while time.time() < deadline:
            if self._is_ready():
                return
            if process is not None and process.poll() is not None:
                raise ComfyError("ComfyUI 启动失败，请查看 logs/comfyui.log")
            time.sleep(2)
        if process is None:
            raise ComfyError("ComfyUI 端口已被占用但未响应；为避免重复实例，未再次启动")
        raise ComfyError("ComfyUI 启动超时，请查看 logs/comfyui.log")

    def probe(self) -> dict:
        try:
            self._get_json("/system_stats", timeout=3)
            return {"ready": True, "state": "ready", "message": "ComfyUI 已就绪"}
        except (TimeoutError, socket.timeout):
            return {"ready": False, "state": "busy", "message": "ComfyUI 响应超时，可能正在生成或加载模型"}
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return {"ready": False, "state": "busy", "message": "ComfyUI 响应超时，可能正在生成或加载模型"}
            return {"ready": False, "state": "offline", "message": f"ComfyUI 未连接: {reason or exc}"}
        except OSError as exc:
            return {"ready": False, "state": "offline", "message": f"ComfyUI 未连接: {exc}"}
        except Exception as exc:
            return {"ready": False, "state": "unknown", "message": str(exc)}

    def check_h3_nodes(
        self,
        require_sage_attention: bool | None = None,
        acceleration_mode: str | None = None,
        nsfw_enabled: bool | None = None,
        nsfw_profile: str | None = None,
        max_age: float = 0,
        require_low_vram_depth: bool = False,
        require_memory_optimization: bool = False,
        require_depth_control: bool = False,
    ) -> dict:
        # 概览轮询复用节点目录；真正提交任务默认 max_age=0，仍做实时检查。
        if max_age <= 0:
            info = self._get_json("/object_info")
        else:
            with self._node_info_lock:
                if self._node_info_cache is None or time.monotonic() - self._node_info_loaded_at >= max_age:
                    self._node_info_cache = self._get_json("/object_info")
                    self._node_info_loaded_at = time.monotonic()
                info = self._node_info_cache
        required = [
            "MiniMaxH3ImageToVideo",
            "MiniMaxH3ReferenceToVideo",
            "UNETLoader",
            "CLIPLoader",
            "VAELoader",
            "VAEDecodeAudio",
            "CreateVideo",
            "SaveVideo",
            UNBOUNDED_SCHEDULER_NODE_CLASS,
        ]
        if acceleration_mode is None:
            if require_sage_attention is not None:
                acceleration_mode = "sage_attention" if require_sage_attention else "standard"
            else:
                acceleration_mode = get_acceleration_mode()
        if acceleration_mode not in ACCELERATION_MODES:
            raise ValueError(f"不支持的生成加速方案: {acceleration_mode}")
        if nsfw_enabled is None:
            nsfw_enabled = get_nsfw_enabled()
        if nsfw_enabled and nsfw_profile is None:
            nsfw_profile = get_nsfw_profile()
        active_nsfw_profile = nsfw_profile if nsfw_enabled else None
        if active_nsfw_profile is not None and active_nsfw_profile not in NSFW_PROFILES:
            raise ValueError(f"不支持的 NSFW 方案: {active_nsfw_profile}")
        require_sage_attention = acceleration_mode in {"sage_attention", "turbo_8step_sage"}
        missing = []
        turbo_lora_ready = True
        nsfw_lora_ready = True
        heretic_text_encoder_ready = True
        if acceleration_mode in {"turbo_8step", "turbo_8step_sage"} or active_nsfw_profile:
            required.append(TURBO_LORA_NODE_CLASS)
            # Check the backend's actual model list, not just a local file that
            # may belong to a different ComfyUI installation.
            lora_names = info.get(TURBO_LORA_NODE_CLASS, {}).get("input", {}).get("required", {}).get("lora_name", [[]])[0]
        if acceleration_mode in {"turbo_8step", "turbo_8step_sage"}:
            turbo_lora_ready = TURBO_LORA_MODEL in lora_names
            if not turbo_lora_ready:
                missing.append(f"Turbo LoRA 模型: {TURBO_LORA_MODEL}")
        if active_nsfw_profile:
            expected_nsfw_lora = (
                NAUGHTYTIMES_LORA_MODEL
                if active_nsfw_profile in NAUGHTYTIMES_PROFILES
                else NSFW_LORA_MODEL
            )
            nsfw_lora_ready = expected_nsfw_lora in lora_names
            if not nsfw_lora_ready:
                missing.append(f"NSFW LoRA 模型: {expected_nsfw_lora}")
        if active_nsfw_profile == NAUGHTYTIMES_PROFILE:
            clip_names = info.get("CLIPLoader", {}).get("input", {}).get("required", {}).get("clip_name", [[]])[0]
            heretic_text_encoder_ready = HERETIC_TEXT_ENCODER_MODEL in clip_names
            if not heretic_text_encoder_ready:
                missing.append(f"Heretic 文本编码器: {HERETIC_TEXT_ENCODER_MODEL}")
        if require_sage_attention:
            required.append(SAGE_ATTENTION_NODE_CLASS)
        if require_low_vram_depth or require_memory_optimization:
            required.extend([
                "H3MemoryOptimization",
                "H3AIMDOResidencyLimiter",
            ])
        if require_low_vram_depth or require_depth_control:
            required.append("MiniMaxCloneDepthControlApply")
        missing.extend(name for name in required if name not in info)
        return {
            "ok": not missing,
            "missing": missing,
            "acceleration_mode": acceleration_mode,
            "sage_attention_enabled": require_sage_attention,
            "sage_attention_ready": SAGE_ATTENTION_NODE_CLASS in info,
            "unbounded_scheduler_ready": UNBOUNDED_SCHEDULER_NODE_CLASS in info,
            "turbo_lora_ready": turbo_lora_ready,
            "nsfw_enabled": active_nsfw_profile is not None,
            "nsfw_profile": active_nsfw_profile,
            "nsfw_lora_ready": nsfw_lora_ready,
            "heretic_text_encoder_ready": heretic_text_encoder_ready,
        }

    def run_prompt(
        self,
        prompt: dict,
        timeout_seconds: int = 7200,
        *,
        expected_acceleration_mode: str | None = None,
        expected_nsfw_enabled: bool = False,
        expected_nsfw_profile: str | None = None,
        on_submitted: Callable[[str], None] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        on_timing: Callable[[dict], None] | None = None,
        tail_seconds: float | None = None,
    ) -> Path:
        if expected_nsfw_enabled and expected_nsfw_profile is None:
            expected_nsfw_profile = AFTERMIDNIGHT_PROFILE
        if expected_acceleration_mode is not None:
            validate_acceleration_prompt(
                prompt,
                expected_acceleration_mode,
                nsfw_enabled=expected_nsfw_enabled,
                nsfw_profile=expected_nsfw_profile,
            )
        self.ensure_running()
        self._ensure_generation_budget()
        prompt_uses_sage_attention = any(
            node.get("class_type") == SAGE_ATTENTION_NODE_CLASS for node in prompt.values()
        )
        prompt_uses_turbo = any(
            node.get("class_type") == TURBO_LORA_NODE_CLASS
            and node.get("inputs", {}).get("lora_name") == TURBO_LORA_MODEL
            for node in prompt.values()
        )
        prompt_uses_heretic = any(
            node.get("class_type") == "CLIPLoader"
            and node.get("inputs", {}).get("clip_name") == HERETIC_TEXT_ENCODER_MODEL
            for node in prompt.values()
        )
        prompt_nsfw_profile = next(
            (
                AFTERMIDNIGHT_PROFILE
                if node.get("inputs", {}).get("lora_name") == NSFW_LORA_MODEL
                else expected_nsfw_profile
                if expected_nsfw_profile in NAUGHTYTIMES_PROFILES
                else NAUGHTYTIMES_PROFILE
                if prompt_uses_heretic
                else NAUGHTYTIMES_STANDARD_PROFILE
                for node in prompt.values()
                if node.get("class_type") == TURBO_LORA_NODE_CLASS
                and node.get("inputs", {}).get("lora_name")
                in {NSFW_LORA_MODEL, NAUGHTYTIMES_LORA_MODEL}
            ),
            None,
        )
        selected_mode = (
            "turbo_8step_sage"
            if prompt_uses_turbo and prompt_uses_sage_attention
            else "turbo_8step"
            if prompt_uses_turbo
            else "sage_attention"
            if prompt_uses_sage_attention
            else "standard"
        )
        selected_mode = expected_acceleration_mode or selected_mode
        node_check = self.check_h3_nodes(
            acceleration_mode=selected_mode,
            nsfw_enabled=bool(expected_nsfw_profile or prompt_nsfw_profile),
            nsfw_profile=expected_nsfw_profile or prompt_nsfw_profile,
            require_memory_optimization=any(
                node.get("class_type") == "H3MemoryOptimization"
                for node in prompt.values()
            ),
            require_depth_control=any(
                node.get("class_type") == "MiniMaxCloneDepthControlApply"
                for node in prompt.values()
            ),
        )
        if not node_check["ok"]:
            raise ComfyError("当前 ComfyUI 缺少 H3 节点: " + ", ".join(node_check["missing"]))

        started_at = time.time()
        client_id = str(uuid.uuid4())
        observer = None
        if on_progress is not None or on_timing is not None:
            from .comfy_progress import ProgressObserver
            observer = ProgressObserver(
                self.base_url.replace("http", "ws", 1) + "/ws?clientId=" + client_id,
                on_progress or (lambda *_: None), on_timing=on_timing, tail_seconds=tail_seconds,
            )
        try:
            payload = json.dumps({"prompt": prompt, "client_id": client_id}).encode("utf-8")
            response = self._post_json("/prompt", payload)
            prompt_id = response.get("prompt_id")
            if not prompt_id:
                raise ComfyError(f"ComfyUI 未返回 prompt_id: {response}")
            logging.info(
                "H3 submitted: prompt_id=%s acceleration_mode=%s turbo_lora=%s nsfw_profile=%s",
                prompt_id,
                selected_mode,
                TURBO_LORA_MODEL if prompt_uses_turbo else "none",
                prompt_nsfw_profile or "none",
            )
            if observer is not None:
                observer.prompt_id = prompt_id
            if on_submitted is not None:
                on_submitted(prompt_id)

            deadline = time.time() + timeout_seconds
            # ComfyUI 重启或内部校验失败后，prompt 可能既不在队列也不在
            # history；旧逻辑会继续等待到 7200 秒，永久占住 Web 的单线程
            # executor，后续任务只能排队且 GPU 保持空闲。允许短暂的队列
            # 刷新窗口，随后明确失败并释放执行器。
            last_prompt_seen_at = time.time()
            last_backend_response = time.monotonic()
            prompt_missing_timeout = min(90, max(30, timeout_seconds // 20))
            while time.time() < deadline:
                stage = observer.stalled_stage(
                    conditioning_seconds=_stall_limit("MINIMAX_H3_CONDITIONING_TIMEOUT_SECONDS", 600),
                    other_seconds=max(1800, timeout_seconds // 4),
                ) if observer is not None else None
                if stage or time.monotonic() - last_backend_response >= 120:
                    reason = stage or "ComfyUI 连续 120 秒没有响应"
                    self._abort_stalled_prompt(prompt_id)
                    final = self._get_json(f"/history/{prompt_id}").get(prompt_id, {})
                    if final.get("status", {}).get("status_str") == "success":
                        return self._copy_latest_video(prompt_id, started_at)
                    raise ComfyError(f"H3 执行停滞：{reason}；已中断异常任务，后续队列可继续")
                try:
                    history = self._get_json(f"/history/{prompt_id}")
                    last_backend_response = time.monotonic()
                except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
                    time.sleep(5)
                    continue
                if prompt_id in history:
                    if history[prompt_id].get("status", {}).get("status_str") == "error":
                        raise ComfyError(f"H3 生成失败: {history[prompt_id].get('status')}")
                    return self._copy_latest_video(prompt_id, started_at)

                try:
                    queue = self._get_json("/queue", timeout=5)
                    queued_ids = {
                        item[1]
                        for key in ("queue_running", "queue_pending")
                        for item in queue.get(key, [])
                        if isinstance(item, list) and len(item) > 1
                    }
                    if prompt_id in queued_ids:
                        last_prompt_seen_at = time.time()
                    elif time.time() - last_prompt_seen_at >= prompt_missing_timeout:
                        raise ComfyError(
                            "ComfyUI 未执行已提交的任务，可能刚刚重启或丢失队列；已释放任务执行器，请重试"
                        )
                except ComfyError:
                    raise
                except (TimeoutError, socket.timeout, urllib.error.URLError, OSError, ValueError):
                    pass
                time.sleep(5)
            self._abort_stalled_prompt(prompt_id)
            raise ComfyError("H3 生成超时")
        finally:
            if observer is not None:
                observer.close()

    def _abort_stalled_prompt(self, prompt_id: str) -> None:
        """精确中断；CUDA 不响应时只结束本项目独占的本机后端。"""
        try:
            self.cancel_prompt(prompt_id)
        except Exception:
            logging.exception("Could not interrupt stalled H3 prompt %s", prompt_id)
        deadline = time.monotonic() + 30
        queue = None
        while time.monotonic() < deadline:
            try:
                queue = self._get_json("/queue", timeout=5)
                ids = {item[1] for key in ("queue_running", "queue_pending")
                       for item in queue.get(key, [])}
                if prompt_id not in ids:
                    return
            except (OSError, urllib.error.URLError, ValueError):
                pass
            time.sleep(1)
        if queue and any(item[1] != prompt_id for key in ("queue_running", "queue_pending")
                         for item in queue.get(key, [])):
            raise ComfyError("异常任务未响应取消；ComfyUI 有其他任务，未自动重启，请停止后端后恢复")
        logging.warning("H3 prompt %s did not exit after targeted cancellation; recovering local backend", prompt_id)
        self._stop_exclusive_local_backend()
        self.ensure_running()

    def _stop_exclusive_local_backend(self) -> None:
        if COMFY_HOST not in {"127.0.0.1", "localhost", "::1"}:
            raise ComfyError("远端 ComfyUI 未响应取消，请在算力节点重启生成服务")
        expected = str((COMFY_DIR / "main.py").resolve()).lower()
        owners = {c.pid for c in psutil.net_connections(kind="tcp")
                  if c.status == psutil.CONN_LISTEN and c.laddr.port == COMFY_PORT and c.pid}
        if not owners:
            raise ComfyError("无法确认异常 ComfyUI 的进程身份，请停止生成服务后重试")
        processes = [psutil.Process(pid) for pid in owners]
        if any(expected not in [str(Path(arg).resolve()).lower() for arg in p.cmdline()]
               for p in processes):
            raise ComfyError("生成端口由其他程序占用，未自动结束，请检查节点服务")
        for process in processes:
            children = process.children(recursive=True)
            for child in children:
                child.terminate()
            process.terminate()
            _, alive = psutil.wait_procs([process, *children], timeout=5)
            for item in alive:
                item.kill()
            psutil.wait_procs(alive, timeout=5)
        self._process = None
        self._node_info_cache = None
        logging.info("Stopped project local ComfyUI on port %s", COMFY_PORT)

    def release_cache_between_jobs(self) -> dict:
        """等待清理图真正执行完成，避免 /free 标记与下一条任务的竞态。"""
        queue = self._get_json("/queue", timeout=5)
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise ComfyError("ComfyUI 尚有任务，不能执行任务间缓存清理")
        nonce = str(uuid.uuid4())
        payload = json.dumps({"prompt": {"h3_cache_release": {
            "class_type": "H3ReleaseBackendCache", "inputs": {"nonce": nonce},
        }}}).encode("utf-8")
        response = self._post_json("/prompt", payload)
        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise ComfyError("缓存清理未返回任务编号")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            record = self._get_json(f"/history/{prompt_id}", timeout=5).get(prompt_id)
            if record:
                if record.get("status", {}).get("status_str") != "success":
                    raise ComfyError("后端缓存清理执行失败")
                return {"ok": True, "message": "后端任务间缓存清理已完成"}
            time.sleep(0.2)
        self._abort_stalled_prompt(prompt_id)
        raise ComfyError("后端缓存清理超时，已恢复异常后端")

    def cancel_prompt(self, prompt_id: str) -> bool:
        """Cancel one running or pending ComfyUI prompt without affecting others."""
        if not prompt_id:
            return False
        payload = json.dumps({}).encode("utf-8")
        response = self._post_json(f"/api/jobs/{prompt_id}/cancel", payload)
        return bool(response.get("cancelled"))

    def _ensure_generation_budget(self) -> None:
        """Stop a new job when the configured office budget is already consumed."""
        reserve_gb = comfy_vram_reserve_gb()
        if reserve_gb <= 0:
            return

        free_gb = self._nvidia_free_gb()
        if free_gb is None or free_gb + 0.05 >= reserve_gb:
            return

        # DynamicVRAM can keep model weights staged in RAM while releasing its
        # own GPU cache. Give the backend a chance to do that before blaming
        # another application for the missing office budget.
        try:
            self.free_memory()
        except Exception:
            pass
        for _ in range(10):
            time.sleep(0.5)
            free_gb = self._nvidia_free_gb()
            if free_gb is None or free_gb + 0.05 >= reserve_gb:
                return

        if free_gb is not None and free_gb + 0.05 < reserve_gb:
            raise ComfyError(
                f"GPU 当前空闲显存约 {free_gb:.1f}GB，低于办公预留 {reserve_gb:.1f}GB；"
                "已暂停新任务，请关闭 GPU 占用程序或释放后端缓存后重试"
            )

    def _nvidia_free_gb(self) -> float | None:
        if not shutil.which("nvidia-smi"):
            return None
        command = [
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            free_mb = float(result.stdout.splitlines()[0].strip())
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return None
        return free_mb / 1024

    def free_memory(self) -> dict:
        payload = json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8")
        body = self._post("/free", payload)
        if not body:
            return {"ok": True, "message": "已请求 ComfyUI 释放模型和显存缓存"}
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return {"ok": True, "message": "已请求 ComfyUI 释放模型和显存缓存"}
        return {"ok": True, "message": "已请求 ComfyUI 释放模型和显存缓存", "response": data}

    def _copy_latest_video(self, prompt_id: str, started_at: float) -> Path:
        WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # 优先使用本任务history登记的文件，避免其他Comfy任务的新文件串入结果。
        history = self._get_json(f"/history/{prompt_id}").get(prompt_id, {})
        outputs = history.get("outputs", {})
        records = [outputs["92"]] if "92" in outputs else outputs.values()
        for record in records:
            for item in record.get("images", []) + record.get("videos", []):
                filename = item.get("filename", "")
                if item.get("type") != "output" or Path(filename).suffix.lower() not in {".mp4", ".webm", ".mov", ".mkv"}:
                    continue
                source = (COMFY_OUTPUT_DIR / item.get("subfolder", "") / filename).resolve()
                try:
                    source.relative_to(COMFY_OUTPUT_DIR.resolve())
                except ValueError as exc:
                    raise ComfyError("ComfyUI返回了无效视频路径") from exc
                if not source.is_file():
                    raise ComfyError("当前任务登记的视频文件不存在")
                target = WEB_OUTPUT_DIR / f"{prompt_id}{source.suffix}"
                shutil.copy2(source, target)
                return target
        candidates = []
        for suffix in ("*.mp4", "*.webm", "*.mov", "*.mkv"):
            candidates.extend(COMFY_OUTPUT_DIR.rglob(suffix))
        fresh = [p for p in candidates if p.stat().st_mtime >= started_at - 2]
        if not fresh:
            raise ComfyError("ComfyUI 已完成任务，但未找到视频输出文件")
        source = max(fresh, key=lambda p: p.stat().st_mtime)
        target = WEB_OUTPUT_DIR / f"{prompt_id}{source.suffix}"
        shutil.copy2(source, target)
        return target

    def _is_ready(self) -> bool:
        try:
            self._get_json("/system_stats", timeout=3)
            return True
        except Exception:
            return False

    def _get_json(self, path: str, timeout: int = 20) -> dict:
        with _backend_opener().open(self.base_url + path, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path: str, payload: bytes, timeout: int = 60) -> dict:
        body = self._post(path, payload, timeout)
        return json.loads(body.decode("utf-8"))

    def _post(self, path: str, payload: bytes, timeout: int = 60) -> bytes:
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _backend_opener().open(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                error_data = json.loads(detail)
                for node in error_data.get("node_errors", {}).values():
                    for error in node.get("errors", []):
                        extra = error.get("extra_info", {})
                        if error.get("type") == "value_not_in_list" and extra.get("input_name") == "unet_name":
                            model = extra.get("received_value", "所选权重")
                            raise ComfyError(
                                f"当前 ComfyUI 未识别权重 {model}；请确认模型位于实际运行的 ComfyUI/models/diffusion_models 目录，并重启生成服务"
                            ) from exc
            except (ValueError, AttributeError, TypeError):
                pass
            raise ComfyError(f"ComfyUI 请求失败: {detail}") from exc


def _backend_opener():
    # 本机生成服务不经过系统 HTTP 代理，避免代理断开被误判为后端故障。
    if COMFY_HOST in {"127.0.0.1", "localhost", "::1"}:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _stall_limit(name: str, default: int) -> int:
    try:
        return max(60, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _install_h3_diagnostics() -> None:
    install_input_safety(COMFY_DIR)
    source = Path(__file__).resolve().parents[1] / "comfy_extensions/h3_generation_diagnostics/__init__.py"
    target = COMFY_DIR / "custom_nodes/h3_generation_diagnostics/__init__.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != source.read_bytes():
        shutil.copy2(source, target)


def _clean_network_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]:
        env.pop(key, None)
    env["HF_ENDPOINT"] = HF_ENDPOINT
    env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    from .h3_dependencies import backend_python_path
    overlay = backend_python_path(ROOT_DIR)
    if overlay:
        env["PYTHONPATH"] = str(overlay)
    return env


def _build_comfy_command() -> list[str]:
    command = [
        os.environ.get('MINIMAX_COMFY_PYTHON', sys.executable),
        str(COMFY_DIR / "main.py"),
        "--listen",
        COMFY_HOST,
        "--port",
        str(COMFY_PORT),
        "--lowvram",
        "--disable-auto-launch",
        "--output-directory",
        str(COMFY_OUTPUT_DIR),
    ]
    vram_reserve_gb = comfy_vram_reserve_gb()
    if vram_reserve_gb > 0:
        command.extend(["--reserve-vram", _format_cli_float(vram_reserve_gb)])
        if os.name == 'nt':
            command.extend(["--vram-headroom", _format_cli_float(vram_reserve_gb)])
    return command


def _format_cli_float(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _acceleration_label(mode: str) -> str:
    return {
        "standard": "标准",
        "sage_attention": "SageAttention ",
        "turbo_8step": "Turbo 8-step LoRA ",
        "turbo_8step_sage": "Turbo 8-step + SageAttention ",
    }.get(mode, "加速 ")


def _is_tcp_port_in_use(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        pass

    try:
        for connection in psutil.net_connections(kind="tcp"):
            if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                continue
            if connection.laddr.port == port:
                return True
    except (psutil.AccessDenied, psutil.Error, OSError):
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((host, port))
        return False
    except OSError:
        return True
