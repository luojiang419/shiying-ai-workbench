"""视觉参考识别协议、裁切几何校验及角色内视角预算。"""

import math
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

LAYOUT_PROMPT = """Analyze the uploaded CHARACTER REFERENCE image, not a video frame.
Distinguish: single_person (one depiction), same_person_multiview (a turnaround/contact sheet showing multiple views of ONE identity), different_people (a group photo or multiple distinct identities), uncertain, no_person.
Do not assume multiple bodies are the same identity: use matching facial features, hairstyle, outfit and a reference-sheet layout as evidence. Ordinary interacting people are different_people. Reflections are not extra character-sheet panels.
Return ONLY JSON: {"kind":"single_person|same_person_multiview|different_people|uncertain|no_person","confidence":0.0,"same_identity":false,"reason":"brief evidence","views":[{"view":"front|side|back|detail|unknown","box":[0,0,1000,1000]}]}.
Coordinates are NORMALIZED 0..1000 integers [left,top,right,bottom] in the whole image. For multiview give tight PANEL boundaries, including the entire depicted person with head, feet and a small margin, excluding every neighboring depiction. Support horizontal, vertical and grid layouts without separator lines. Do not use a single whole-image box for a multiview sheet. A single-person image uses exactly [0,0,1000,1000]. Never invent obscured body parts."""


def validate_layout(data):
    if not isinstance(data, dict):
        raise ValueError("参考图识别结果必须为对象")  # noqa: TRY004 - 识别结果校验统一使用ValueError
    kind = data.get("kind")
    confidence = data.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise ValueError("参考图识别置信度无效")
    if kind not in {"single_person", "same_person_multiview"} or confidence < 0.85:
        label = {
            "different_people": "多人合照",
            "uncertain": "类型不确定",
            "no_person": "未发现人物",
            "single_person": "单人图识别可信度不足",
            "same_person_multiview": "同人多视图识别可信度不足",
        }.get(kind, "无法确定类型")
        raise ValueError(
            f"参考图需要核对：{label}，请为该人物提供清晰单人图或明确的同人多视图"
        )
    views = data.get("views")
    if (
        not isinstance(views, list)
        or not views
        or any(not isinstance(view, dict) for view in views)
    ):
        raise ValueError("参考图视角列表无效")
    if kind == "single_person":
        if len(views) != 1:
            raise ValueError("单人图类型与视角数量冲突，请核对参考图")
        views = [
            {
                "view": (views or [{}])[0].get("view", "unknown"),
                "box": [0, 0, 1000, 1000],
            }
        ]
    elif (
        data.get("same_identity") is not True
        or not isinstance(views, list)
        or not 2 <= len(views) <= 9
    ):
        raise ValueError("多视图未确认同一人物或视角数量无效")
    normalized = []
    for view in views:
        box = view.get("box")
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or not math.isfinite(x)
                or not 0 <= x <= 1000
                for x in box
            )
        ):
            raise ValueError("参考图裁切框必须为0–1000归一化坐标")
        left, top, right, bottom = box
        if right - left < 50 or bottom - top < 50:
            raise ValueError("参考图裁切区域过小或坐标倒置")
        for other in normalized:
            a, b, c, d = other["box"]
            overlap = max(0, min(right, c) - max(left, a)) * max(
                0, min(bottom, d) - max(top, b)
            )
            if overlap / min((right - left) * (bottom - top), (c - a) * (d - b)) > 0.12:
                raise ValueError("多视图裁切区域重叠，可能混入相邻人物")
        normalized.append(
            {
                "view": view.get("view")
                if view.get("view") in {"front", "side", "back", "detail", "unknown"}
                else "unknown",
                "box": box,
            }
        )
    return {
        "kind": kind,
        "confidence": confidence,
        "reason": str(data.get("reason", ""))[:400],
        "views": normalized,
    }


def crop_views(source, layout, directory, prefix):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        for index, view in enumerate(layout["views"]):
            left, top, right, bottom = view["box"]
            box = (
                math.floor(left * image.width / 1000),
                math.floor(top * image.height / 1000),
                math.ceil(right * image.width / 1000),
                math.ceil(bottom * image.height / 1000),
            )
            path = directory / f"{prefix}-{index + 1}.png"
            image.crop(box).save(path)
            result.append({**view, "path": str(path), "pixel_box": list(box)})
    return result


def validate_crop_check(check):
    confidence = check.get("confidence")
    if (
        check.get("single_depiction") is not True
        or check.get("neighbor_fragment") is not False
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0.8 <= confidence <= 1
    ):
        raise ValueError("参考图裁切复核未通过，请提供独立视角图，避免相邻人物混入")


def is_empty_panel(path):
    """仅排除近乎常量的空白格，不凭低对比度猜测人物不存在。"""
    with Image.open(path) as image:
        image = image.convert("RGB")
        if min(image.size) > 20:
            # 面板边框/整数像素取整可能留下细线，仅忽略约1%的四周边框。
            x, y = max(1, image.width // 100), max(1, image.height // 100)
            image = image.crop((x, y, image.width - x, image.height - y))
        return (
            max(ImageStat.Stat(image).stddev) <= 1
            and max(high - low for low, high in image.getextrema()) <= 4
        )


def select_views(groups, limit=9):
    """每个人至少一图；优先正面，再侧背视角，确定性分配预算。"""
    if not groups or len(groups) > limit or any(not group for group in groups):
        raise ValueError("每个人物至少需要一张有效参考图")
    priorities = {"front": 0, "side": 1, "back": 2, "detail": 3, "unknown": 4}
    ordered = []
    for group in groups:
        distinct, extras, seen = [], [], set()
        for view in sorted(group, key=lambda view: priorities.get(view["view"], 4)):
            (extras if view["view"] in seen else distinct).append(view)
            seen.add(view["view"])
        ordered.append(distinct + extras)
    chosen = [[group[0]] for group in ordered]
    remaining = limit - len(groups)
    for index in range(1, max(map(len, ordered))):
        for group, selected in zip(ordered, chosen):
            if remaining and index < len(group):
                selected.append(group[index])
                remaining -= 1
    return chosen
