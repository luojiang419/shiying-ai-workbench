from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass

from .config import (
    ACCELERATION_MODES,
    AFTERMIDNIGHT_PROFILE,
    AUDIO_VAE_MODEL,
    DIFFUSION_MODEL,
    HERETIC_TEXT_ENCODER_MODEL,
    NAUGHTYTIMES_LORA_MODEL,
    NAUGHTYTIMES_LORA_STRENGTH,
    NAUGHTYTIMES_PROFILE,
    NAUGHTYTIMES_PROFILES,
    NSFW_LORA_MODEL,
    NSFW_PROFILES,
    REFERENCE_DIFFUSION_MODEL,
    SAGE_ATTENTION_NODE_CLASS,
    TEXT_ENCODER_MODEL,
    TURBO_LORA_MODEL,
    TURBO_LORA_NODE_CLASS,
    UNBOUNDED_SCHEDULER_NODE_CLASS,
    VIDEO_VAE_MODEL,
)
from .config import (
    acceleration_mode as configured_acceleration_mode,
)
from .config import (
    nsfw_enabled as configured_nsfw_enabled,
)
from .config import (
    nsfw_profile as configured_nsfw_profile,
)

HIGH_MEMORY_16_9_PRESETS: dict[str, tuple[int, int]] = {
    "0.7MP 16:9 - 1152x640": (1152, 640),
    "0.8MP 16:9 - 1216x672": (1216, 672),
    "0.9MP 16:9 - 1280x736": (1280, 736),
    "0.98MP 16:9 - 1344x768 (H3 原生上限)": (1344, 768),
    "1.0MP 16:9 - 1376x768 (高显存实验)": (1376, 768),
    "1.2MP 16:9 - 1504x832 (高显存实验)": (1504, 832),
    "1.5MP 16:9 - 1664x928 (高显存实验)": (1664, 928),
    "1.8MP 16:9 - 1824x1024 (高显存实验)": (1824, 1024),
    "2.0MP 16:9 - 1920x1088 (高显存实验)": (1920, 1088),
}


def high_resolution_presets_enabled() -> bool:
    return os.environ.get("MINIMAX_HIGH_RESOLUTION_PRESETS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


BASE_RESOLUTION_PRESETS: dict[str, tuple[int, int]] = {
    "0.2MP 21:9 - 672x288": (672, 288),
    "0.3MP 21:9 - 896x384": (896, 384),
    "0.5MP 21:9 - 1120x480": (1120, 480),
    "0.2MP 16:9 - 608x352": (608, 352),
    "0.3MP 16:9 - 736x416": (736, 416),
    "0.4MP 16:9 - 864x480": (864, 480),
    "0.5MP 16:9 - 960x544": (960, 544),
    "0.6MP 16:9 - 1056x608": (1056, 608),
    "0.2MP 4:3 - 512x384": (512, 384),
    "0.3MP 4:3 - 640x480": (640, 480),
    "0.4MP 4:3 - 768x576": (768, 576),
    "Square 512x512": (512, 512),
    "0.2MP 3:4 - 384x512": (384, 512),
    "0.3MP 3:4 - 480x640": (480, 640),
    "0.4MP 3:4 - 576x768": (576, 768),
    "0.2MP 9:16 - 352x608": (352, 608),
    "0.3MP 9:16 - 416x736": (416, 736),
    "0.4MP 9:16 - 480x864": (480, 864),
}


# MP 是预设档位；32 像素对齐后实际像素量会略有偏差。
# 保留旧名称作为 API/历史任务标识，新增比例共享同一档位表。
RESOLUTION_TIERS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 1.8, 2.0)
NATIVE_RESOLUTION_SIZES = {
    "21:9": (1792, 768),
    "16:9": (1344, 768),
    "4:3": (1024, 768),
    "1:1": (768, 768),
    "3:4": (768, 1024),
    "9:16": (768, 1344),
}


