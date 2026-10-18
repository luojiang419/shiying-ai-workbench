from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from .service_endpoints import select_comfy_port


def _string_env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _port_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    try:
        port = int(value) if value and value.strip() else default
    except ValueError:
        return default
    return port if 1 <= port <= 65535 else default


def _path_env(name: str, default: Path) -> Path:
    return Path(_string_env(name, str(default))).expanduser().resolve()


ROOT_DIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT_DIR / "vendor"
COMFY_DIR = _path_env("MINIMAX_COMFY_DIR", VENDOR_DIR / "ComfyUI")
WEB_DIR = ROOT_DIR / "web"
OUTPUT_DIR = ROOT_DIR / "outputs"
COMFY_OUTPUT_DIR = _path_env("MINIMAX_COMFY_OUTPUT_DIR", OUTPUT_DIR / "comfy")
WEB_OUTPUT_DIR = OUTPUT_DIR / "web"
WORK_LIBRARY_FILE = OUTPUT_DIR / "works.json"
JOB_DATABASE_FILE = _path_env("MINIMAX_JOB_DATABASE_FILE", OUTPUT_DIR / "jobs.sqlite3")
LOG_DIR = ROOT_DIR / "logs"
RUNTIME_DIR = OUTPUT_DIR / "runtime"
COMFY_INPUT_DIR = _path_env("MINIMAX_COMFY_INPUT_DIR", COMFY_DIR / "input")
CONTROL_PANEL_SETTINGS_FILE = ROOT_DIR / "config" / "control-panel-settings.json"
ASSISTANT_PROVIDERS_FILE = ROOT_DIR / "config" / "assistant-providers.json"

COMFY_HOST = _string_env("MINIMAX_COMFY_HOST", "127.0.0.1")
COMFY_PORT = _port_env("MINIMAX_COMFY_PORT", 8188)
COMFY_PORT = select_comfy_port(ROOT_DIR, COMFY_HOST, COMFY_PORT)
WEB_HOST = _string_env("MINIMAX_WEB_HOST", "127.0.0.1")
WEB_PORT = _port_env("MINIMAX_WEB_PORT", 7860)

HF_ENDPOINT = "https://hf-mirror.com"
PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_COMFY_VRAM_HEADROOM_GB = 2.0
DEFAULT_COMFY_VRAM_RESERVE_GB = 2.0

DIFFUSION_MODEL = _string_env(
    "MINIMAX_DIFFUSION_MODEL", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
)
REFERENCE_DIFFUSION_MODEL = _string_env(
    "MINIMAX_REFERENCE_DIFFUSION_MODEL", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
)
TEXT_ENCODER_MODEL = _string_env(
    "MINIMAX_TEXT_ENCODER_MODEL", "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
)
VIDEO_VAE_MODEL = _string_env(
    "MINIMAX_VIDEO_VAE_MODEL", "minimax_h3_video_vae_fp16.safetensors"
)
AUDIO_VAE_MODEL = _string_env(
    "MINIMAX_AUDIO_VAE_MODEL", "minimax_h3_audio_vae_fp32.safetensors"
)
SAGE_ATTENTION_NODE_CLASS = "PathchSageAttentionKJ"
UNBOUNDED_SCHEDULER_NODE_CLASS = "MiniMaxUnboundedBasicScheduler"
TURBO_LORA_NODE_CLASS = "LoraLoaderModelOnly"
TURBO_LORA_MODEL = _string_env(
    "MINIMAX_TURBO_LORA_MODEL",
    "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
)
NSFW_LORA_MODEL = _string_env(
    "MINIMAX_NSFW_LORA_MODEL",
    "AfterMidnight_ref2va_h3_sexytime_rank64-v1.2.safetensors",
)
NSFW_LORA_REVISION = "4b325f60229c136b97501f5830bb277aace738b6"
NSFW_LORA_SHA256 = "82226a7c7f0b4631092f9270fa33d078c985a2d757895fcbe8f3fca8881bef59"
AFTERMIDNIGHT_PROFILE = "aftermidnight"
NAUGHTYTIMES_PROFILE = "naughtytimes"
NAUGHTYTIMES_STANDARD_PROFILE = "naughtytimes_standard"
NAUGHTYTIMES_PROFILES = (NAUGHTYTIMES_STANDARD_PROFILE, NAUGHTYTIMES_PROFILE)
NSFW_PROFILES = (AFTERMIDNIGHT_PROFILE, *NAUGHTYTIMES_PROFILES)
DEFAULT_NSFW_PROFILE = AFTERMIDNIGHT_PROFILE
NAUGHTYTIMES_LORA_MODEL = _string_env(
    "MINIMAX_NAUGHTYTIMES_LORA_MODEL",
    "SexGod_NaughtyTimes_v3_rank64_pruned_NOADALN.safetensors",
)
NAUGHTYTIMES_LORA_STRENGTH = 0.5
NAUGHTYTIMES_LORA_REVISION = "cc027ae9e393578cf646f228d37c29b81bbf6cce"
NAUGHTYTIMES_LORA_SHA256 = "22466f81d4dc6a990e810aa2a57edf579015acb8ffc15e3f562ec21e82f9d0dd"
HERETIC_TEXT_ENCODER_MODEL = _string_env(
    "MINIMAX_HERETIC_TEXT_ENCODER_MODEL",
    "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors",
)
HERETIC_TEXT_ENCODER_REVISION = "2814607c9e6034e2cf2c76da82f996d179567551"
HERETIC_TEXT_ENCODER_SHA256 = "a166c7bbbe66a22065159e478335fee4a633c4a3e3bb34c8e8ac4cc91bf4996f"
ACCELERATION_MODES = ("standard", "sage_attention", "turbo_8step", "turbo_8step_sage")
DEFAULT_ACCELERATION_MODE = "sage_attention"


