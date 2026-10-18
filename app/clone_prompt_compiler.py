"""将角色引用、源运动和控制约束编译为唯一的H3六段提示词。"""

import math
import re


def clean(value, limit=600):
    # 模型返回的文本是数据，不允许它注入新的媒体/主体标签或章节。
    return re.sub(
        r"\s+", " ", re.sub(r"<[^>]*>|\[Shot[^]]*\]", "", str(value or ""))
    ).strip()[:limit]


def timestamp(seconds):
    return f"{int(seconds // 60):02d}:{seconds % 60:06.3f}"


def normalize_attributes(raw):
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key in (
        "hair",
        "upper_garment",
        "closure",
        "neckline",
        "sleeves",
        "lower_garment",
        "footwear",
    ):
        value = raw.get(key)
        if isinstance(value, dict):
            value = ", ".join(
                f"{part}: {clean(value[part], 70)}"
                for part in ("type", "color", "material", "length", "style", "pattern")
                if isinstance(value.get(part), str)
                and value[part].lower() not in {"unknown", "not visible", "n/a"}
            )
        if not isinstance(value, str) or value.lower() in {
            "unknown",
            "not visible",
            "n/a",
        }:
            continue
        if key == "closure" and value not in {"closed", "open", "partially_open"}:
            continue
        value = clean(value, 220)
        if value:
            result[key] = value
    return result