def _complete_resolution_presets() -> dict[str, tuple[int, int]]:
    presets = {**BASE_RESOLUTION_PRESETS, **HIGH_MEMORY_16_9_PRESETS}
    for aspect, native_size in NATIVE_RESOLUTION_SIZES.items():
        # 竖屏直接交换对应横屏的宽高，保证相同档位、相同像素量。
        landscape = {"3:4": "4:3", "9:16": "16:9"}.get(aspect, aspect)
        numerator, denominator = map(int, landscape.split(":"))
        for tier in RESOLUTION_TIERS:
            prefix = f"{tier:.1f}MP {aspect} - "
            if any(name.startswith(prefix) for name in presets):
                continue
            source = next((size for name, size in presets.items()
                           if name.startswith(f"{tier:.1f}MP {landscape} - ")), None)
            if source is None:
                width = max(32, round(math.sqrt(tier * 1_000_000 * numerator / denominator) / 32) * 32)
                height = max(32, round(math.sqrt(tier * 1_000_000 * denominator / numerator) / 32) * 32)
                source = width, height
            size = source[::-1] if aspect != landscape else source
            if any(existing == size for existing in presets.values()):
                continue
            experimental = size[0] > native_size[0] or size[1] > native_size[1]
            suffix = " (高显存实验)" if experimental else " (768P)" if size == native_size else ""
            presets[f"{prefix}{size[0]}x{size[1]}{suffix}"] = size
        if native_size not in presets.values():
            mp = native_size[0] * native_size[1] / 1_000_000
            presets[f"{mp:.2f}MP {aspect} - {native_size[0]}x{native_size[1]} (768P)"] = native_size

    def sort_key(item):
        name, (width, height) = item
        aspect = "1:1" if name.startswith("Square") else name.split()[1]
        return list(NATIVE_RESOLUTION_SIZES).index(aspect), width * height

    return dict(sorted(presets.items(), key=sort_key))


ALL_RESOLUTION_PRESETS = _complete_resolution_presets()
RESOLUTION_PRESETS = ALL_RESOLUTION_PRESETS if high_resolution_presets_enabled() else BASE_RESOLUTION_PRESETS


@dataclass(frozen=True)
class H3Request:
    mode: str
    prompt: str
    resolution: str
    duration: float
    steps: int
    seed: int | None
    first_frame: str | None = None
    last_frame: str | None = None
    reference_images: tuple[str, ...] = ()
    reference_videos: tuple[str, ...] = ()
    reference_audios: tuple[str, ...] = ()
    depth_control: bool = False
    control_video: str | None = None
    control_strength: float = 1.0
    reference_model: str = "ref2va"
    memory_chunk_rows: int | None = None


def duration_to_frame_length(duration: float) -> int:
    base = max(5, round(duration * 24))
    return base + (5 - (base % 17)) % 17


def low_vram_depth_chunk_rows(req: H3Request) -> int | None:
    if not req.depth_control:
        return None
    width, height = RESOLUTION_PRESETS[req.resolution]
    area = width * height
    if area < 864 * 480:
        return None
    return 1024 if area >= 1216 * 672 else 2048


def memory_chunk_rows(req: H3Request) -> int | None:
    return req.memory_chunk_rows or low_vram_depth_chunk_rows(req)


def sanitize_request(data: dict) -> H3Request:
    raw_control_strength = data.get("control_strength", 1.0)
    if isinstance(raw_control_strength, bool) or not isinstance(raw_control_strength, (int, float)) or not 0.5 <= raw_control_strength <= 2.0:
        raise ValueError("深度结构控制力度必须在0.5–2.0之间")
    mode = str(data.get("mode") or "keyframes").strip()
    if mode not in {"keyframes", "references"}:
        raise ValueError("生成模式不受支持")

    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("提示词不能为空")

    resolution = str(data.get("resolution") or "0.2MP 16:9 - 608x352")
    if resolution not in RESOLUTION_PRESETS:
        raise ValueError("分辨率预设不受支持")

    duration = float(data.get("duration", 2.0))
    if duration < 1 or duration > 15:
        raise ValueError("H3 时长建议在 1 到 15 秒之间")

    steps = int(data.get("steps", 12))
    if steps < 4:
        raise ValueError("steps 不能小于 4")

    raw_seed = data.get("seed")
    seed = None if raw_seed in (None, "", "random") else int(raw_seed)
    if seed is None or seed < 0:
        seed = random.randint(1, 2**63 - 1)

    first_frame = _optional_image_name(data.get("first_frame"))
    last_frame = _optional_image_name(data.get("last_frame"))
    reference_images = _optional_image_names(data.get("reference_images"))
    reference_videos = _optional_video_names(data.get("reference_videos"))
    reference_audios = _optional_audio_names(data.get("reference_audios"))
    depth_control = data.get("depth_control", False)
    control_video = _optional_video_name(data.get("control_video"))
    if not isinstance(depth_control, bool):
        raise ValueError("深度结构控制开关必须为布尔值")  # noqa: TRY004 - API以ValueError返回400
    if depth_control and (mode != "references" or not control_video):
        raise ValueError("深度结构控制需要Ref2VA及独立的对齐控制视频")
    if control_video and not depth_control:
        raise ValueError("控制视频只能在深度结构控制开启时使用")
    reference_model = str(data.get("reference_model") or "ref2va").strip().lower()
    if reference_model not in {"fl2va", "ref2va"}:
        raise ValueError("多参考权重选项不受支持")
    if depth_control and reference_model != "ref2va":
        raise ValueError("深度结构控制必须使用Ref2VA权重")
    if mode == "references" and not reference_images and not reference_videos and not reference_audios:
        raise ValueError("多参考模式至少需要上传 1 个参考图片或参考视频，也可上传参考音频")
    if len(reference_images) > 9:
        raise ValueError("多参考图模式最多支持 9 张参考图")
    if len(reference_videos) > 3:
        raise ValueError("多参考模式最多支持 3 个参考视频")
    if len(reference_audios) > 3:
        raise ValueError("多参考模式最多支持 3 个参考音频")

    return H3Request(
        mode=mode,
        prompt=prompt,
        resolution=resolution,
        duration=duration,
        steps=steps,
        seed=seed,
        first_frame=first_frame,
        last_frame=last_frame,
        reference_images=reference_images,
        reference_videos=reference_videos,
        reference_audios=reference_audios,
        depth_control=depth_control,
        control_video=control_video,
        control_strength=float(raw_control_strength),
        reference_model=reference_model,
    )


