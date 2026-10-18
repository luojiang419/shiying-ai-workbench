"""从明确的素材替换语句提取约束；不猜测复杂编辑需求。"""

from __future__ import annotations

import re


def replacement_contract(request: dict) -> dict | None:
    brief = request.get("user_prompt") or request.get("prompt", "")
    # 不把已经编译的 H3 文本反向解释为用户需求。
    if re.search(r"^subject_definitions\s*:", brief.lstrip(), re.IGNORECASE):
        return None
    brief = re.sub(r"(?<!<)(?:视频|video)\s*(\d+)", lambda m: f"<Video {int(m[1])}>", brief, flags=re.IGNORECASE)
    brief = re.sub(r"(?<!<)(?:图片|图像|picture)\s*(\d+)", lambda m: f"<Picture {int(m[1])}>", brief, flags=re.IGNORECASE)
    clauses = re.split(r"[。；;\n]", brief)
    pictures = set()
    whole_person = False
    for clause in clauses:
        clause = re.sub(r"(?:不要|不能|不是|do not|don't)\s*(?:只|仅|only).{0,12}(?:脸|face|facial)", "", clause, flags=re.IGNORECASE)
        if not re.search(r"<Video\s+1>", clause, re.IGNORECASE):
            continue
        if re.search(r"(?:不要|不必|无需|禁止|别|do not|don't)\s*(?:更换|替换|换|replace|change)", clause, re.IGNORECASE):
            continue
        if not re.search(r"更换为|替换为|替换成|换成|换为|替换|replace|swap", clause, re.IGNORECASE):
            continue
        tags = re.findall(r"<Picture\s+(\d+)>", clause, re.IGNORECASE)
        if not tags:
            continue
        pictures.update(int(number) for number in tags)
        person = bool(re.search(r"人物|角色|人(?=更换|替换|换成)|\b(?:person|people|woman|man|character)\b", clause, re.IGNORECASE))
        attribute_target = re.search(
            r"(?:人物|角色|人)[的\s]*(?:脸部|面部|脸|发型|头发|衣服|服装|衣着|背景)|"
            r"\b(?:person|woman|man|character)(?:'s|’s)\s+(?:face|hair|clothes|clothing|outfit)|"
            r"(?:只|仅)(?:改变|修改|更换|替换|换)?(?:脸|发型|衣服|服装|背景)",
            clause, re.IGNORECASE,
        )
        whole_person |= person and not attribute_target
    if not pictures:
        return None
    scope_brief = re.sub(r"(?:不要|不能|不是|do not|don't)\s*(?:只|仅|only).{0,12}(?:脸|face|facial)", "", brief, flags=re.IGNORECASE)
    face_only = bool(re.search(r"只.{0,4}(?:换脸|脸|面部)|仅.{0,4}(?:脸|面部)|only.{0,15}(?:face|facial)", scope_brief, re.IGNORECASE))
    return {"schema": "clone.replacement/v1", "source": "<Video 1>",
            "pictures": [f"<Picture {number}>" for number in sorted(pictures)],
            "whole_person": whole_person and not face_only, "instruction": brief.strip()}


def replacement_rules(contract: dict | None) -> str:
    if not contract:
        return ""
    rules = (
        "本次存在明确素材替换约束：" + str(contract) + "。必须执行替换，不能重播原视频。"
        "源 Video 的 retention 必须为 partially_preserved，并明确排除被替换的身份/属性，不能写 fully_preserved。"
        "summary 和 detailed_description 都必须明确目标图片到源对象的替换关系。"
    )
    if contract["whole_person"]:
        rules += (
            "用户要求更换整个人物，不是仅换脸。目标人物的脸、头发、体态和服装外观以指定图片为准；"
            "只有用户明确要求保留的属性（例如原衣服）才沿用源人物。姿态、动作、位置和场景沿用源视频。"
            "不要自行加上 only face/identity/hairstyle、保留源服装等缩小编辑范围的限制。"
            "只为替换后的目标演员建立人物 Subject；原演员不作为另一个可见演员 Subject，不得与目标身份混合。"
        )
    return rules


def replacement_prompt_issues(text: str, contract: dict | None) -> list[str]:
    if not contract:
        return []
    issues = []
    retention = re.split(r"retention_analysis\s*:?", text, flags=re.IGNORECASE)[-1]
    retention = re.split(r"detailed_description\s*:?", retention, flags=re.IGNORECASE)[0]
    for line in retention.splitlines():
        if re.match(r"\s*<Video\s+1>", line, re.IGNORECASE) and re.search(r"\bfully_preserved\b", line):
            issues.append("替换任务的源视频须标 partially_preserved，明确排除被替换属性")
    if contract["whole_person"] and re.search(
        r"only\s+(?:(?:her|his|the|their)\s+)?(?:facial identity|face|facial features)|only.{0,30}(?:identity and hairstyle|face and hair)",
        text, re.IGNORECASE,
    ):
        issues.append("不得将更换人物擅自缩小为仅换脸和发型")
    return issues
