"""克隆素材编译：助手优先，未配置时才使用通用编辑模板。"""

from __future__ import annotations

import base64
import io
import json
import math
import re
import subprocess

from PIL import Image, ImageOps

from . import prompt_optimizer
from .clone_depth_runtime import find_tool
from .clone_edit_contract import replacement_contract, replacement_prompt_issues
from .config import ROOT_DIR


def validate_material_request(manager, request: dict) -> list[dict]:
    if request.get("reference_mode") != "prompt_video":
        raise ValueError("统一素材克隆需要原视频提示词参考模式")
    context = manager.resolve_reference(request)
    if "output_duration" in request:
        duration = request["output_duration"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 4 <= duration <= 30:
            raise ValueError("云端生成时长应为 4–30 秒")
    ids = request.get("reference_images", [])
    if not isinstance(ids, list) or len(ids) > 9 or any(not isinstance(aid, str) for aid in ids):
        raise ValueError("参考图片应为列表，最多 9 张")
    if len(set(ids)) != len(ids):
        raise ValueError("参考素材不能重复")
    request["reference_images"] = list(ids)
    source = manager.asset(context["source_id"], "video")
    manifest = [{"id": source["id"], "kind": "Video", "number": 1, "tag": "<Video 1>",
                 "name": source.get("name", "参考视频"), "duration": context["duration"], "role": "edit_source"}]
    for index, aid in enumerate(ids, 1):
        asset = manager.asset(aid, "image")
        manifest.append({"id": aid, "kind": "Picture", "number": index, "tag": f"<Picture {index}>",
                         "name": asset.get("name", f"参考图 {index}")})
    for key in ("prompt", "user_prompt"):
        text = request.get(key, request.get("prompt", ""))
        if not isinstance(text, str) or not text.strip():
            raise ValueError("请输入提示词，可使用 @ 引用素材并描述需求")
        limit = 7000 if key == "user_prompt" else 18000
        if len(text) > limit:
            raise ValueError(f"{key} 超过 {limit} 字符")
        validate_material_references(text, manifest)
    return manifest


def validate_material_references(text: str, manifest: list[dict]) -> None:
    if "[已移除素材：" in text:
        raise ValueError("提示词包含已移除素材，请删除失效引用或重新选择素材")
    available = {item["tag"] for item in manifest}
    for match in re.finditer(r"<(Picture|Video|Audio)\s+(\d+)>", text, re.IGNORECASE):
        kind = match[1].capitalize()
        tag = f"<{kind} {int(match[2])}>"
        if tag not in available:
            raise ValueError(f"{tag} 没有对应的已上传素材")


def _image_data(path) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1536, 1536))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _assistant_visuals(manager, request: dict, manifest: list[dict]) -> dict:
    images = []
    for item in manifest:
        if item["kind"] == "Picture":
            asset = manager.asset(item["id"], "image")
            images.append({"tag": item["tag"], "name": item["name"],
                           "dataUrl": _image_data(manager.folder(item["id"]) / asset["file"])})
    source = manager.asset(request["source_id"], "video")
    start, end = request["start"], request["end"]
    frames = []
    # 三点采样会漏掉片段内的跳跃、遮挡和切镜，导致助手把动作写成静止。
    # 在优化器的 15 帧预算内覆盖整个所选区间，而非只看首中尾。
    duration = end - start
    count = min(15, max(3, math.ceil(duration * 2) + 1))
    last = max(0, duration - .05)
    for index in range(count):
        relative = round(last * index / (count - 1), 6)
        command = [find_tool(ROOT_DIR, "ffmpeg") or "ffmpeg", "-v", "error", "-ss", str(start + relative),
                   "-i", str(manager.folder(source["id"]) / source["file"]), "-frames:v", "1",
                   "-vf", "scale=768:768:force_original_aspect_ratio=decrease", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
        result = subprocess.run(command, capture_output=True, check=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not result.stdout:
            raise ValueError("参考视频抽帧失败")
        frames.append({"tag": "<Video 1>", "name": source.get("name", "参考视频"), "timeSeconds": relative,
                       "dataUrl": "data:image/jpeg;base64," + base64.b64encode(result.stdout).decode("ascii")})
    return {"images": images, "videoFrames": frames}


def compile_material_template(request: dict, manifest: list[dict]) -> dict:
    """无助手时只拼接已知素材和需求，不猜测主体数量、外观或镜头。"""
    duration = request.get("output_duration", request["end"] - request["start"])
    definitions = ["<Video 1> is the source video to edit, supplying the original shots, framing, actions and scene."]
    retention = [("<Video 1>: partially_preserved — apply all explicit user edits first; "
                  "retain other subjects, background, subtitles, stickers, props, shot order and camera movement only where the request leaves them unchanged.")]
    for item in manifest:
        if item["kind"] != "Picture":
            continue
        subject = f"<Subject {item['number']}>"
        definitions.append(f"{subject} is the reference appearance or visual attribute from {item['tag']} selected by the user. "
                           "Apply it only to the source content named in the edit request; this definition does not add a cast member "
                           "or imply that each picture depicts a different subject.")
        retention.append(f"{subject}: attribute_transfer — transfer only the requested reference features to the indicated source content; "
                         "use the picture background or an opening/ending frame only if the user explicitly assigns that role.")
    sections = {
        "subject_definitions": "\n".join(definitions),
        "summary": f"[video editing] The target video is an edited version of <Video 1>. Produce a {duration:.3f}-second video "
                   "with only the user-requested changes. Explicit edits override only the corresponding source attributes.",
        "retention_analysis": "\n".join(retention),
        "detailed_description": "[Shot 1] Apply the user edit request below as the authoritative target, including all exclusions and preservation requirements. "
            "Use the source opening composition and action only if the user does not request a different opening. "
            "Apply the reference features according to the user's bindings, retaining the source subject count and screen positions "
            "unless explicitly changed. A reference picture may supply multiple targets or an attribute rather than a new subject. "
            "Keep the edited appearance consistent through movement, interaction and occlusion.\n"
            f"Map the {request['end']-request['start']:.3f}-second source interval uniformly to {duration:.3f} seconds, "
            "following the source shots in their original order and at their original relative cut times. Preserve each shot's camera "
            "angle, framing, camera movement or stationary camera, action timing, pose and physical contacts unless the request changes them. "
            "Do not add an unrequested camera move, shot, accessory, scene or event. Explicit camera, scene and action edits override these source defaults.\n"
            "Change only the requested appearance or rendering style. Preserve the style of unchanged content. Keep original subtitles "
            "and visible text in their original language, wording, position, size, styling and timing, unless explicitly changed or removed; retain other unchanged stickers and overlays. "
            "Match scene lighting, perspective and contact shadows, integrating the edited subject naturally into the scene. "
            "End on the source final action and composition unless the user explicitly selects another ending or reference-picture keyframe.\n"
            "User edit request (quoted verbatim; use its material tags to resolve all bindings): "
            + json.dumps(request["prompt"], ensure_ascii=False)
            + ("\nReplacement binding: replace the source person with the complete visible appearance from "
               + ", ".join(replacement_contract(request)["pictures"])
               + ", including face, hair, build and clothing unless the user explicitly preserves an attribute. "
               "The source person supplies motion and placement only, not the target identity."
               if (replacement_contract(request) or {}).get("whole_person") else ""),
        "overall_soundscape": "Follow the source scene's ambience, movement sounds and dialogue timing unless the user requests audio changes. "
                              "Do not invent dialogue or extra sound events.",
        "non_diegetic_music": "Retain the source music if present and unchanged by the request; otherwise N/A. Do not add an unrelated score.",
    }
    prompt = "\n\n".join(f"{key}\n{value}" for key, value in sections.items())
    limit = 7000 if "output_duration" in request else 18000
    if len(prompt) > limit:
        raise ValueError(f"默认模板拼接后超过 {limit} 字符，请缩短需求或减少素材；未截断提示词")
    return {"prompt": prompt,
            "plan": {"schema": "clone.material-template/v1", "duration": duration},
            "optimizer": {"source": "template", "name": "默认克隆模板"}, "manifest": manifest}


def compile_material_prompt(manager, request: dict) -> dict:
    manifest = validate_material_request(manager, request)
    prepared = {"prompt": request["prompt"], "user_prompt": request.get("user_prompt", request["prompt"]),
                "mode": "references",
                "duration": request.get("output_duration", request["end"] - request["start"]),
                "manifest": manifest, "assets": "\n".join(f"{item['tag']}：{item['name']}" for item in manifest)}
    # 原文仍在 user_prompt；编译使用规范化引用，避免把“图片1”别名重新传给 H3。
    # 未配置助手时不解码素材、不启动模型。
    try:
        enabled = bool(prompt_optimizer.load_assistant_providers())
    except ValueError as exc:
        raise ValueError(f"AI 助手配置不可用，请修复配置后重试；未使用默认模板：{exc}") from exc
    if not enabled:
        from .public_release import enabled as public_release_enabled
        if public_release_enabled():
            raise ValueError('请先在「AI 助手配置」中配置自己的 DeepSeek Flash Key，再生成克隆视频')
        return compile_material_template(request, manifest)
    try:
        prepared.update(_assistant_visuals(manager, request, manifest))
        result = prompt_optimizer.optimize_preferred_prompt(prepared)
        if result.get("plan", {}).get("prompt_fallback"):
            raise ValueError("AI 助手提示词未通过质检")
        if result.get("optimizer", {}).get("source") != "assistant":
            reason = result.get("optimizer", {}).get("warning") or "AI 助手未返回优化结果"
            raise ValueError(reason.replace("，已使用原始提示词", ""))
        validate_material_references(result["prompt"], manifest)
        issues = replacement_prompt_issues(result["prompt"], replacement_contract(request))
        if issues:
            raise ValueError("；".join(issues))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise ValueError(f"克隆提示词优化失败，请检查 AI 助手或重试；未使用默认模板：{exc}") from exc
    return {**result, "manifest": manifest}