def _optional_image_name(value: object) -> str | None:
    if value in (None, ""):
        return None
    name = str(value).strip().replace("\\", "/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError("参考图文件名不安全")
    return name


def _optional_image_names(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("多参考图参数格式不正确")
    names = []
    for item in value:
        name = _optional_image_name(item)
        if name:
            names.append(name)
    return tuple(names)


def _optional_video_name(value: object) -> str | None:
    if value in (None, ""):
        return None
    name = str(value).strip().replace("\\", "/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError("参考视频文件名不安全")
    if not name.lower().endswith((".mp4", ".mov")):
        raise ValueError("参考视频只支持 mp4、mov")
    return name


def _optional_video_names(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("参考视频参数格式不正确")
    names = []
    for item in value:
        name = _optional_video_name(item)
        if name:
            names.append(name)
    return tuple(names)


def _optional_audio_names(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("参考音频参数格式不正确")
    names = []
    for item in value:
        name = _optional_audio_name(item)
        if name:
            names.append(name)
    return tuple(names)


def _optional_audio_name(value: object) -> str | None:
    if value in (None, ""):
        return None
    name = str(value).strip().replace("\\", "/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError("参考音频文件名不安全")
    if not name.lower().endswith((".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")):
        raise ValueError("参考音频只支持 mp3、wav、m4a、aac、flac、ogg")
    return name


def build_prompt(
    req: H3Request,
    *,
    acceleration_mode: str | None = None,
    use_sage_attention: bool | None = None,
    nsfw_enabled: bool | None = None,
    nsfw_profile: str | None = None,
) -> dict:
    width, height = RESOLUTION_PRESETS[req.resolution]
    length = duration_to_frame_length(req.duration)
    low_vram_chunk_rows = memory_chunk_rows(req)
    if acceleration_mode is None:
        if use_sage_attention is not None:
            acceleration_mode = "sage_attention" if use_sage_attention else "standard"
        else:
            acceleration_mode = configured_acceleration_mode()
    if acceleration_mode not in ACCELERATION_MODES:
        raise ValueError(f"不支持的生成加速方案: {acceleration_mode}")
    use_sage_attention = acceleration_mode in {"sage_attention", "turbo_8step_sage"}
    if low_vram_chunk_rows and use_sage_attention:
        raise ValueError("低显存分块需要标准注意力模式（关闭 SageAttention），以启用分块 QKV")
    use_turbo = acceleration_mode in {"turbo_8step", "turbo_8step_sage"}
    if req.mode == "references" and use_turbo:
        raise ValueError("多参考Ref2VA不能使用首尾帧FL2VA的Turbo适配器，请使用标准或SageAttention模式")
    if nsfw_enabled is None:
        nsfw_enabled = configured_nsfw_enabled()
    if nsfw_profile is None:
        nsfw_profile = configured_nsfw_profile()
    active_nsfw_profile = (
        None if req.mode == "references" and req.reference_model == "fl2va"
        else resolve_nsfw_profile(req.mode, nsfw_enabled, nsfw_profile)
    )
    use_nsfw = active_nsfw_profile is not None
    use_euler = use_turbo or use_nsfw
    use_beta = use_turbo or active_nsfw_profile == AFTERMIDNIGHT_PROFILE
    text_encoder_model = (
        HERETIC_TEXT_ENCODER_MODEL
        if active_nsfw_profile == NAUGHTYTIMES_PROFILE
        else TEXT_ENCODER_MODEL
    )
    guider_model = ["6", 0]
    effective_steps = 8 if use_turbo else req.steps

    prompt = {
        "6": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": (
                    REFERENCE_DIFFUSION_MODEL
                    if req.mode == "references" and req.reference_model == "ref2va"
                    else DIFFUSION_MODEL
                ),
                "weight_dtype": "default",
            },
        },
        "13": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": text_encoder_model,
                "type": "minimax",
                "device": "default",
            },
        },
        "11": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VIDEO_VAE_MODEL},
        },
        "24": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": AUDIO_VAE_MODEL},
        },
        "15": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": req.seed},
        },
        "17": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler" if use_euler else "res_multistep"},
        },
        "9": {
            "class_type": UNBOUNDED_SCHEDULER_NODE_CLASS,
            "inputs": {
                "model": ["6", 0],
                "scheduler": "beta" if use_beta else "simple",
                "steps": effective_steps,
                "denoise": 1.0,
            },
        },
        "16": {
            "class_type": "BasicGuider",
            "inputs": {
                "model": guider_model,
                "conditioning": ["104", 0],
            },
        },
        "14": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["15", 0],
                "guider": ["16", 0],
                "sampler": ["17", 0],
                "sigmas": ["9", 0],
                "latent_image": ["104", 1],
            },
        },
        "10": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["14", 0],
                "vae": ["11", 0],
            },
        },
        "23": {
            "class_type": "VAEDecodeAudio",
            "inputs": {
                "samples": ["14", 0],
                "vae": ["24", 0],
            },
        },
        "91": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["10", 0],
                "audio": ["23", 0],
                "fps": 24,
                "bit_depth": 8,
            },
        },
        "92": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["91", 0],
                "filename_prefix": "video/MiniMax_H3",
                "format": "auto",
                "codec": "auto",
            },
        },
    }

    model_for_guider = ["6", 0]
    if use_turbo:
        prompt["7"] = {
            "class_type": TURBO_LORA_NODE_CLASS,
            "inputs": {
                "model": ["6", 0],
                "lora_name": TURBO_LORA_MODEL,
                "strength_model": 1.0,
            },
        }
        model_for_guider = ["7", 0]
    if use_nsfw:
        lora_name = (
            NAUGHTYTIMES_LORA_MODEL
            if active_nsfw_profile in NAUGHTYTIMES_PROFILES
            else NSFW_LORA_MODEL
        )
        lora_strength = (
            NAUGHTYTIMES_LORA_STRENGTH
            if active_nsfw_profile in NAUGHTYTIMES_PROFILES
            else 1.0
        )
        prompt["18"] = {
            "class_type": TURBO_LORA_NODE_CLASS,
            "inputs": {
                "model": model_for_guider,
                "lora_name": lora_name,
                "strength_model": lora_strength,
            },
        }
        model_for_guider = ["18", 0]
    if use_sage_attention:
        sage_node_id = "8" if use_turbo else "7"
        prompt[sage_node_id] = {
            "class_type": SAGE_ATTENTION_NODE_CLASS,
            "inputs": {
                "model": model_for_guider,
                "sage_attention": "auto",
                "allow_compile": False,
            },
        }
        guider_model = [sage_node_id, 0]
    else:
        guider_model = model_for_guider
    prompt["16"]["inputs"]["model"] = guider_model

    if low_vram_chunk_rows:
        prompt["503"] = {
            "class_type": "H3MemoryOptimization",
            "inputs": {
                "model": guider_model,
                "fused_qkv": "auto",
                "mlp_memory": "auto",
                "chunk_rows": low_vram_chunk_rows,
                "preserve_precision": True,
                "precision_mode": "Preserve native",
                "qkv_streaming_mode": "Forced",
                "embedding_memory_mode": "Auto",
                "kitchen_v_memory_mode": "Lower VRAM (slower)",
            },
        }
        prompt["504"] = {
            "class_type": "H3AIMDOResidencyLimiter",
            "inputs": {"model": ["503", 0], "residency": "0 blocks"},
        }
        guider_model = ["504", 0]
        prompt["16"]["inputs"]["model"] = guider_model

    if req.mode == "references":
        prompt["104"] = {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["13", 0],
                "vae": ["11", 0],
                "audio_vae": ["24", 0],
                "prompt": req.prompt,
                "width": width,
                "height": height,
                "length": length,
                "ref_image_size": "match",
            },
        }
        for index, image_name in enumerate(req.reference_images):
            node_id = str(201 + index)
            prompt[node_id] = {
                "class_type": "LoadImage",
                "inputs": {"image": image_name},
            }
            prompt["104"]["inputs"][f"ref_images.ref_image_{index}"] = [node_id, 0]
        for index, video_name in enumerate(req.reference_videos):
            load_node_id = str(301 + index)
            components_node_id = str(311 + index)
            prompt[load_node_id] = {
                "class_type": "LoadVideo",
                "inputs": {"file": video_name},
            }
            prompt[components_node_id] = {
                "class_type": "GetVideoComponents",
                "inputs": {"video": [load_node_id, 0]},
            }
            prompt["104"]["inputs"][f"ref_videos.ref_video_{index}"] = [components_node_id, 0]
            prompt["104"]["inputs"][f"ref_video_audios.ref_video_audio_{index}"] = [
                components_node_id,
                1,
            ]
        for index, audio_name in enumerate(req.reference_audios):
            node_id = str(401 + index)
            prompt[node_id] = {
                "class_type": "LoadAudio",
                "inputs": {"audio": audio_name},
            }
            prompt["104"]["inputs"][f"ref_audios.ref_audio_{index}"] = [node_id, 0]
        if req.depth_control:
            prompt["351"] = {
                "class_type": "LoadVideo",
                "inputs": {"file": req.control_video},
            }
            prompt["361"] = {
                "class_type": "GetVideoComponents",
                "inputs": {"video": ["351", 0]},
            }
            # OOM 降档后控制视频也必须匹配新尺寸，避免控制流 token 数不一致。
            prompt["362"] = {
                "class_type": "ImageScale",
                "inputs": {
                    "image": ["361", 0], "upscale_method": "bilinear",
                    "width": width, "height": height, "crop": "disabled",
                },
            }
            prompt["501"] = {
                "class_type": "MiniMaxCloneDepthControlLoader",
                "inputs": {"control_net_name": "minimax_h3_fun_controlnet_union_pruned_bf16.safetensors"},
            }
            prompt["502"] = {
                "class_type": "MiniMaxCloneDepthControlApply",
                "inputs": {
                    "model": prompt["16"]["inputs"]["model"],
                    "control_net": ["501", 0], "vae": ["11", 0],
                    "control_video": ["362", 0], "strength": req.control_strength,
                    "start_percent": 0.0, "end_percent": 1.0,
                    **({"low_vram": True, "low_vram_chunk_rows": low_vram_chunk_rows}
                       if low_vram_chunk_rows else {}),
                },
            }
            prompt["16"]["inputs"]["model"] = ["502", 0]
    else:
        prompt["104"] = {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": ["13", 0],
                "vae": ["11", 0],
                "prompt": req.prompt,
                "width": width,
                "height": height,
                "length": length,
            },
        }

        if req.first_frame:
            prompt["101"] = {
                "class_type": "LoadImage",
                "inputs": {"image": req.first_frame},
            }
            prompt["104"]["inputs"]["first_frame"] = ["101", 0]

        if req.last_frame:
            prompt["102"] = {
                "class_type": "LoadImage",
                "inputs": {"image": req.last_frame},
            }
            prompt["104"]["inputs"]["last_frame"] = ["102", 0]

    return prompt


def resolve_nsfw_profile(mode: str, enabled: bool, profile: str) -> str | None:
    if profile not in NSFW_PROFILES:
        raise ValueError(f"不支持的 NSFW 方案: {profile}")
    if not enabled:
        return None
    if profile == AFTERMIDNIGHT_PROFILE and mode == "references":
        return profile
    if profile in NAUGHTYTIMES_PROFILES and mode != "references":
        return profile
    return None