def compile_prompt(
    people,
    duration,
    depth_mode,
    observations=None,
    scene_description="",
    motion_observations=None,
    supplement_plan=None,
    reference_mode="extracted_depth",
):
    duration = float(duration)
    if (
        not math.isfinite(duration)
        or not 0 < duration <= 15
        or not 1 <= len(people) <= 9
    ):
        raise ValueError("提示词计划的人数或时长无效")
    if reference_mode == "prompt_video":
        return compile_lightweight_prompt(people, duration)
    if reference_mode not in {"extracted_depth", "depth_video", "source_video"}:
        raise ValueError("提示词计划的视频参考方式无效")
    uses_depth = reference_mode != "source_video"
    has_source_video = reference_mode == "source_video"
    environment_override = any(
        item["scope"] == "environment" and item["operation"] == "replace_or_modify"
        for item in (supplement_plan or {}).get("directives", [])
    )
    if environment_override:
        scene_description = "natural surfaces using the explicit user-requested appearance below; retain depth-defined spatial layout"
    overrides = {}
    for directive in (supplement_plan or {}).get("directives", []):
        if directive["operation"] == "replace_or_modify" and directive["scope"] == "subject":
            overrides.setdefault(directive["subject_index"], set()).add(directive["attribute"])
    subjects, references, definitions, retention = [], [], [], []
    for index, person in enumerate(people, 1):
        paths = person.get("prepared_images", person["images"])
        views = person.get("prepared_views", [])
        tags = []
        roles = []
        for offset, path in enumerate(paths):
            number = len(references) + 1
            view = (
                views[offset].get("view", "unknown")
                if offset < len(views)
                else "unknown"
            )
            references.append(
                {"picture": number, "subject": index, "path": path, "view": view}
            )
            tags.append(f"<Picture {number}>")
            roles.append(
                f"<Picture {number}> supplies the {view} view of this same character"
            )
        if not tags or len(references) > 9:
            raise ValueError("每个角色至少一张参考图，全部引用最多9张")
        subject = {
            "subject": index,
            "track_id": person.get("track_id", index),
            "position": person["position"],
            "appearance": clean(person.get("description")),
            "pictures": tags,
            "primary_picture": tags[0],
        }
        subject["appearance_attributes"] = normalize_attributes(
            person.get("appearance_attributes")
        )
        subjects.append(subject)
        definitions.append(
            f"<Subject {index}> is ONE character defined by {', '.join(tags)}. These are views of the SAME individual, never separate cast members. View roles: {'; '.join(roles)}. {tags[0]} is the primary identity and garment reference; other views fill unseen details and must not override it when details disagree. Appearance: {subject['appearance'] or 'the identity and clothing visible in the assigned pictures'}. Initial screen position: {subject['position']}."
        )
        changed = overrides.get(index, set())
        if changed:
            # 自由外观描述可能混有衣服/发型；避免将旧外观作为另一条硬约束。
            definitions[-1] = definitions[-1].split(" Appearance:")[0] + f" Initial screen position: {subject['position']}. Use the assigned identity; appearance follows the explicit attribute overrides below."
            subject["appearance_attributes"] = {
                key: value for key, value in subject["appearance_attributes"].items()
                if not ("clothing" in changed and key != "hair") and not ("hair" in changed and key == "hair")
            }
        if subject["appearance_attributes"]:
            definitions[-1] += (
                " Visible locked attributes: "
                + "; ".join(
                    f"{key}: {value}"
                    for key, value in subject["appearance_attributes"].items()
                )
                + ". Omitted attributes are unknown, not instructions to invent new features."
            )
        retention.append(
            f"<Subject {index}>: fully_preserved — retain identity, face, hair, body proportions and the SAME garments in every shot. Preserve collar, neckline, button closure, sleeve length, garment material, colors and shoes as shown; never copy the source person's outfit or open a closed garment after a cut."
        )
        if changed:
            retention[-1] = f"<Subject {index}>: partially_preserved — preserve reference identity and all attributes except the explicitly overridden {', '.join(sorted(changed))}. Keep the resulting appearance continuous across shots."
    if reference_mode == "extracted_depth":
        definitions.append(
            f"An aligned depth-control sequence derived from the selected source clip covers 0.00–{duration:.3f} seconds. It is control data only, not a visible reference asset: follow its framing, body motion, depth ordering, occlusion, camera trajectory and cut timing. The source RGB pixels and source-person identity are deliberately not provided to the generator."
        )
    elif reference_mode == "depth_video":
        definitions.append(
            f"An aligned depth-control sequence covers 0.00–{duration:.3f} seconds. It is control data only, not a visible reference asset: follow its framing, body motion, depth ordering, occlusion, camera trajectory and cut timing without rendering its grayscale pixels or inventing a source appearance."
        )
    else:
        definitions.append(
            f"<Video 1> is the source action video for 0.00–{duration:.3f} seconds; follow its visible framing, body motion, contacts, camera trajectory and cut timing while replacing the people with the assigned target identities."
        )
        retention.append(
            "<Video 1>: fully_preserved — preserve its visible shot order, action timing, composition, contacts and camera motion; replace only the source people with the assigned target identities and clothing."
        )
    if scene_description and (depth_mode == "professional" or reference_mode == "extracted_depth"):
        definitions.append(
            f"<Subject {len(people) + 1}> is the environment, with depth-defined geometry and appearance: {clean(scene_description, 900)}."
        )
        retention.append(
            f"<Subject {len(people) + 1}>: attribute_transfer — preserve spatial layout and described surface appearance."
        )
    events = []
    for entry in motion_observations or []:
        time = float(entry["time"])
        if not math.isfinite(time) or not 0 <= time < duration:
            raise ValueError("源镜头时间超出选定区间")
        events.append(
            {
                "time": time,
                "cut": bool(entry.get("cut")),
                "description": clean(entry.get("description"), 1000),
            }
        )
    mapping = {str(s["track_id"]): s["subject"] for s in subjects}
    for frame in observations or []:
        time = float(frame["time"])
        if math.isfinite(time) and 0 <= time < duration:
            for actor in frame.get("people", []):
                if str(actor["id"]) in mapping and actor.get("action"):
                    events.append(
                        {
                            "time": time,
                            "cut": False,
                            "description": f"<Subject {mapping[str(actor['id'])]}> {clean(actor['action'], 240)}",
                        }
                    )
    # 同一时刻的多人动作作为一个关键时点保留，不能在限量时丢掉某个角色。
    by_time = {}
    for event in sorted(events, key=lambda item: item["time"]):
        bucket = by_time.setdefault(
            event["time"], {"time": event["time"], "cut": False, "descriptions": []}
        )
        bucket["cut"] = bucket["cut"] or event["cut"]
        if event["description"] and event["description"] not in bucket["descriptions"]:
            bucket["descriptions"].append(event["description"])
    events = [
        {
            "time": event["time"],
            "cut": event["cut"],
            "description": " ".join(event["descriptions"]),
        }
        for event in by_time.values()
    ]
    cuts = sorted(
        {event["time"] for event in events if event["cut"] and event["time"] > 0}
    )
    starts = [0.0, *cuts]
    shots = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else duration
        shot_events = sorted(
            [e for e in events if start <= e["time"] < end], key=lambda e: e["time"]
        )
        unique = []
        for event in shot_events:
            if not unique or unique[-1]["description"] != event["description"]:
                unique.append(event)
        # 每镜保留开场、中间和结尾，避免近似静帧描述反复压过身份条件。
        if len(unique) > 3:
            unique = [unique[0], unique[len(unique) // 2], unique[-1]]
        shots.append({"shot": index + 1, "start": start, "end": end, "events": unique})
    if reference_mode == "extracted_depth":
        video_constraint = (
            "follow the aligned depth-control sequence for exact geometry; text must not override its framing or timing. Render a new full-color photorealistic scene with only the assigned target identity, never the source person's identity, grayscale depth shading or the reference-photo studio."
        )
    elif reference_mode == "depth_video":
        video_constraint = (
            "follow the aligned depth-control sequence; text must not override its framing or timing. Render a full-color photorealistic scene, never grayscale depth shading or the reference-photo studio."
        )
    else:
        video_constraint = (
            "follow <Video 1>'s visible action and composition; text must not override its framing or timing. Render a coherent photorealistic edit, not the reference-photo studio."
        )
    continuity_instruction = (
        "Preserve every source cut"
        if has_source_video
        else "Preserve the depth-control cut timing"
    )
    detail = [
        f"Show exactly {len(people)} person(s) per frame, each target appearing once only. Reference pictures supply identity and clothing only, never a collage, turntable, camera plan or standing pose. {continuity_instruction} and {video_constraint}"
    ]
    for shot in shots:
        prefix = (
            "[Shot 1] "
            if shot["shot"] == 1
            else f"[Shot {shot['shot']}] At {timestamp(shot['start'])}, preserve the source cut. "
        )
        detail.append(
            prefix
            + f"Match the {'source video' if has_source_video else 'depth-control'} composition, orientation, subject size, contacts and occlusions throughout this interval. Keep assigned identities and garment construction continuous."
        )
        for subject in subjects:
            clothing = "; ".join(
                f"{key}: {subject['appearance_attributes'][key]}"
                for key in ("upper_garment", "closure", "neckline", "sleeves")
                if key in subject["appearance_attributes"]
            )
            if overrides.get(subject["subject"]):
                detail.append(f"<Subject {subject['subject']}> retains the assigned identity, with the explicit appearance overrides applied consistently in this shot and across cuts.")
                continue
            detail.append(
                f"<Subject {subject['subject']}> remains the person from {subject['primary_picture']} in this shot. "
                + (f"Visible upper-body wardrobe: {clothing}. " if clothing else "")
                + "Keep this outfit throughout the shot and across the cut. Do not remove clothing layers, change sleeve length, or replace the upper garment with a different top."
            )
        for event in shot["events"]:
            detail.append(f"At {event['time']:.3f} seconds, {event['description']}")
    detail.append(
        f"End at the {'source' if has_source_video else 'depth-control'} pose at {duration:.3f} seconds. Environment details may be reconstructed, but must respect {'the analyzed scene description and aligned depth geometry' if reference_mode == 'extracted_depth' else 'depth geometry' if uses_depth else 'the source video layout and contacts'}. No invented dialogue, extra cuts or extra people."
    )
    if reference_mode == "extracted_depth":
        summary = f"[reference generation] Render a {duration:.3f}-second natural-color video with exactly the {len(people)} assigned target characters. Exact geometry and timing come from the aligned depth control; scene appearance comes from the analyzed textual description; identity and clothing come exclusively from the assigned pictures. Never preserve or reconstruct the source person's identity."
    elif reference_mode == "depth_video":
        summary = f"[reference generation] Render a {duration:.3f}-second natural-color video with the {len(people)} assigned target characters. Exact geometry and timing come from the aligned depth control; identity and clothing come from the assigned pictures. The depth control is not a visible source and must never be rendered as grayscale."
    else:
        summary = f"[video editing] The target video is an edited version of <Video 1>. Replace its people with the {len(people)} assigned target characters and render a {duration:.3f}-second natural-color video. Motion, composition and timing come from <Video 1>; identity and clothing come from the assigned pictures."
    sections = {
        "subject_definitions": "\n".join(definitions),
        "summary": summary,
        "retention_analysis": "\n".join(retention),
        "detailed_description": "\n".join(detail),
        "overall_soundscape": "Natural ambient and movement sounds appropriate to each source scene. No invented speech.",
        "non_diegetic_music": "N/A",
    }
    if overrides:
        sections["summary"] += " Explicit user-requested appearance attributes below replace only their corresponding reference attributes."
    prompt = "\n\n".join(f"{key}\n{value}" for key, value in sections.items())
    if supplement_plan:
        from .clone_supplement import merge_prompt
        prompt = merge_prompt(prompt, supplement_plan)
    if len(prompt) > 18000:
        raise ValueError("镜头计划过长，请缩短区间或减少人物，避免静默截断引用")
    return {
        "schema": "clone.prompt-plan/v1",
        "duration": duration,
        "subjects": subjects,
        "references": references,
        "shots": shots,
        "priority": [
            "depth_geometry_and_timing" if uses_depth
            else "source_video_motion_and_timing",
            "user_requested_semantic_overrides",
            "target_identity_and_garments",
            "source_action_and_environment" if has_source_video else "controlled_action_and_environment",
        ],
        "prompt": prompt,
    }


def append_lightweight_instruction(prompt, supplement):
    from .clone_supplement import split_prompt

    if not supplement:
        return prompt
    sections = split_prompt(prompt)
    target = supplement.get("target", {"kind": "all"})
    label = f"<Subject {target['subject_index']}>" if target["kind"] == "subject" else "the assigned target subjects and scene"
    sections["detailed_description"] += (
        f"\nUser instruction for {label} (apply only to this edit; explicitly "
        "requested changes override the corresponding source and reference "
        f"constraints above): {supplement['text']}"
    )
    return "\n\n".join(f"{key}\n{value}" for key, value in sections.items())


def compile_lightweight_prompt(people, duration, supplement=None):
    """兼容旧槽位输入；不把动物、物体或未知主体强行编译成人物换装。"""
    duration = float(duration)
    if not math.isfinite(duration) or not 0 < duration <= 15 or not 1 <= len(people) <= 9:
        raise ValueError("提示词计划的主体数或时长无效")
    subjects, references, definitions, retention = [], [], [], []
    for index, person in enumerate(people, 1):
        tags = []
        for path in person.get("prepared_images", person["images"]):
            number = len(references) + 1
            references.append({"picture": number, "subject": index, "path": path, "view": "unknown"})
            tags.append(f"<Picture {number}>")
        if not tags or len(references) > 9:
            raise ValueError("每个主体至少一张参考图，全部引用最多9张")
        subjects.append({"subject": index, "track_id": person.get("track_id", index),
                         "position": person["position"], "pictures": tags, "primary_picture": tags[0]})
        definitions.append(
            f"<Subject {index}> is the target subject whose visible appearance comes from {', '.join(tags)}. "
            f"Bind this target to the source subject initially at screen {person['position']}; "
            "follow the same subject through movement, occlusion and cuts. "
            "Use only visible reference features relevant to the user's edit."
        )
        retention.append(
            f"<Subject {index}>: attribute_transfer — use the assigned reference appearance "
            "throughout the source actions, with explicit user changes taking priority."
        )
    definitions.append(
        "<Video 1> is the source video to edit. It supplies action, timing, framing, "
        "camera movement, scene layout, physical contacts and visual style. "
        "Reference pictures supply target appearance only, never an opening or closing frame."
    )
    retention.append(
        "<Video 1>: partially_preserved — replace the selected subjects; preserve "
        "unselected subjects, action, scene, shot order and camera movement unless the user requests changes."
    )
    sections = {"subject_definitions": "\n".join(definitions)}
    sections["summary"] = (
        f"[video editing] The target video is an edited version of <Video 1>. "
        f"Produce a {duration:.3f}-second video. Keep the source visual style for unchanged content; "
        "replacement subjects use the rendering style explicitly requested by the user. Replace only "
        f"the {len(people)} position-bound source subject(s) with the assigned targets. "
        "Explicit user instructions take priority over default preservation rules."
    )
    sections["retention_analysis"] = "\n".join(retention)
    opening_bindings = " ".join(
        f"<Subject {subject['subject']}> replaces the source subject initially at screen "
        f"{subject['position']}, using the appearance defined by {', '.join(subject['pictures'])}."
        for subject in subjects
    )
    sections["detailed_description"] = (
        "[Shot 1] Start with <Video 1>'s opening composition, framing, "
        f"orientation and visible action. {opening_bindings} Apply the initial-position bindings once "
        "and maintain the same subject assignment thereafter. Render the target "
        "appearance from the pictures while following the source movement and contacts.\n"
        "Continue through the source shots in their original order. Preserve every "
        "visible cut and its relative timing, shot size, camera angle, camera "
        "movement and speed. Keep the target appearance continuous across cuts.\n"
        "Preserve only props actually visible in the source and the interactions "
        "with them. Match occlusions, illumination, contrast and perspective to the source scene.\n"
        "Keep the source background and the style of unchanged content throughout, including the final frame. "
        "Do not cut to a reference picture or copy its background, pose or layout. "
        "End with the source final action performed by the replacement subjects. "
        "No invented accessories, subjects, dialogue or events. These are semantic "
        "editing instructions; no mask or frame-level structural control is supplied."
    )
    sections["overall_soundscape"] = "Natural ambient and movement sounds appropriate to the source scene. No invented speech."
    sections["non_diegetic_music"] = "N/A"
    return {
        "schema": "clone.lightweight-prompt/v2", "duration": duration,
        "subjects": subjects, "references": references, "shots": [],
        "priority": ["explicit_user_edit", "position_bound_target_appearance", "source_video_action_and_scene"],
        "prompt": append_lightweight_instruction(
            "\n\n".join(f"{key}\n{value}" for key, value in sections.items()), supplement
        ),
    }
