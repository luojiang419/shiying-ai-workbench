"""演员区域快照、预览及保留镜头的时间对齐；不推断演员身份。"""

import math
from itertools import pairwise
from pathlib import Path


def validate_regions(value, duration):
    if not isinstance(value, list) or not 1 <= len(value) <= 24:
        raise ValueError("请添加1–24段演员区域")
    result = []
    frames = math.floor(duration * 24 + 1e-6)
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("演员区域格式无效")  # noqa: TRY004 - API校验统一返回400
        times = [item.get("start"), item.get("end")]
        box = item.get("box")
        if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in times):
            raise ValueError("演员区域时间必须为有效秒数")
        start, end = (round(x * 24) for x in times)
        if not 0 <= start < end <= frames:
            raise ValueError("演员区域时间超出选中区间，请按相对时间填写")
        if not isinstance(box, list) or len(box) != 4 or any(
            isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or not 0 <= x <= 1 for x in box
        ):
            raise ValueError("演员范围需要0–1归一化坐标")
        left, top, right, bottom = box
        if right - left < .01 or bottom - top < .01:
            raise ValueError("演员范围过小或坐标倒置")
        if (right - left) * (bottom - top) > .8:
            raise ValueError("演员范围过大，请保留足够背景用于校正镜头")
        fill = item.get("fill", "auto")
        if fill not in {"track", "auto", "crop", "box"}:
            raise ValueError("遮罩修正方式无效")
        prompt_cut = item.get("prompt_cut", True)
        if not isinstance(prompt_cut, bool):
            raise TypeError("提示词切镜标记必须为布尔值")
        result.append({"start": start / 24, "end": end / 24, "box": box, "fill": fill,
                       "prompt_cut": prompt_cut})
    result.sort(key=lambda item: item["start"])
    if any(a["end"] > b["start"] for a, b in pairwise(result)):
        raise ValueError("演员区域时间不可重叠；同一镜头请用一个覆盖运动范围的选区")
    return result


def read_video(path, size=None):
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(path))
    frames = []
    try:
        if abs(cap.get(cv2.CAP_PROP_FPS) - 24) > .01:
            raise ValueError("演员区域视频需要对齐24 FPS")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if size:
                frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()
    if not frames:
        raise ValueError("无法解码演员区域视频")
    return np.stack(frames)


def encode_video(frames, path, ffmpeg):
    import subprocess

    height, width = frames.shape[1:3]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(ffmpeg), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{width}x{height}", "-r", "24", "-i", "pipe:0", "-an", "-c:v", "libx264",
                    "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
                   input=frames.tobytes(), capture_output=True, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def neutralize(frames, masks, regions):
    import cv2
    import numpy as np

    output = frames.copy()
    selected = np.zeros(frames.shape[:3], np.uint8)
    height, width = frames.shape[1:3]
    stats = []
    for region in regions:
        first, last = round(region["start"] * 24), round(region["end"] * 24)
        left, top, right, bottom = region["box"]
        left, top = int(left * width), int(top * height)
        right, bottom = math.ceil(right * width), math.ceil(bottom * height)
        counts = []
        for index in range(first, last):
            if region["fill"] == "box":
                selected[index, top:bottom, left:right] = 255
            elif region["fill"] == "track":
                selected[index] = (masks[index] > 0) * 255
            else:
                selected[index, top:bottom, left:right] = (masks[index, top:bottom, left:right] > 60) * 255
                selected[index] = cv2.dilate(selected[index], np.ones((3, 3), np.uint8))
            counts.append(int(np.count_nonzero(selected[index])))
            output[index][selected[index] > 0] = (128, 128, 128)
        missing = sum(count < 16 for count in counts)
        nonzero = [count for count in counts if count >= 16]
        jumps = [max(a, b) / max(1, min(a, b)) for a, b in pairwise(nonzero)]
        frame_area = height * width
        stats.append({"start": region["start"], "end": region["end"], "fill": region["fill"],
                      "frames": last - first, "missing_frames": missing,
                      "missing_times": [(first + offset) / 24 for offset, count in enumerate(counts) if count < 16],
                      "min_coverage": min(counts) / max(1, (right - left) * (bottom - top)),
                      "max_area_jump": max(jumps, default=1),
                      "max_frame_coverage": max(counts, default=0) / max(1, frame_area)})
    return output, selected, stats


