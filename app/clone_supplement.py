"""单任务补充计划：模型只解释意图，程序负责绑定对象和结构。"""

import copy
import math
import re

SECTIONS = (
    "subject_definitions", "summary", "retention_analysis",
    "detailed_description", "overall_soundscape", "non_diegetic_music",
)
ATTRIBUTES = {"clothing", "hair", "accessory", "environment", "preservation"}
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["status", "directives"],
    "properties": {
        "status": {"type": "string", "enum": ["ready", "needs_clarification", "conflict", "unsupported"]},
        "directives": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["operation", "scope", "subject_index", "attribute", "instruction_en", "preserve", "confidence"],
            "properties": {
                "operation": {"type": "string", "enum": ["replace_or_modify", "preserve"]},
                "scope": {"type": "string", "enum": ["subject", "environment"]},
                "subject_index": {"type": ["integer", "null"]},
                "attribute": {"type": "string", "enum": sorted(ATTRIBUTES)},
                "instruction_en": {"type": "string", "maxLength": 700},
                "preserve": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 160}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        }},
    },
}


def split_prompt(prompt):
    pattern = r"(?m)^(" + "|".join(SECTIONS) + r")\s*$"
    matches = list(re.finditer(pattern, prompt))
    if tuple(m.group(1) for m in matches) != SECTIONS or prompt[:matches[0].start()].strip():
        raise ValueError("使用补充需求时，手动提示词必须保留完整且有序的六段结构，请恢复自动模板")
    return {
        m.group(1): prompt[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(prompt)].strip()
        for i, m in enumerate(matches)
    }


def english(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or re.search(r"[<>\x00-\x1f\u3400-\u9fff]|\[Shot|" + "|".join(SECTIONS), value)):
        raise ValueError("Qwen补充需求不够明确或包含无效引用，请修改后重试")
    return value.strip()


def validate_plan(raw, supplement, people):
    if not isinstance(raw, dict):
        raise ValueError("Qwen未返回有效的补充需求计划")  # noqa: TRY004 - 统一任务校验错误
    status = raw.get("status")
    if status != "ready":
        reason = {"needs_clarification": "作用对象或修改内容不明确，请选择人物并具体描述",
                  "conflict": "需求与深度控制、人物绑定或手动提示词冲突，请调整需求",
                  "unsupported": "当前深度参考无法精确保留源人物外观或执行局部像素编辑"}.get(status, "Qwen返回的计划状态无效")
        raise ValueError(reason)
    items = raw.get("directives")
    if not isinstance(items, list) or not 1 <= len(items) <= 6:
        raise ValueError("Qwen补充需求必须包含1–6项指令，不能静默截断")
    directives = []
    target = supplement["target"]
    for item in items:
        if not isinstance(item, dict) or item.get("operation") not in {"replace_or_modify", "preserve"}:
            raise ValueError("补充需求不支持新增或删除人物、道具及改变深度几何")
        scope, index, attribute = item.get("scope"), item.get("subject_index"), item.get("attribute")
        if scope == "subject":
            if type(index) is not int or not 1 <= index <= len(people):
                raise ValueError("Qwen补充需求的人物编号无效")
            if target["kind"] == "subject" and index != target["subject_index"] and item["operation"] != "preserve":
                raise ValueError("Qwen补充需求越过所选人物范围")
            if attribute not in ATTRIBUTES - {"environment"}:
                raise ValueError("Qwen补充需求的修改属性无效")
        elif scope != "environment" or index is not None or attribute not in {"environment", "preservation"}:
            raise ValueError("Qwen补充需求的作用范围无效")
        if target["kind"] == "subject" and scope == "environment" and item["operation"] != "preserve":
            raise ValueError("修改背景请使用按文字指定对象，并明确描述背景需求")
        if (item["operation"] == "preserve") != (attribute == "preservation"):
            raise ValueError("Qwen补充需求的操作与属性不一致")
        confidence = item.get("confidence")
        if type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0.7 <= confidence <= 1:
            raise ValueError("Qwen补充需求不够明确，请修改后重试")
        preserve = item.get("preserve", [])
        if not isinstance(preserve, list) or len(preserve) > 10:
            raise ValueError("Qwen保留对象列表无效")
        instruction = english(item.get("instruction_en"), 700)
        # 对象引用由程序生成；英文正文不得偷偷绑定其他人物或素材。
        if re.search(r"\b(subject|picture|video|track)\s*#?\s*\d", instruction, re.IGNORECASE):
            raise ValueError("Qwen指令不得自行引用人物或素材编号")
        directives.append({"operation": item["operation"], "scope": scope, "subject_index": index,
                           "attribute": attribute, "instruction_en": instruction,
                           "preserve": [english(x, 160) for x in preserve], "confidence": float(confidence)})
    protected_subjects = (
        [index for index in range(1, len(people) + 1) if index != target["subject_index"]]
        if target["kind"] == "subject" else []
    )
    return {"schema": "clone.supplement/v2", "source_text": supplement["text"],
            "target": copy.deepcopy(target), "directives": directives,
            "protected_subjects": protected_subjects, "model": "Qwen3-VL-4B Q4_K_M"}


def merge_prompt(prompt, plan):
    sections = split_prompt(prompt)
    lines, preserved = [], []
    for item in plan["directives"]:
        label = f"<Subject {item['subject_index']}>" if item["scope"] == "subject" else "the environment"
        lines.append(f"- Apply to {label} only ({item['attribute']}): {item['instruction_en']}")
        preserved.extend(item["preserve"])
    sections["subject_definitions"] += "\nUser overrides below affect this generation only; subject identities, media bindings, depth geometry, camera, cuts and timing remain fixed."
    if preserved:
        sections["retention_analysis"] += "\nUser-requested preservation: " + "; ".join(dict.fromkeys(preserved)) + "."
    protected_subjects = plan.get("protected_subjects", [])
    if protected_subjects:
        sections["retention_analysis"] += "\nScope protection: " + "; ".join(
            f"<Subject {index}> must keep its assigned identity and full reference appearance"
            for index in protected_subjects
        ) + ". Never apply the selected subject's edits to these people."
        sections["retention_analysis"] += (
            "\nEnvironment protection: retain the depth-defined spatial layout, "
            "prop positions, camera motion and cuts. Surface appearance may vary in generation."
        )
    sections["detailed_description"] = (
        "User-requested overrides for this generation only:\n" + "\n".join(lines)
        + "\nOnly the listed attributes change; all other reference attributes remain unchanged.\n"
        + sections["detailed_description"]
    )
    result = "\n\n".join(f"{key}\n{sections[key]}" for key in SECTIONS)
    if len(result) > 18000:
        raise ValueError("补充合并后的提示词过长，请减少内容")
    return result