def comfy_vram_headroom_gb(settings_file: Path | None = None) -> float:
    """Backward-compatible alias for the control-panel VRAM reserve setting."""
    return comfy_vram_reserve_gb(settings_file)


def free_gpu_cache_after_task() -> bool:
    data = _read_settings(CONTROL_PANEL_SETTINGS_FILE) or {}
    return data.get("free_gpu_cache_after_task", False) is True


def comfy_vram_reserve_gb(settings_file: Path | None = None) -> float:
    """Return the VRAM budget reserved for the OS and other applications.

    The old setting/environment names are accepted so existing installations
    keep their value after upgrading, but the value is now consumed as
    ComfyUI's ``--reserve-vram`` budget rather than DynamicVRAM headroom.
    """
    override = os.environ.get("COMFY_VRAM_RESERVE_GB")
    if not override or not override.strip():
        override = os.environ.get("COMFY_VRAM_HEADROOM_GB")
    if override and override.strip():
        try:
            value = float(override)
        except ValueError:
            return DEFAULT_COMFY_VRAM_RESERVE_GB
        return value if math.isfinite(value) and 0.0 <= value <= 8.0 else DEFAULT_COMFY_VRAM_RESERVE_GB

    path = settings_file or CONTROL_PANEL_SETTINGS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_COMFY_VRAM_RESERVE_GB
    value = data.get("vram_reserve_gb", data.get("vram_headroom_gb", DEFAULT_COMFY_VRAM_RESERVE_GB))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_COMFY_VRAM_RESERVE_GB
    value = float(value)
    return value if math.isfinite(value) and 0.0 <= value <= 8.0 else DEFAULT_COMFY_VRAM_RESERVE_GB


def acceleration_mode(settings_file: Path | None = None) -> str:
    override = os.environ.get("MINIMAX_ACCELERATION_MODE")
    if override and override.strip():
        if override.strip().lower() not in ACCELERATION_MODES:
            raise ValueError("MINIMAX_ACCELERATION_MODE 指定了不支持的生成加速方案")
        return override.strip().lower()

    legacy_override = os.environ.get("MINIMAX_SAGE_ATTENTION_ENABLED")
    if legacy_override and legacy_override.strip():
        normalized = legacy_override.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return "sage_attention"
        if normalized in {"0", "false", "no", "off"}:
            return "standard"

    data = _read_settings(settings_file)
    if data is None:
        return DEFAULT_ACCELERATION_MODE
    if "acceleration_mode" in data:
        value = data["acceleration_mode"]
        if not isinstance(value, str) or value not in ACCELERATION_MODES:
            raise ValueError("生成加速配置中的 acceleration_mode 无效，请重新选择方案")
        return value
    legacy_value = data.get("sage_attention_enabled", True)
    return "sage_attention" if legacy_value is not False else "standard"


def nsfw_enabled(settings_file: Path | None = None) -> bool:
    override = os.environ.get("MINIMAX_NSFW_ENABLED")
    if override and override.strip():
        normalized = override.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError("MINIMAX_NSFW_ENABLED 必须为 true 或 false")

    data = _read_settings(settings_file)
    if data is None or "nsfw_enabled" not in data:
        return False
    value = data["nsfw_enabled"]
    if not isinstance(value, bool):
        raise ValueError("NSFW 配置中的 nsfw_enabled 必须为布尔值")
    return value


def nsfw_profile(settings_file: Path | None = None) -> str:
    override = os.environ.get("MINIMAX_NSFW_PROFILE")
    if override and override.strip():
        value = override.strip().lower()
        if value not in NSFW_PROFILES:
            raise ValueError("MINIMAX_NSFW_PROFILE 指定了不支持的 NSFW 方案")
        return value

    data = _read_settings(settings_file)
    if data is None or "nsfw_profile" not in data:
        return DEFAULT_NSFW_PROFILE
    value = data["nsfw_profile"]
    if not isinstance(value, str) or value not in NSFW_PROFILES:
        raise ValueError("NSFW 配置中的 nsfw_profile 无效，请重新选择方案")
    return value


def _read_settings(settings_file: Path | None = None) -> dict | None:
    path = settings_file or CONTROL_PANEL_SETTINGS_FILE
    # Older control panels truncated the shared file before writing. Retry so
    # API submissions never silently use defaults during a partial write.
    for attempt in range(3):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            if attempt == 2:
                raise ValueError("控制面板配置读取失败，请重新保存；已阻止按默认模式生成") from exc
            time.sleep(0.05)
    if not isinstance(data, dict):
        raise ValueError("控制面板配置必须是 JSON 对象，请重新保存")
    return data


def sage_attention_enabled(settings_file: Path | None = None) -> bool:
    """Backward-compatible boolean view of the selected acceleration mode."""
    return acceleration_mode(settings_file) in {"sage_attention", "turbo_8step_sage"}
