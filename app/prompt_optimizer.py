"""工作台与 Web 一致的 MiniMax H3 提示词自动编译服务。"""

from __future__ import annotations

import ctypes
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from .clone_edit_contract import (
    replacement_contract,
    replacement_prompt_issues,
    replacement_rules,
)
from .config import ASSISTANT_PROVIDERS_FILE

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "desktop-workbench" / "src-tauri" / "prompts"
H3_PROMPT_MAX_CHARS = 7000

Completion = Callable[[str, dict, bool], str]


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _decrypt_assistant_key(encrypted: str) -> str:
    if not encrypted:
        return ""
    if encrypted.startswith('node:v1:'):
        from .assistant_secrets import open_node_key
        return open_node_key(encrypted, ROOT)
    if os.name != "nt":
        raise RuntimeError("AI 助手 API Key 只能由保存它的 Windows 账户解密")
    try:
        source = bytes.fromhex(encrypted)
    except ValueError as exc:
        raise ValueError("AI 助手 API Key 密文无效") from exc
    buffer = (ctypes.c_ubyte * len(source)).from_buffer_copy(source)
    input_blob = _DataBlob(len(source), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob))
    if not ok:
        raise RuntimeError("无法使用当前 Windows 账户读取 AI 助手 API Key，请在工作台重新保存配置")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def load_assistant_providers(path: Path = ASSISTANT_PROVIDERS_FILE) -> list[dict]:
    for attempt in range(3):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            if attempt == 2:
                raise ValueError("AI 助手共享配置读取失败，请在工作台重新保存") from exc
            time.sleep(0.05)
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("AI 助手共享配置无效")
    providers = []
    for item in value:
        if not isinstance(item, dict) or "apiKey" in item:
            raise ValueError("AI 助手共享配置包含无效或明文密钥字段")
        base_url = item.get("baseUrl")
        model = item.get("defaultModel")
        if item.get("enabled", True) is False:
            continue
        if not isinstance(base_url, str) or not base_url.strip() or not isinstance(model, str) or not model.strip():
            continue
        if not str(item.get("encryptedKey") or "").strip():
            from urllib.parse import urlparse
            if urlparse(base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
                continue
        providers.append({
            "id": str(item.get("id") or "")[:100],
            "name": str(item.get("name") or model)[:100],
            "baseUrl": base_url.strip(),
            "apiType": str(item.get("apiType") or "OpenAI Chat Completions API")[:100],
            "defaultModel": model.strip()[:300],
            "encryptedKey": str(item.get("encryptedKey") or "")[:8192],
        })
    return providers


def _normalize_provider_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        raise ValueError('AI 助手服务地址为空')
    if not value.lower().startswith(('http://', 'https://')):
        local = value.lower().startswith(('localhost', '127.', '[::1]'))
        value = f"{'http' if local else 'https'}://{value}"
    url = urlsplit(value)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请输入不含密钥、查询参数的 HTTP 服务地址')
    path = re.sub(r'/(responses|chat/completions|models)/?$', '', url.path.rstrip('/'), flags=re.I)
    return urlunsplit((url.scheme, url.netloc, path or '/v1', '', ''))


def _responses_body(chat_body: dict, model: str) -> dict:
    messages = chat_body.get("messages", [])
    system = next((message.get("content", "") for message in messages if message.get("role") == "system"), "")
    user = next((message.get("content", "") for message in messages if message.get("role") == "user"), "")
    if isinstance(user, str):
        input_value: object = user
    else:
        content = []
        for item in user if isinstance(user, list) else []:
            if item.get("type") == "text":
                content.append({"type": "input_text", "text": item.get("text", "")})
            elif item.get("type") == "image_url":
                content.append({"type": "input_image", "image_url": item.get("image_url", {}).get("url", "")})
        input_value = [{"role": "user", "content": content}]
    return {"model": model, "instructions": system, "input": input_value}


def _provider_completion(provider: dict, api_key: str) -> Completion:
    responses = provider["apiType"] == "OpenAI Responses API"
    endpoint = f"{_normalize_provider_base_url(provider['baseUrl'])}/{'responses' if responses else 'chat/completions'}"

    def complete(_stage: str, body: dict, _multimodal: bool) -> str:
        request_body = _responses_body(body, provider["defaultModel"]) if responses else {**body, "model": provider["defaultModel"]}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        request = urllib.request.Request(endpoint, data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message")
            except Exception:
                detail = None
            raise RuntimeError(f"AI 助手接口返回 HTTP {exc.code}{f'：{detail}' if detail else ''}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("AI 助手接口连接失败或超时") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("AI 助手接口返回了无效 JSON") from exc
        if responses:
            text = "\n".join(
                content.get("text", "")
                for output in value.get("output", []) if isinstance(output, dict)
                for content in output.get("content", []) if isinstance(content, dict) and content.get("type") == "output_text"
            )
        else:
            content = value.get("choices", [{}])[0].get("message", {}).get("content", "")
            text = content if isinstance(content, str) else "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
        if not text.strip():
            raise RuntimeError("AI 助手接口没有返回可用文本")
        return text.strip()
    return complete


def is_h3_prompt(text: str) -> bool:
    return bool(re.match(
        r"^(?:subject_definitions:|integrated_multimodal_description:|For the target video,|How the reference pictures align with the target video)",
        str(text).lstrip(),
    ))


def _required_sections(mode: str) -> list[str]:
    if mode == "references":
        return ["subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"]
    return ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"]


def _media_tags(text: str) -> list[str]:
    return [f"<{kind} {number}>" for kind, number in re.findall(r"<(Picture|Video|Audio) (\d+)>", text)]


def _subject_tags(text: str) -> list[str]:
    return list(dict.fromkeys(f"<Subject {number}>" for number in re.findall(r"<Subject (\d+)>", text)))


def _normalize_h3_sections(text: str, mode: str) -> str:
    """Normalize common small-model variants such as bare or Markdown section names."""
    text = re.sub(r"^\s*```(?:text|markdown)?\s*|\s*```\s*$", "", text.strip(), flags=re.IGNORECASE)
    expected = _required_sections(mode)
    names = "|".join(map(re.escape, expected))
    pattern = re.compile(
        rf"(?im)^[ \t]*(?:#{{1,6}}[ \t]*)?(?:\*\*|__)?({names})(?:\*\*|__)?[ \t]*:?[ \t]*(?:\*\*|__)?[ \t]*"
    )
    matches = list(pattern.finditer(text))
    counts = {name: sum(match.group(1).lower() == name for match in matches) for name in expected}
    if matches and all(counts[name] == 1 for name in expected):
        sections: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections[match.group(1).lower()] = text[match.end():end].strip()
        prefix = text[:matches[0].start()].strip()
        normalized = "\n".join(f"{name}:\n{sections[name]}" for name in expected)
        if mode != "references" and prefix.startswith(("For the target video,", "How the reference pictures align with the target video")):
            return f"{prefix}\n\n{normalized}".strip()
        return normalized.strip()
    return pattern.sub(lambda match: f"{match.group(1).lower()}:\n", text).strip()


def _replace_unknown_media_tags(text: str, request: dict) -> str:
    available = {str(asset["tag"]) for asset in request["manifest"]}
    replacements = {
        "Picture": "the described visual reference",
        "Video": "the described motion",
        "Audio": "the described sound",
    }

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        return tag if tag in available else replacements[match.group(1)]

    return re.sub(r"<(Picture|Video|Audio) \d+>", replace, text)


def _remove_phantom_references(text: str, request: dict) -> str:
    """Qwen occasionally applies the guide's first-frame example to text-only jobs."""
    if request["manifest"]:
        return text
    header = "integrated_multimodal_description:"
    if header in text:
        text = text[text.index(header):]
    text = re.sub(r"<(?:Picture|Video|Audio|Subject) \d+>", "the described scene", text)
    text = re.sub(r"\bas (?:established|shown|defined) by the reference\b", "as described by the user", text, flags=re.IGNORECASE)
    return text.strip()


def _normalize_model_prompt(text: str, request: dict) -> str:
    text = _normalize_h3_sections(text, request["mode"])
    text = _remove_phantom_references(text, request)
    text = _replace_unknown_media_tags(text, request)
    if request["mode"] == "references" and not any(asset["kind"] == "Video" for asset in request["manifest"]):
        text = re.sub(r"\[(?:video editing|video continuation)\b", "[reference generation", text, flags=re.IGNORECASE)
    return _normalize_h3_sections(text, request["mode"])


def _has_static_request(text: str) -> bool:
    lower = text.casefold()
    return any(term in lower for term in (
        "static shot", "static camera", "locked camera", "fixed camera", "no camera movement",
        "静止机位", "固定机位", "镜头不动",
    ))


def _edit_source_tag(request: dict) -> str:
    return next((asset['tag'] for asset in request.get('manifest', [])
                 if asset.get('kind') == 'Video' and asset.get('role') == 'edit_source'), '')


def _has_dynamic_direction(text: str) -> bool:
    lower = text.casefold()
    camera = any(term in lower for term in (
        "camera", "push", "pull", "pan", "tilt", "track", "dolly", "truck", "arc", "orbit",
        "handheld", "镜头", "运镜", "前推", "后拉", "横移", "环绕", "跟随",
    ))
    progression = any(term in lower for term in (
        "then", "followed", "finally", "before", "after", "随后", "紧接着", "最后", "先", "同时",
    ))
    return camera and progression


def _duration_directing_plan(duration: float, image_count: int) -> str:
    if duration <= 3.5:
        shots = "一个连续镜头，禁止无依据切镜"
    elif duration <= 7.5:
        shots = "一到两个镜头，默认优先一个连续镜头"
    elif duration <= 11.5:
        shots = "两到三个镜头，按动作转折或空间揭示切换"
    else:
        shots = "最多三个镜头，保留开场、发展和收束"
    image_rule = (
        "仅一张图片时保持同一主体、空间和时间的连续动作，不得无依据改变机位、地点或时段。"
        if image_count == 1 else
        "多张图片按顺序建立连续关系，但不能机械按图片数量切镜。"
    )
    return f"目标时长为 {duration:g} 秒：{shots}。{image_rule} 所有 H3 时间点、首尾帧对齐和切镜必须落在该总时长内。"


def _validate_request(request: dict) -> None:
    mode = request.get("mode")
    if mode not in {"references", "keyframes"}:
        raise ValueError("提示词优化模式无效")
    try:
        duration = float(request.get("duration"))
    except (TypeError, ValueError) as exc:
        raise ValueError("提示词优化时长无效") from exc
    if not math.isfinite(duration) or not 1 <= duration <= 30:
        raise ValueError("提示词优化时长必须在 1 到 30 秒之间")
    request["duration"] = duration
    prompt = request.get("prompt", "")
    assets = request.get("assets", "")
    images = request.get("images", [])
    video_frames = request.get("videoFrames", [])
    manifest = request.get("manifest", [])
    if not isinstance(prompt, str) or len(prompt) > 10000:
        raise ValueError("原始提示词无效或过长")
    if not isinstance(assets, str) or len(assets) > 30000:
        raise ValueError("素材清单无效或过长")
    if not isinstance(images, list) or len(images) > 9:
        raise ValueError("参考图片数据无效或超过 9 张")
    if not isinstance(video_frames, list) or len(video_frames) > 15:
        raise ValueError("视频抽帧数据无效或超过 15 张")
    if not isinstance(manifest, list) or len(manifest) > 15:
        raise ValueError("素材清单数量无效")
    for image in images:
        if not isinstance(image, dict) or not str(image.get("dataUrl", "")).startswith("data:image/") or ";base64," not in image["dataUrl"]:
            raise ValueError("参考图片数据无效，请重新选择")
    for frame in video_frames:
        if (not isinstance(frame, dict) or not str(frame.get("tag", "")).startswith("<Video ")
                or not str(frame.get("dataUrl", "")).startswith("data:image/") or ";base64," not in frame["dataUrl"]):
            raise ValueError("视频抽帧数据无效，请重新选择参考视频")
    for asset in manifest:
        if (not isinstance(asset, dict) or asset.get("kind") not in {"Picture", "Video", "Audio"}
                or not re.fullmatch(r"<(?:Picture|Video|Audio) [1-9]\d*>", str(asset.get("tag", "")))):
            raise ValueError("素材清单包含无效引用")
    if not prompt.strip() and not images and not video_frames:
        raise ValueError("智能创作至少需要一张可解析的图片或视频")


def _prompt_issues(text: str, request: dict) -> list[str]:
    issues: list[str] = []
    mode = request["mode"]
    duration = request["duration"]
    manifest = request["manifest"]
    if len(text) > H3_PROMPT_MAX_CHARS:
        issues.append(f"长度为 {len(text)}，超过 {H3_PROMPT_MAX_CHARS} 字符")
    expected = _required_sections(mode)
    headers = [name for name in re.findall(r"^([a-z_]+):", text, re.MULTILINE) if name in expected]
    if headers != expected:
        issues.append(f"H3 字段应按顺序且各出现一次：{' → '.join(expected)}")
    for index, name in enumerate(expected):
        match = re.search(rf"^{re.escape(name)}:", text, re.MULTILINE)
        if not match:
            continue
        end_match = re.search(rf"^{re.escape(expected[index + 1])}:", text[match.end():], re.MULTILINE) if index + 1 < len(expected) else None
        end = match.end() + end_match.start() if end_match else len(text)
        if not text[match.end():end].strip():
            issues.append(f"{name} 内容为空")
    available = {str(asset["tag"]) for asset in manifest}
    missing = [tag for tag in available if tag not in text]
    if missing:
        issues.append(f"缺少素材引用：{'、'.join(sorted(missing))}")
    for tag in _media_tags(text):
        if tag not in available:
            issues.append(f"引用 {tag} 没有对应素材")
    if mode != "references":
        first = any(asset.get("role") == "first_frame" for asset in manifest)
        last = any(asset.get("role") == "last_frame" for asset in manifest)
        if first and not last and not text.lstrip().startswith("For the target video, at 0.00 seconds into the target video, <Picture 1>"):
            issues.append("缺少首帧对齐指令")
        if last and (not text.lstrip().startswith("How the reference pictures align with the target video") or f"{duration:.2f}-second mark" not in text):
            issues.append("缺少正确的尾帧对齐指令")
    if mode == "references":
        definitions = text.split("summary:", 1)[0]
        subjects = _subject_tags(text)
        if any(asset["kind"] != "Audio" for asset in manifest) and not subjects:
            issues.append("多参考提示词未归纳 <Subject 1> 可见主体")
        summary = text.split("summary:", 1)[-1].split("retention_analysis:", 1)[0].lstrip()
        if not re.match(r"\[(?:reference generation|keyframe completion|video editing|video continuation|audio reuse|audio reference)", summary):
            issues.append("summary 缺少 H3 任务类型")
        retention = text.split("retention_analysis:", 1)[-1].split("detailed_description:", 1)[0]
        for tag in subjects:
            if tag not in definitions or tag not in retention:
                issues.append(f"{tag} 缺少定义或保留关系")
        if _edit_source_tag(request):
            issues.extend(replacement_prompt_issues(text, replacement_contract(request)))
    timeline_key = "detailed_description:" if mode == "references" else "integrated_multimodal_description:"
    timeline = text.split(timeline_key, 1)[-1].split("overall_soundscape:", 1)[0]
    shots = list(re.finditer(r"\[Shot (\d+)\](?: At (\d{2}):(\d{2})\.(\d{3}))?", timeline))
    if not shots or shots[0].group(1) != "1" or shots[0].group(2):
        issues.append("首镜须为不带切换时间的 [Shot 1]")
    previous = 0.0
    for index, shot in enumerate(shots[1:], 2):
        groups = shot.groups()
        seconds = (int(groups[1]) * 60 + int(groups[2]) + int(groups[3]) / 1000) if groups[1] else math.nan
        if int(groups[0]) != index or not math.isfinite(seconds) or seconds <= previous or seconds >= duration:
            issues.append(f"镜头 {index} 的编号或切换时间无效")
            break
        previous = seconds
    if not _edit_source_tag(request) and not _has_static_request(request["prompt"]) and _has_static_request(text):
        issues.append("包含静止机位，但用户未要求固定镜头")
    if not _edit_source_tag(request) and not _has_static_request(request["prompt"]) and not _has_dynamic_direction(text):
        issues.append("缺少可执行的主导运镜或连续动作节拍")
    return list(dict.fromkeys(issues))


def _parse_reference_plan(raw: str, request: dict) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("引用规划未返回完整 JSON 对象")
    try:
        plan = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("引用规划 JSON 格式无效，请重试") from exc
    manifest = request["manifest"]
    manifest_tags = {asset["tag"] for asset in manifest}
    roles = plan.get("asset_roles")
    if not isinstance(roles, list):
        raise ValueError("引用规划缺少 asset_roles")
    normalized_roles: list[dict] = []
    for asset in manifest:
        matches = [row for row in roles if isinstance(row, dict) and row.get("tag") == asset["tag"]]
        if len(matches) != 1:
            raise ValueError(f"引用规划未明确 {asset['tag']} 的唯一用途")
        row = dict(matches[0])
        default_role = asset.get("role") or {
            "Picture": "visual_reference", "Video": "motion_reference", "Audio": "audio_reference",
        }[asset["kind"]]
        row["role"] = str(row.get("role") or default_role).strip()
        if asset.get("role") == "edit_source":
            row["role"] = "edit_source"
        row["observed"] = str(row.get("observed") or "以实际上传素材为准").strip()
        row["target_use"] = str(row.get("target_use") or {
            "Picture": "保留可见外观、构图或场景信息",
            "Video": "仅使用带时间戳抽帧可证实的动作与节奏",
            "Audio": "仅作为已上传音频素材使用，不臆造未听取内容",
        }[asset["kind"]]).strip()
        normalized_roles.append(row)
    if any(row.get("tag") not in manifest_tags for row in roles if isinstance(row, dict)):
        raise ValueError("引用规划出现未上传素材")
    plan["asset_roles"] = normalized_roles

    raw_subjects = plan.get("subjects") if isinstance(plan.get("subjects"), list) else []
    subjects: list[dict] = []
    for subject in raw_subjects:
        if not isinstance(subject, dict):
            continue
        sources = [source for source in subject.get("sources", []) if source in manifest_tags] if isinstance(subject.get("sources"), list) else []
        if not sources:
            continue
        subjects.append({**subject, "label": f"<Subject {len(subjects) + 1}>", "sources": sources,
                         "identity": str(subject.get("identity") or f"可见主体或场景，来源为 {', '.join(sources)}").strip()})
    if not subjects:
        subjects = [
            {"label": f"<Subject {index}>", "sources": [asset["tag"]],
             "identity": f"以 {asset['tag']} 中实际可见的主体、物体或场景为准"}
            for index, asset in enumerate((item for item in manifest if item["kind"] != "Audio"), 1)
        ]
    plan["subjects"] = subjects

    raw_shots = plan.get("shot_plan") if isinstance(plan.get("shot_plan"), list) and not _edit_source_tag(request) else []
    shots: list[dict] = []
    last = -1.0
    for shot in raw_shots:
        try:
            start_seconds = float(shot.get("start_seconds"))
        except (AttributeError, TypeError, ValueError):
            continue
        if start_seconds <= last or start_seconds >= request["duration"] or not str(shot.get("action", "")).strip():
            continue
        shots.append({**shot, "start_seconds": start_seconds, "action": str(shot["action"]).strip()})
        last = start_seconds
    if not _edit_source_tag(request) and (not shots or shots[0]["start_seconds"] != 0):
        shots = [{"start_seconds": 0.0, "action": request["prompt"].strip() or "依据实际素材创作连续动作与镜头"}]
    plan["shot_plan"] = shots
    if _edit_source_tag(request):
        plan["motion_policy"] = "preserve_source"
    plan["task_types"] = plan.get("task_types") if isinstance(plan.get("task_types"), list) and plan["task_types"] else ["reference generation"]
    if _edit_source_tag(request) and "video editing" not in plan["task_types"]:
        plan["task_types"].insert(0, "video editing")
    plan["story_goal"] = str(plan.get("story_goal") or request["prompt"] or "依据素材智能创作").strip()
    return plan


def _fallback_reference_plan(request: dict) -> dict:
    roles = []
    for asset in request["manifest"]:
        roles.append({
            "tag": asset["tag"],
            "role": asset.get("role") or {"Picture": "visual_reference", "Video": "motion_reference", "Audio": "audio_reference"}[asset["kind"]],
            "observed": "以实际上传素材为准",
            "target_use": {"Picture": "保留可见外观、构图或场景信息", "Video": "仅使用抽帧可证实的动作与节奏", "Audio": "使用上传的音频参考"}[asset["kind"]],
        })
    subjects = [
        {"label": f"<Subject {index}>", "sources": [asset["tag"]],
         "identity": f"以 {asset['tag']} 中实际可见的主体、物体或场景为准"}
        for index, asset in enumerate((item for item in request["manifest"] if item["kind"] != "Audio"), 1)
    ]
    goal = request["prompt"].strip() or "依据实际素材智能创作"
    return {
        "asset_roles": roles,
        "subjects": subjects,
        "task_types": ["reference generation"],
        "story_goal": goal,
        "shot_plan": [] if _edit_source_tag(request) else [{"start_seconds": 0.0, "action": goal}],
        **({"motion_policy": "preserve_source"} if _edit_source_tag(request) else {}),
        "planning_fallback": True,
    }


def _fallback_h3_prompt(request: dict, plan: dict) -> str:
    brief = _replace_unknown_media_tags(request["prompt"].strip(), request)
    if len(brief) > 3000:
        brief = brief[:3000].rstrip() + "…"
    if not brief:
        brief = "Create a coherent short video from the uploaded visual references."
    manifest = request["manifest"]
    asset_tags = [asset["tag"] for asset in manifest]
    asset_text = ", ".join(asset_tags) if asset_tags else "the user-described scene"
    static = _has_static_request(request["prompt"])
    edit_source = _edit_source_tag(request)
    camera = f"The camera and shot order follow {edit_source}, including any fixed camera or source movement." if edit_source else "The camera remains fixed exactly as requested." if static else (
        "The camera begins in a clear establishing composition, then tracks the main action with one motivated smooth movement, "
        "and finally settles on the completed result."
    )
    timeline = (
        f"[Shot 1] Follow the user's direction exactly: {brief} Use {asset_text} only for their assigned visible roles. "
        f"Begin with a readable start state, then execute the main action with physically coherent contact and environmental response. "
        f"{camera} Finally hold the resolved end state within the {request['duration']:g}-second duration."
    )
    sound = "Use only sounds explicitly requested by the user or directly supported by the uploaded audio/video assets; otherwise keep natural quiet ambience."

    if request["mode"] != "references":
        prefix = ""
        first = any(asset.get("role") == "first_frame" for asset in manifest)
        last = any(asset.get("role") == "last_frame" for asset in manifest)
        if last:
            alignments = ["How the reference pictures align with the target video:"]
            if first:
                alignments.append("At 0.00 seconds into the target video, <Picture 1> is fully referenced as the first frame.")
            last_tag = next(asset["tag"] for asset in manifest if asset.get("role") == "last_frame")
            alignments.append(f"At the {request['duration']:.2f}-second mark, {last_tag} is fully referenced as the last frame.")
            prefix = "\n".join(alignments) + "\n\n"
        elif first:
            prefix = "For the target video, at 0.00 seconds into the target video, <Picture 1> is fully referenced.\n\n"
        return (
            f"{prefix}integrated_multimodal_description:\n{timeline}\n"
            f"overall_soundscape:\n{sound}\n"
            "non_diegetic_music:\nN/A"
        )

    subjects = plan.get("subjects") if isinstance(plan.get("subjects"), list) else []
    definitions = []
    retention = []
    for subject in subjects:
        label = subject.get("label")
        sources = [source for source in subject.get("sources", []) if source in asset_tags]
        if not label or not sources:
            continue
        definitions.append(f"{label} is the visible subject, object, or environment defined only by {', '.join(sources)}; preserve observable facts without inventing identity.")
        retention.append(f"{label}: fully_preserved — preserve its observable appearance and assigned role from {', '.join(sources)}.")
    for asset in manifest:
        marker = "partially_preserved" if asset['tag'] == edit_source else "fully_preserved" if asset["kind"] != "Video" else "attribute_transfer"
        retention.append(f"{asset['tag']}: {marker} — use only the uploaded {asset['kind'].lower()} content for its assigned role.")
    if edit_source:
        definitions.append(f"{edit_source} is the source video to edit; preserve its scene, action, camera and timing except for the user's explicit changes.")
    if not definitions:
        definitions.append("The scene contains no independently defined visible subject; use only the supplied audio reference and user direction.")
    subject_text = ", ".join(subject.get("label", "") for subject in subjects if subject.get("label")) or asset_text
    summary = (f"[video editing] The target video is an edited version of {edit_source}. Follow the user direction with {subject_text}."
               if edit_source else f"[reference generation] Follow the user direction with {subject_text}, using only uploaded assets.")
    return (
        "subject_definitions:\n" + "\n".join(definitions) + "\n"
        f"summary:\n{summary}\n"
        "retention_analysis:\n" + "\n".join(retention) + "\n"
        f"detailed_description:\n{timeline}\n"
        f"overall_soundscape:\n{sound}\n"
        "non_diegetic_music:\nN/A"
    )


def _multimodal_body(request: dict, system: str, input_text: str) -> dict:
    content: list[dict] = [{"type": "text", "text": input_text}]
    for image in request["images"]:
        content.extend([
            {"type": "text", "text": f"{image['tag']}：{image.get('name', '')}。请先分析这张实际图片，再继续处理下一张。"},
            {"type": "image_url", "image_url": {"url": image["dataUrl"]}},
        ])
    for frame in request["videoFrames"]:
        content.extend([
            {"type": "text", "text": f"{frame['tag']}：{frame.get('name', '')} 在 {float(frame.get('timeSeconds') or 0):.2f} 秒的实际抽帧。只根据抽帧和时间顺序判断可见动作，不要声称已经听到音轨。"},
            {"type": "image_url", "image_url": {"url": frame["dataUrl"]}},
        ])
    return {"model": "Qwen3-VL-4B-Q4_K_M", "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}


def _text_body(system: str, input_text: str) -> dict:
    return {"model": "Qwen3-VL-4B-Q4_K_M", "messages": [{"role": "system", "content": system}, {"role": "user", "content": input_text}]}


def optimize_prompt(request: dict, complete: Completion) -> dict:
    request = dict(request)
    request.setdefault("prompt", "")
    request.setdefault("assets", "")
    request.setdefault("images", [])
    request.setdefault("videoFrames", [])
    request.setdefault("manifest", [])
    _validate_request(request)
    edit_rules = replacement_rules(replacement_contract(request)) if _edit_source_tag(request) else ""
    guide_name = "ref-cn.txt" if request["mode"] == "references" else "base-cn.txt"
    try:
        guide = (PROMPT_DIR / guide_name).read_text(encoding="utf-8")
        director_rules = (PROMPT_DIR / ("clone-cn.txt" if _edit_source_tag(request) else "director-cn.txt")).read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("工作台 H3 提示词规范文件缺失，无法执行自动优化") from exc

    plan: dict = {}
    if request["manifest"]:
        timeline_schema = '"motion_policy":"preserve_source"' if _edit_source_tag(request) else '"shot_plan":[{"start_seconds":0,"action":"可执行动作与镜头"}]'
        planning_system = (
            "你是 MiniMax H3 素材引用规划器。先依据用户原话和真实图片/带时间戳视频抽帧，按实际用途建立主体与引用关系。只输出 JSON 对象，不要 Markdown："
            '{"asset_roles":[{"tag":"<Picture 1>","role":"identity/wardrobe/environment/first_frame/last_frame/motion/edit_source/continuation/audio_reference 等具体用途","observed":"可见事实或未观察到","target_use":"目标画面中如何使用"}],'
            '"subjects":[{"label":"<Subject 1>","sources":["<Picture 1>"],"identity":"可见身份特征与用途"}],"task_types":["reference generation"],"story_goal":"用户目标",'
            + timeline_schema + '}。'
            "asset_roles 必须按素材清单为每个实际素材恰好一项，禁止新增不存在的标签；同一个人物的多图可合并为一个 Subject，但必须有用户说明或可见证据。视频只能根据收到的抽帧推断动作顺序，不得虚构未观察的片段；音频未提供可听内容，不得臆造台词或音色。图片仅提供外观时写入 Subject 来源，仅作为实际帧锚点时单独使用 Picture。用户明确要求优先，保留原文对白和否定约束。"
        )
        planning_system += edit_rules
        if _edit_source_tag(request):
            planning_system += "\n克隆中心只识别素材用途和编辑需求，不规划新的运镜、动作或镜头。动作、运镜及切镜沿用原视频。\n" + director_rules
        planning_input = f"模式：{request['mode']}\n目标时长：{request['duration']:g} 秒\n素材清单：\n{request['assets']}\n\n用户原始需求：\n{request['prompt']}"
        raw = complete("planning", _multimodal_body(request, planning_system, planning_input), True)
        try:
            plan = _parse_reference_plan(raw, request)
        except ValueError as error:
            timeline_rule = "不输出动作或运镜规划，motion_policy 必须为 preserve_source。" if _edit_source_tag(request) else "shot_plan 是镜头起点，首项必须为 0，后续时间严格递增且小于总时长，每项必须含非空 action。"
            repair_input = f"以下 JSON 引用规划未通过检查：{error}。目标总时长为 {request['duration']:g} 秒；{timeline_rule}保持用户原始需求与真实素材绑定，重新输出一个完整 JSON 对象，不要解释。\n素材：{request['assets']}\n用户需求：{request['prompt']}\n原规划：\n{raw}"
            repaired = complete("planning-repair", _multimodal_body(request, planning_system, repair_input), True)
            try:
                plan = _parse_reference_plan(repaired, request)
            except ValueError:
                plan = _fallback_reference_plan(request)

    creative = not request["prompt"].strip()
    task = (
        "用户没有输入创意。按图片出现顺序逐张分析可见主体、动作状态、空间关系、光线和构图，推导自然的因果与过渡，创作一个连贯且有视觉亮点的短视频故事。不要把多张图写成互不相关的静态清单，也不要虚构图片无法支持的身份、对白或事实。"
        if creative else
        "用户提示词是最高优先级的导演意图。先理解用户明确要求，再用图片中实际可见的信息补足主体外观、场景、动作衔接、镜头和声音；图片与用户意图有歧义时保留用户意图，不擅自改写核心剧情、对白或风格。"
    )
    edit_source = _edit_source_tag(request)
    source_rules = (
        f"本次是 {edit_source} 的源视频编辑。源视频的构图、机位、运动与切镜顺序优先于通用创作运镜规则；"
        "用户明确要求的改变优先于源视频原状。原片静止机位合法，未要求改机位时不能强加推拉摇移，不能按参考图片数或时长凭空分镜。"
        "只按带时间戳抽帧描述可见动作；替换主体的写实/卡通等渲染风格以用户明确需求和目标图片为准，不能被源主体风格覆盖。"
        "默认只修改用户明确指定的对象和属性；原片字幕、文字、贴纸、背景、其他对象、动作、坐姿、镜头和原声均保留。"
        "不得为了更写实而自行重编动作、改变姿态、移除字幕或饰物；只有用户明确要求的改变才可覆盖对应源内容。"
        + edit_rules if edit_source else ""
    )
    directing_plan = (f"目标时长为 {request['duration']:g} 秒。{source_rules}" if edit_source
                      else _duration_directing_plan(request["duration"], len(request["images"])))
    asset_rule = (
        "本次没有上传任何参考素材。禁止输出 Picture、Video、Audio、Subject 标签或任何首尾帧对齐前言；直接从 integrated_multimodal_description: 开始。"
        if not request["manifest"] else
        "仅可使用素材清单中真实存在的 Picture、Video、Audio 标签，禁止新增素材编号。"
    )
    plan_json = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    system = (
        f"你是 MiniMax H3 视频提示词导演与多模态素材分析器。{task} 依据已核对的引用规划建立稳定的 Subject 与 Picture/Video/Audio 关系；所有上传素材都要在最终文本中按实际用途引用，不得新增或调换编号。"
        f"视频动作只可依据实际带时间戳抽帧描述，不能把未提供的音轨当作已听到。只输出最终可直接提交的英文结构化提示词，不要解释、标题或 Markdown 代码块；对白、歌词、画面文字保留原语言。素材文件名只是辅助信息，判断以实际画面为准。不要增加用户未要求且素材不能支持的品牌、真实人物身份、对白或声轨。{asset_rule} {directing_plan}\n\n"
        f"已核对的结构化引用规划：\n{plan_json}\n\n以下是从无限画布视频节点复用的自动导演与动作编排规则，必须执行：\n{director_rules}\n\n{source_rules}\n\n遵循以下完整 H3 指南：\n{guide}"
    )
    prompt = "（空，由你根据图片序列智能创作）" if creative else request["prompt"].strip()
    visual_note = (
        "以下图片是本次提交的真实视觉输入，顺序与 Picture/Video 编号严格一致。"
        if request["images"] or request["videoFrames"] else
        "本次没有上传任何视觉或音频素材；不得假设存在 Picture、Video、Audio 或 Subject。"
    )
    input_text = f"模式：{request['mode']}\n目标时长：{request['duration']:g} 秒\n素材清单：\n{request['assets']}\n\n用户提示词：\n{prompt}\n\n{visual_note}"
    text = _normalize_model_prompt(complete("generate", _multimodal_body(request, system, input_text), True).strip(), request)
    issues = _prompt_issues(text, request)
    for subject in plan.get("subjects", []):
        label = subject.get("label")
        if label and label not in text:
            issues.append(f"引用规划中的 {label} 未出现在最终提示词")
    if issues:
        motion_rule = "遵循用户和源视频的动作与机位，不强加动作节拍或 camera 运镜。" if edit_source else "每个动态镜头都要明确写出 camera 运镜，并用 then/followed by/finally 组织连续动作。"
        repair_system = f"你是 MiniMax H3 提示词质检与修复器。只输出修复后的最终英文 H3 提示词，不要解释或 Markdown。必须保留用户意图、全部真实素材引用、主体身份、动作因果、运镜、声音和正确字段；不得杜撰素材。{asset_rule} {motion_rule} 引用规划：\n{plan_json}\n严格执行以下导演规则和 H3 指南。\n{director_rules}\n{source_rules}\n{guide}"
        repair_input = f"目标时长：{request['duration']:g} 秒。{directing_plan}\n\n用户原始需求：{request['prompt']}\n\n质检发现：{'；'.join(issues)}。\n\n请在不超过 {H3_PROMPT_MAX_CHARS} 字符的前提下修复以下提示词；空格、标点和换行都计入。\n\n{text}"
        text = _normalize_model_prompt(complete("prompt-repair", _text_body(repair_system, repair_input), False).strip(), request)
    final_issues = _prompt_issues(text, request)
    if final_issues:
        text = _fallback_h3_prompt(request, plan)
        fallback_issues = _prompt_issues(text, request)
        if fallback_issues:
            raise ValueError(f"自动解析/润色与本地保守编译均未通过导演质检：{'；'.join(fallback_issues)}。系统未写入无效提示词，请重试。")
        plan = {**plan, "prompt_fallback": True, "model_prompt_issues": final_issues}
    return {"prompt": text, "plan": plan}


def optimize_local_prompt(request: dict) -> dict:
    """兼容旧调用；本地提示词优化已停用，保留原始文本。"""
    return passthrough_prompt(request)


def passthrough_prompt(request: dict) -> dict:
    prompt = request.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("未配置 AI 助手，请填写生成提示词")
    if len(prompt) > H3_PROMPT_MAX_CHARS:
        raise ValueError("提示词超过 7000 字符")
    return {"prompt": prompt, "plan": {}, "optimizer": {"source": "passthrough", "name": "原始提示词"}}


def optimize_preferred_prompt(request: dict) -> dict:
    try:
        providers = load_assistant_providers()
    except ValueError as exc:
        result = passthrough_prompt(request)
        result["optimizer"]["warning"] = f"AI 助手配置不可用，已使用原始提示词：{exc}"
        return result
    if not providers:
        return passthrough_prompt(request)
    prepared = dict(request)
    prepared.setdefault("prompt", "")
    prepared.setdefault("assets", "")
    prepared.setdefault("images", [])
    prepared.setdefault("videoFrames", [])
    prepared.setdefault("manifest", [])
    _validate_request(prepared)

    failures = []
    for provider in providers:
        try:
            api_key = _decrypt_assistant_key(provider["encryptedKey"])
            result = optimize_prompt(prepared, _provider_completion(provider, api_key))
            return {**result, "optimizer": {
                "source": "assistant", "id": provider["id"], "name": provider["name"], "model": provider["defaultModel"],
            }}
        except Exception as exc:
            failures.append(f"{provider['name']}：{exc}")
    result = passthrough_prompt(prepared)
    result["optimizer"]["warning"] = "AI 助手暂不可用，已使用原始提示词：" + "；".join(failures)
    return result