def match_region_appearance(source_frame, generated_frame, mask, previous=None):
    """用人物邻近背景匹配局部色彩、亮度与高频细节，并返回可跨帧平滑的参数。"""
    import cv2
    import numpy as np

    binary = (mask > 0).astype(np.uint8)
    radius = max(9, min(31, round(min(mask.shape) * .04)))
    if radius % 2 == 0:
        radius += 1
    outer = cv2.dilate(binary, np.ones((radius, radius), np.uint8), iterations=1) > 0
    inner = cv2.dilate(binary, np.ones((5, 5), np.uint8), iterations=1) > 0
    ring = outer & ~inner
    if np.count_nonzero(ring) < 64:
        return generated_frame, previous, {
            "applied": False,
            "ring_pixels": int(np.count_nonzero(ring)),
        }

    source_lab = cv2.cvtColor(source_frame, cv2.COLOR_RGB2LAB).astype(np.float32)
    generated_lab = cv2.cvtColor(generated_frame, cv2.COLOR_RGB2LAB).astype(np.float32)

    def robust_stats(values):
        median = np.median(values, axis=0)
        low, high = np.percentile(values, [25, 75], axis=0)
        return median, np.maximum((high - low) / 1.349, 2)

    source_median, source_scale = robust_stats(source_lab[ring])
    generated_median, generated_scale = robust_stats(generated_lab[ring])
    gain = np.clip(source_scale / generated_scale, .88, 1.14)
    offset = np.clip(source_median - generated_median, [-18, -10, -10], [18, 10, 10])

    source_gray = cv2.cvtColor(source_frame, cv2.COLOR_RGB2GRAY).astype(np.float32)
    generated_gray = cv2.cvtColor(generated_frame, cv2.COLOR_RGB2GRAY).astype(np.float32)

    def detail_level(gray):
        residual = np.abs(gray - cv2.GaussianBlur(gray, (0, 0), .8))[ring]
        return float(np.percentile(residual, 75))

    source_detail = detail_level(source_gray)
    generated_detail = detail_level(generated_gray)
    current = {"gain": gain, "offset": offset}
    if previous is not None:
        # 镜头内指数平滑，防止逐帧颜色统计带来闪烁；切镜时由调用方重置。
        current = {
            "gain": previous["gain"] * .72 + gain * .28,
            "offset": previous["offset"] * .72 + offset * .28,
        }

    corrected_lab = (
        (generated_lab - generated_median) * current["gain"]
        + generated_median
        + current["offset"]
    )
    corrected = cv2.cvtColor(np.clip(corrected_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB).astype(np.float32)
    detail = float(np.clip(source_detail / max(generated_detail, .5), .75, 1.5))
    if previous is not None:
        detail = previous["detail"] * .72 + detail * .28
    current["detail"] = detail
    # 颜色只作用于低频基底；沿用原生成帧的高频残差，避免 Lab 往返把皮肤与布料磨平。
    generated_float = generated_frame.astype(np.float32)
    original_detail = generated_float - cv2.GaussianBlur(generated_float, (0, 0), .8)
    corrected_base = cv2.GaussianBlur(corrected, (0, 0), .8)
    corrected = np.clip(corrected_base + original_detail * detail, 0, 255).astype(np.uint8)
    corrected_lab_final = cv2.cvtColor(corrected, cv2.COLOR_RGB2LAB).astype(np.float32)
    corrected_median = np.median(corrected_lab_final[ring], axis=0)
    corrected_gray = cv2.cvtColor(corrected, cv2.COLOR_RGB2GRAY).astype(np.float32)
    corrected_detail = detail_level(corrected_gray)
    return corrected, current, {
        "applied": True,
        "ring_pixels": int(np.count_nonzero(ring)),
        "lab_delta_before": float(np.mean(np.abs(source_median - generated_median))),
        "lab_delta_after": float(np.mean(np.abs(source_median - corrected_median))),
        "detail_ratio_before": generated_detail / max(source_detail, .5),
        "detail_ratio_after": corrected_detail / max(source_detail, .5),
    }


def _replace_region_edge_background(frame, mask, alpha, target_background):
    """按成像合成方程把软边中的生成背景连续替换成目标背景。"""
    import cv2
    import numpy as np

    binary = (mask > 0).astype(np.uint8)
    fringe = (alpha > 0) & (alpha < .999)
    generated_background = cv2.inpaint(frame, binary * 255, 3, cv2.INPAINT_TELEA).astype(np.float32)
    observed = frame.astype(np.float32)
    matte = alpha[:, :, None]
    # I=αF+(1-α)B。无需先求解并裁切 F，直接把 B_generated 换成
    # B_source，可避免强反混色造成的黑边、锯齿和剪纸感。
    replaced = observed + (1 - matte) * (
        target_background.astype(np.float32) - generated_background
    )
    # 距离场只近似真实 alpha；限制结果始终位于原前景与目标背景之间，
    # 防止深色头发等高反差边缘反解过冲成黑线。
    target = target_background.astype(np.float32)
    replaced = np.minimum(np.maximum(replaced, np.minimum(observed, target)), np.maximum(observed, target))
    output = target_background.copy()
    foreground = binary > 0
    output[foreground] = np.clip(replaced[foreground], 0, 255).astype(np.uint8)
    return output, int(np.count_nonzero(fringe))


def _tracked_composite(source, generated, mapping, active, source_masks, generated_masks,
                       regions, generated_masks_mapped=False):
    """以内侧羽化回贴生成人物，并用源背景板清除旧人物轮廓。"""
    import cv2
    import numpy as np

    if source_masks.shape != source.shape[:3]:
        raise ValueError("源演员遮罩尺寸与视频不匹配，请重新准备")
    expected_generated_mask_shape = source.shape[:3] if generated_masks_mapped else generated.shape[:3]
    if generated_masks.shape != expected_generated_mask_shape:
        raise ValueError("生成人物遮罩尺寸与视频不匹配")
    output = source.copy()
    effective = np.zeros(source.shape[:3], np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    edited_pixels = 0
    cleanup_pixels = 0
    decontaminated_pixels = 0
    appearance_stats = []
    appearance_state = None
    previous_region = None
    for index in np.flatnonzero(active):
        generated_index = mapping[index]
        generated_mask = generated_masks[index] if generated_masks_mapped else generated_masks[generated_index]
        source_binary = (source_masks[index] > 0).astype(np.uint8)
        generated_binary = (generated_mask > 0).astype(np.uint8)
        if np.count_nonzero(generated_binary) < 16:
            raise ValueError(f"{index / 24:.3f}秒未跟踪到替换人物，请重新框选该镜头")
        # 旧演员外轮廓先从源帧生成背景板，避免不同发型/手势尺寸留下白边。
        # 生成轮廓不再侵蚀，而是依据内部距离构建连续 alpha；这样既能替换
        # 生成背景的颜色污染，又保留发梢、手指等细节，避免剪纸感。
        source_cover = cv2.dilate(source_binary, kernel, iterations=2)
        background_plate = cv2.inpaint(
            source[index], source_cover * 255, 3, cv2.INPAINT_TELEA,
        )
        distance = cv2.distanceTransform(generated_binary, cv2.DIST_L2, 5)
        feather = max(3.0, min(5.0, min(generated_binary.shape) * .01))
        alpha = np.clip(distance / feather, 0, 1)
        alpha = alpha * alpha * (3 - 2 * alpha)
        region_index = next(i for i, region in enumerate(regions) if region["start"] <= index / 24 < region["end"])
        if region_index != previous_region:
            appearance_state = None
        matched, appearance_state, appearance = match_region_appearance(
            source[index], generated[generated_index], generated_binary * 255, appearance_state,
        )
        composited, cleaned_count = _replace_region_edge_background(
            matched, generated_binary, alpha, background_plate,
        )
        appearance_stats.append(appearance)
        previous_region = region_index
        effective_binary = np.maximum(source_cover, generated_binary)
        effective[index] = effective_binary * 255
        edited_pixels += int(np.count_nonzero(effective[index]))
        cleanup_pixels += int(np.count_nonzero(source_cover & ~generated_binary))
        decontaminated_pixels += cleaned_count
        output[index] = composited
    outside = effective == 0
    outside_error = int(np.max(np.abs(output.astype(np.int16) - source.astype(np.int16))[outside])) if np.any(outside) else 0
    applied = [item for item in appearance_stats if item["applied"]]
    return output, effective, {
        "edited_pixels": edited_pixels,
        "edited_fraction": edited_pixels / max(1, int(np.count_nonzero(active)) * source.shape[1] * source.shape[2]),
        "edge_method": "background_replacement_distance_matte",
        "source_outline_cleanup_pixels": cleanup_pixels,
        "decontaminated_edge_pixels": decontaminated_pixels,
        "outside_mask_source_max_error": outside_error,
        "appearance_match": {
            "method": "local_robust_lab_and_detail_temporal",
            "frames": len(applied),
            "mean_ring_lab_delta_before": float(np.mean([x["lab_delta_before"] for x in applied])) if applied else 0,
            "mean_ring_lab_delta_after": float(np.mean([x["lab_delta_after"] for x in applied])) if applied else 0,
            "mean_detail_ratio_before": float(np.mean([x["detail_ratio_before"] for x in applied])) if applied else 1,
            "mean_detail_ratio_after": float(np.mean([x["detail_ratio_after"] for x in applied])) if applied else 1,
        },
    }


def align_region_result(source, generated, regions, source_masks=None, generated_masks=None,
                        generated_masks_mapped=False, allow_tracked_closeup=False):
    """按背景匹配单调帧序列；未选镜头直接用原帧，拒绝明显错场景。"""
    import cv2
    import numpy as np

    count, total = len(source), len(generated)
    active = np.zeros(count, dtype=bool)
    costs = np.empty((count, total), np.float32)
    descriptors = [cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (32, 24)).astype(np.float32) / 255 for frame in generated]
    for index, frame in enumerate(source):
        area = np.ones((24, 32), bool)
        for region in regions:
            if region["start"] <= index / 24 < region["end"]:
                active[index] = True
                left, top, right, bottom = region["box"]
                area[int(top * 24):math.ceil(bottom * 24), int(left * 32):math.ceil(right * 32)] = False
        target = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (32, 24)).astype(np.float32) / 255
        if np.count_nonzero(area) < 96:
            raise ValueError("演员范围过大，无法根据背景校正镜头；请缩小范围")
        for other, descriptor in enumerate(descriptors):
            costs[index, other] = np.mean(np.abs(target[area] - descriptor[area]))
    # 单调动态规划允许少量重复/跳过帧，禁止重排动作或跨远距离镜头匹配。
    accumulated = np.full((count + 1, total + 1), np.inf)
    accumulated[0, 0] = 0
    parents = np.zeros((count, total), np.uint8)
    for i in range(count):
        for j in range(max(0, i - 12), min(total, i + 13)):
            candidates = [accumulated[i, j], accumulated[i, j + 1] + .015, accumulated[i + 1, j] + .015]
            choice = int(np.argmin(candidates))
            accumulated[i + 1, j + 1] = candidates[choice] + costs[i, j]
            parents[i, j] = choice
    if not np.isfinite(accumulated[count, total]):
        raise ValueError("生成视频时长变化过大，无法校正镜头")
    matches = [[] for _ in range(count)]
    i, j = count - 1, total - 1
    while i >= 0 and j >= 0:
        matches[i].append(j)
        choice = parents[i, j]
        if choice == 0:
            i, j = i - 1, j - 1
        elif choice == 1:
            i -= 1
        else:
            j -= 1
    mapping = [min(options, key=lambda k: costs[index, k]) if options else 0 for index, options in enumerate(matches)]
    if any(b < a for a, b in pairwise(mapping)):
        raise ValueError("生成镜头顺序与原片不一致，无法校正")
    distances = []
    alignment_warnings = []
    for index in range(count):
        if active[index]:
            distance = float(costs[index, mapping[index]])
            distances.append(distance)
            # 跟踪式合成最终会逐像素恢复源背景；H3 的硬切允许出现少量提前/延后，
            # 因此初始映射与最终跟踪合成统一采用较宽阈值。仍保留上限以拦截完全
            # 错误的镜头；非跟踪模式继续使用严格门禁。
            tracked_closeup = source_masks is not None or allow_tracked_closeup
            allowed_distance = .55 if tracked_closeup else .24
            if distance > allowed_distance:
                raise ValueError(f"{index / 24:.3f}秒生成画面与源镜头不匹配，请调整演员范围或重新生成")
            if distance > .24:
                warning = (
                    f"{index / 24:.3f}秒跟踪镜头生成背景差异较高（{distance:.3f}），"
                    "已由跟踪式合成恢复源背景"
                )
                alignment_warnings.append(warning)
    composite = None
    if source_masks is not None or generated_masks is not None:
        if source_masks is None or generated_masks is None:
            raise ValueError("跟踪式合成需要同时提供源人物与生成人物遮罩")
        output, effective, composite = _tracked_composite(
            source, generated, mapping, active, source_masks, generated_masks, regions,
            generated_masks_mapped=generated_masks_mapped,
        )
    else:
        output = source.copy()
        for index in np.flatnonzero(active):
            output[index] = generated[mapping[index]]
        effective = None
    plan = {"schema": "clone.region-alignment/v2" if composite else "clone.region-alignment/v1",
            "source_frames": count, "mapping": mapping,
            "preserved_frames": int(np.count_nonzero(~active)), "max_background_error": max(distances, default=0),
            "method": "monotonic_tracked_mask_composite" if composite else "monotonic_background_match",
            "background_source_preserved": bool(composite), "motion_pixel_exact": False,
            "alignment_warnings": alignment_warnings}
    if composite:
        plan["composite"] = composite
        plan["effective_masks"] = effective
    return output, plan


