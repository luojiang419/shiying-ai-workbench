"""Qwen 生图的参考图联系表与蒙版像素合成，沿用旧项目的语义。"""
from __future__ import annotations

import io

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


def pack_references(references: list[bytes], slots: int) -> tuple[list[bytes], bool]:
    if len(references) <= slots:
        return references, False
    kept, extras = references[:slots - 1], references[slots - 1:]
    sheet = Image.new("RGB", (1024, ((len(extras) + 1) // 2) * 512), "white")
    draw = ImageDraw.Draw(sheet)
    for index, raw in enumerate(extras):
        with Image.open(io.BytesIO(raw)) as image:
            thumb = ImageOps.contain(image.convert("RGB"), (496, 480), Image.Resampling.LANCZOS)
        left = (index % 2) * 512 + (512 - thumb.width) // 2
        top = (index // 2) * 512 + 20 + (480 - thumb.height) // 2
        sheet.paste(thumb, (left, top))
        draw.text(((index % 2) * 512 + 12, (index // 2) * 512 + 5), f"Reference {slots + index}", fill="#333333")
    output = io.BytesIO()
    sheet.save(output, format="PNG")
    return kept + [output.getvalue()], True


def validate_mask(mask: bytes) -> None:
    with Image.open(io.BytesIO(mask)) as image:
        if not image.convert("L").getbbox():
            raise HTTPException(422, "蒙版没有可编辑区域，请使用白色标记编辑范围")


def apply_mask(original: bytes, generated: bytes, mask: bytes, width: int, height: int) -> bytes:
    with Image.open(io.BytesIO(original)) as source, Image.open(io.BytesIO(generated)) as result, Image.open(io.BytesIO(mask)) as mask_image:
        original_image = ImageOps.fit(source.convert("RGBA"), (width, height))
        generated_image = result.convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)
        alpha = mask_image.convert("L").resize((width, height), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(2))
        if not ImageChops.difference(alpha, Image.new("L", alpha.size)).getbbox():
            raise HTTPException(422, "蒙版没有可编辑区域")
        output = io.BytesIO()
        Image.composite(generated_image, original_image, alpha).save(output, format="PNG")
        return output.getvalue()
