"""Build a checked cloud H3 reference plan from local Qwen scene analysis."""

from __future__ import annotations

import re

SCHEMA = "clone.cloud-depth/v2"


def compile_depth_plan(request: dict, qwen: dict, duration: float) -> dict:
    output_duration = request.get("output_duration", 5)
    if isinstance(output_duration, bool) or not isinstance(output_duration, int) or not 4 <= output_duration <= 30:
        raise ValueError("云端生成时长须为 4–30 秒整数")
    people = request["people"]
    picture_count = sum(len(person["images"]) for person in people)
    if not 1 <= picture_count <= 8:
        raise ValueError("深度专业模式最多使用8张人物图，须为背景场景图保留1个名额")
    if qwen.get("status") != "ready":
        raise ValueError(str(qwen.get("reason") or "Qwen未能确认场景引用，请修改说明后重试")[:300])
    scene = re.sub(r"\s+", " ", str(qwen.get("scene_description_en", ""))).strip()
    instruction = re.sub(r"\s+", " ", str(qwen.get("user_instruction_en", ""))).strip()
    if not scene or len(scene) > 1200 or len(instruction) > 1200 or any(c in scene + instruction for c in "<>"):
        raise ValueError("Qwen场景规划结果无效，请重试")
    definitions, bindings = [], []
    number = 0
    for index, person in enumerate(people, 1):
        tags = []
        for asset_id in person["images"]:
            number += 1
            tags.append(f"<Picture {number}>")
            bindings.append({"tag": tags[-1], "role": f"人物 {index}：身份与服装", "asset_id": asset_id})
        definitions.append(f"<Subject {index}> is the single target person defined by {', '.join(tags)}. Preserve this person's face and identity. Keep clothing continuous across the clip; follow an explicit user wardrobe edit only when it names this subject.")
    definitions.append(f"<Picture {number + 1}> is the uploaded background scene image. Use it for environment appearance, colors and lighting only, never as another person.")
    bindings.append({"tag": f"<Picture {number + 1}>", "role": "背景场景图", "asset_id": request["background_image_id"]})
    definitions.append(
        "<Video 1> is a grayscale depth visualization of the selected action. Use its visible motion, composition and spatial cues as a video reference; do not render gray depth shading as the final colors. This cloud reference is not a ControlNet depth input."
    )
    reference_id = request.get("depth_id") or request.get("source_id")
    bindings.append({"tag": "<Video 1>", "role": "深度视频：动作与空间线索", "asset_id": reference_id})
    prompt = "\n".join([
        "subject_definitions", *definitions, "", "summary",
        f"[video editing] Render a full-color {output_duration}-second video with exactly {len(people)} target person(s). Retiming from the {duration:.2f}-second depth reference should preserve the order of visible actions. The scene is: {scene}",
        "", "retention_analysis",
        "Keep target identities and garments from the assigned person pictures. Use the background references for environment appearance only.",
        "", "detailed_description",
        instruction or "Follow the depth reference for subject motion and composition, while rendering the environment from the background references.",
        "Do not add people, introduce new cuts, or treat depth grayscale as the intended color palette.",
        "", "overall_soundscape", "Natural ambient sound appropriate to the rendered scene. No invented dialogue.",
        "", "non_diegetic_music", "N/A",
    ])
    if len(prompt) > 7000:
        raise ValueError("规划后的提示词超过云端7000字符限制")
    return {
        "schema": SCHEMA, "reference_type": "depth", "depth_id": request.get("depth_id"),
        "reference_mode": request.get("reference_mode", "extracted_depth"),
        "source_id": request.get("source_id"), "start": request.get("start"), "end": request.get("end"),
        "background_image_id": request["background_image_id"],
        "people_image_ids": [item["asset_id"] for item in bindings if item["role"].startswith("人物")],
        "scene_text": request["scene_text"],
        "source_prompt": request.get("prompt", "") if request.get("manual_prompt") else "",
        "supplement": request.get("supplement"),
        "bindings": bindings, "prompt": prompt,
        "qwen_context": qwen.get("qwen_context", {}),
    }


def validate_manual_picture_labels(text: str, person_images: int) -> None:
    """Catch explicit numeric scene claims that contradict deterministic bindings."""
    matches = re.findall(
        r"(?:<Picture\s*(\d+)>|图片\s*(\d+)|图\s*(\d+))\s*(?:是|作为|is|=|:|：)?\s*(?:背景|场景|background|scene)", text, re.I
    )
    scene_numbers = [int(number) for match in matches for number in match if number]
    if any(number <= person_images or number != person_images + 1 for number in scene_numbers):
        raise ValueError("场景图片编号与人物图片冲突，请使用“场景图A”或上传顺序对应的编号")