def region_prompt(person, duration, regions, source_shots=None):
    from .clone_prompt_compiler import clean, normalize_attributes, timestamp

    images = person.get("prepared_images") or person["images"]
    tags = ", ".join(f"<Picture {i + 1}>" for i in range(len(images)))
    detail_index = person.get("detail_reference_index")
    detail_note = (f" <Picture {detail_index}> is the verified face and upper-body detail of this same person; use it for close-ups, never as a new cast member."
                   if detail_index else "")
    attributes = "; ".join(f"{k}: {v}" for k, v in normalize_attributes(person.get("appearance_attributes")).items())
    starts = {0.0}
    for index, region in enumerate(regions):
        if index == 0 or region["start"] > regions[index - 1]["end"] or region.get("prompt_cut", True):
            starts.add(region["start"])
        next_region = regions[index + 1] if index + 1 < len(regions) else None
        continues = (next_region is not None and next_region["start"] == region["end"]
                     and not next_region.get("prompt_cut", True))
        if region["end"] < duration and not continues:
            starts.add(region["end"])
    starts = sorted(starts)
    shots = []
    for index, start in enumerate(starts):
        active = next((r for r in regions if r["start"] <= start < r["end"]), None)
        if active:
            region_index = regions.index(active)
            area = (active["box"][2] - active["box"][0]) * (active["box"][3] - active["box"][1])
            cut = ("Preserve the hard source cut at this exact timestamp; do not continue the preceding shot. "
                   if region_index else "")
            framing = ("Match the source extreme close-up exactly: the target face, hair and hands fill the frame at the same scale, with the lower body outside the frame. Never convert this close-up into a full-body or medium shot. "
                       if area >= .55 else
                       "Match the source subject scale and framing exactly; do not zoom out or replace it with the reference-photo standing pose. ")
            evidence = ""
            if source_shots:
                candidates = sorted(source_shots, key=lambda shot: abs(float(shot.get("start", 0)) - start))
                source_shot = candidates[0] if candidates and abs(float(candidates[0].get("start", 0)) - start) <= .25 else None
                descriptions = [clean(event.get("description")) for event in (source_shot or {}).get("events", [])]
                descriptions = [description for description in descriptions if description]
                if descriptions:
                    evidence = " Source-visible evidence to preserve: " + " ".join(descriptions)[:700] + "."
            action = (cut + framing +
                      "Replace ONLY the moving neutral-gray performer with <Subject 1>, keeping the source trajectory, airborne poses, body scale, camera distance, occlusion and background. The reference-photo pose and background do not transfer." + evidence)
        else:
            action = "Retain this unmasked source shot and all its people unchanged. They are not the target performer."
        shots.append(("[Shot 1] " if index == 0 else f"[Shot {index + 1}] At {timestamp(start)}, ") + action)
    return f"""subject_definitions
<Subject 1> is ONE target performer defined by {tags}, showing complementary views or details of the same person. Never treat them as separate people.{detail_note} Identity and clothing: {clean(person.get('description')) or 'as shown in the assigned pictures'}. {attributes}
<Video 1> is the source performance with ONLY the selected actor's appearance removed to a neutral-gray edit placeholder. Its background, other people, motion and camera provide the source scene.

summary
[video editing] The target video is an edited version of <Video 1>. Replace only the neutral-gray performer with <Subject 1> in the {duration:.3f}-second clip, preserving the source actions and scene.

retention_analysis
<Subject 1>: fully_preserved — transfer the target's complete visible identity, body appearance and garments to each gray performer region, including wide shots and jumps.
<Video 1>: partially_preserved — preserve the source scene, unselected people, motion and cuts; replace only the gray performer. Never render a gray silhouette or insert a reference-photo studio shot.

detailed_description
{chr(10).join(shots)}
Preserve the source trajectory, screen size and corresponding pose at each time. Preserve every source-worn or hand-held action prop, including sunglasses, hats, microphones or tools, with the same visibility, trajectory, occlusion and hand contact; a prop absent from the identity pictures must not be removed. Match the source shot's illumination, white balance, contrast, lens softness, grain and compression texture while retaining <Subject 1>'s true garment colors. Keep both feet airborne when the source is airborne; do not interpret bent airborne legs as sitting. No extra people, invented cuts or speech.

overall_soundscape
Natural scene ambience and movement, no invented speech.

non_diegetic_music
N/A"""
