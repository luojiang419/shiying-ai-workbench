"""Qwen Image 2.1 工作流，移植自相邻 qwen2.1/studio_api.py。

保留真实模型、原生 2K 预算、首参考完整画幅和 CPU KV 缓存；
推理、图库与鉴权生命周期由 H3 工作台管理，不导入 Qwen 账号服务。
"""

from __future__ import annotations

import base64
import io
import re
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageOps

from .qwen_node_profile import MODEL, CLIP, LOADER, MANAGED, LEGACY_MODEL, LOW_VRAM, LEGACY_PRECISION
VAE = "qwen_image_2.1_vae_bf16.safetensors"
MAX_REFERENCES = 16
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
NATIVE_MAX_PIXELS = 1024 * 1024 if LOW_VRAM else 2048 * 2048
NATIVE_MAX_EDGE = 4096


def parse_size(size: str) -> tuple[int, int]:
    value = str(size or "auto").strip().lower()
    if value in {"auto", "1:1", "square"}:
        return 1024, 1024
    match = re.fullmatch(r"(\d{2,4})\s*[x×]\s*(\d{2,4})", value)
    if not match:
        raise HTTPException(422, "size 应为 1024x1024 等宽高格式或 auto")
    width, height = map(int, match.groups())
    if not (
        64 <= width <= 4096 and 64 <= height <= 4096 and width * height <= 16_777_216
    ):
        raise HTTPException(422, "宽高需为 64～4096，且总像素不超过 16777216")
    if max(width, height) / min(width, height) > 4:
        raise HTTPException(422, "画幅比例不得超过 4:1")
    return width, height


def generation_size(
    width: int,
    height: int,
    *,
    max_pixels: int = NATIVE_MAX_PIXELS,
    max_edge: int = NATIVE_MAX_EDGE,
) -> tuple[int, int]:
    """Validated native 2K budget; larger requests export from a 2K sample."""
    scale = min(
        1.0, (max_pixels / (width * height)) ** 0.5, max_edge / max(width, height)
    )
    w, h = (
        max(64, round(width * scale / 32) * 32),
        max(64, round(height * scale / 32) * 32),
    )
    while w * h > max_pixels:
        if w / width >= h / height and w > 64:
            w -= 32
        else:
            h -= 32
    return w, h


def prepare_native_references(
    references: list[bytes], width: int, height: int
) -> tuple[list[bytes], bool]:
    """Keep the first complete reference on the sampling canvas; preserve input order."""
    prepared, aligned = [], False
    for index, raw in enumerate(references):
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            if index == 0:
                aligned = (
                    abs((image.width / image.height) / (width / height) - 1) >= 0.01
                )
                contained = ImageOps.contain(
                    image, (width, height), Image.Resampling.LANCZOS
                )
                canvas = Image.new("RGBA", (width, height), (238, 238, 238, 255))
                left, top = (
                    (width - contained.width) // 2,
                    (height - contained.height) // 2,
                )
                canvas.paste(contained, (left, top), contained)
                right, bottom = left + contained.width, top + contained.height
                if left:
                    canvas.paste(
                        canvas.crop((left, top, left + 1, bottom)).resize(
                            (left, contained.height)
                        ),
                        (0, top),
                    )
                if right < width:
                    canvas.paste(
                        canvas.crop((right - 1, top, right, bottom)).resize(
                            (width - right, contained.height)
                        ),
                        (right, top),
                    )
                if top:
                    canvas.paste(
                        canvas.crop((0, top, width, top + 1)).resize((width, top)),
                        (0, 0),
                    )
                if bottom < height:
                    canvas.paste(
                        canvas.crop((0, bottom - 1, width, bottom)).resize(
                            (width, height - bottom)
                        ),
                        (0, bottom),
                    )
                image = canvas
            else:
                image = image.resize(
                    generation_size(
                        image.width, image.height, max_pixels=1024 * 1024, max_edge=1536
                    ),
                    Image.Resampling.LANCZOS,
                )
            output = io.BytesIO()
            image.save(output, format="PNG")
            prepared.append(output.getvalue())
    return prepared, aligned


def steps_for_quality(quality: str, steps: int | None) -> int:
    if steps is not None:
        if not 1 <= steps <= 80:
            raise HTTPException(422, "steps 需为 1～80")
        return steps
    return {"low": 12, "medium": 20, "high": 40, "auto": 25, "": 25}.get(
        str(quality or "auto").lower(), 25
    )


def validate_model(model: str) -> None:
    if model and model not in {MODEL, LEGACY_MODEL, "qwen-image-2.1", "qwen-image-2.1-uncensored"}:
        raise HTTPException(422, f"当前仅安装 {MODEL}")


def png_bytes(raw: bytes) -> bytes:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "单张参考图不得超过 20MB")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 16_000_000:
                raise HTTPException(413, "参考图像素过大")
            source.load()
            output = io.BytesIO()
            ImageOps.exif_transpose(source).convert("RGBA").save(output, format="PNG")
            return output.getvalue()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "参考图无法解码") from exc


def decode_reference(value: str) -> bytes:
    if value.startswith("data:image/") and ";base64," in value:
        if len(value) > (MAX_UPLOAD_BYTES + 2) // 3 * 4 + 100:
            raise HTTPException(413, "单张参考图不得超过 24MiB")
        try:
            return png_bytes(
                base64.b64decode(value.split(";base64,", 1)[1], validate=True)
            )
        except (ValueError, base64.binascii.Error) as exc:
            raise HTTPException(422, "参考图 base64 无效") from exc
    raise HTTPException(422, "JSON 参考图需为 data:image/...;base64,...")


def comfy_prompt(
    prompt: str,
    width: int,
    height: int,
    steps: int,
    seed: int,
    reference_names: list[str],
    *,
    native_references: bool = True,
) -> dict[str, Any]:
    graph: dict[str, Any] = {
        "1": {"class_type": LOADER, "inputs": {"unet_name": MODEL, **({"weight_dtype": "default"} if MANAGED else {})}},
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "cpu" if LEGACY_PRECISION else "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["2", 0],
                "prompt": prompt,
                "negative_prompt": "",
                "resolution": 0 if native_references else max(width, height),
            },
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["9", 0],
                "positive": ["4", 0],
                "negative": ["4", 1],
                "latent_image": ["4", 2] if reference_names else ["5", 0],
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "7": {
            "class_type": "VAEDecodeTiled" if MANAGED else "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["3", 0], **({"tile_size": 512, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8} if MANAGED else {})},
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {"images": ["7", 0], "filename_prefix": "Qwen21_Studio"},
        },
        "9": {
            "class_type": "QwenImage21Cache",
            "inputs": {"model": ["1", 0], "device": "cpu", "dtype": "default"},
        },
    }
    for index, name in enumerate(reference_names[:MAX_REFERENCES], start=1):
        node_id = str(20 + index)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        graph["4"]["inputs"][f"images.image_{index}"] = [node_id, 0]
    if reference_names:
        graph["4"]["inputs"]["vae"] = ["3", 0]
    return graph
