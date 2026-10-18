"""演员区域预处理隔离进程；按用户范围生成去外观视频和可检查遮罩。"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.clone_depth_runtime import find_tool
from app.clone_region import (
    align_region_result,
    encode_video,
    neutralize,
    read_video,
    validate_regions,
)


def extract_tracking_masks(frames, regions, directory, model_path, progress=lambda *_: None,
                           recover_cuts=False):
    """从每段首帧框提示传播指定演员轮廓；生成片切点轻微漂移时可重新初始化。"""
    import cv2
    import numpy as np
    import torch

    try:
        from sam2.build_sam import build_sam2_video_predictor
    except ImportError as error:
        raise RuntimeError("缺少 SAM 2 视频跟踪运行时，请重新执行环境准备") from error
    model_path = Path(model_path)
    if not model_path.is_file():
        raise RuntimeError("SAM 2 视频跟踪模型尚未准备完成")
    if not torch.cuda.is_available():
        raise RuntimeError("视频跟踪轮廓需要 CUDA 显卡")

    predictor = build_sam2_video_predictor(
        "configs/sam2.1/sam2.1_hiera_t.yaml",
        str(model_path),
        device="cuda",
        apply_postprocessing=False,
    )
    masks = np.zeros(frames.shape[:3], np.uint8)
    total = sum(round((region["end"] - region["start"]) * 24) for region in regions if region["fill"] == "track")
    completed = 0
    try:
        for region_index, region in enumerate(regions, 1):
            if region["fill"] != "track":
                continue
            first, last = round(region["start"] * 24), round(region["end"] * 24)
            segment = frames[first:last]
            cursor = 0
            restarts = 0
            while cursor < len(segment):
                with tempfile.TemporaryDirectory(prefix="clone-track-", dir=directory) as temporary:
                    frame_dir = Path(temporary)
                    for index, frame in enumerate(segment[cursor:]):
                        cv2.imwrite(str(frame_dir / f"{index:05d}.jpg"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                                    [cv2.IMWRITE_JPEG_QUALITY, 96])
                    state = predictor.init_state(video_path=str(frame_dir), offload_video_to_cpu=True,
                                                 offload_state_to_cpu=True)
                    height, width = frames.shape[1:3]
                    left, top, right, bottom = region["box"]
                    box = np.array([left * width, top * height, right * width - 1, bottom * height - 1], np.float32)
                    predictor.add_new_points_or_box(inference_state=state, frame_idx=0, obj_id=1, box=box)
                    seen = set()
                    for relative, object_ids, logits in predictor.propagate_in_video(state):
                        if 1 not in object_ids:
                            continue
                        slot = list(object_ids).index(1)
                        mask = (logits[slot, 0] > 0).cpu().numpy().astype(np.uint8) * 255
                        if mask.shape != (height, width):
                            mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
                        masks[first + cursor + relative] = mask
                        seen.add(relative)
                        progress(10 + round(75 * (completed + cursor + len(seen)) / max(1, total)),
                                 f"正在跟踪第 {region_index} 段指定演员")
                    predictor.reset_state(state)
                missing = [relative for relative in range(len(segment) - cursor)
                           if relative not in seen or np.count_nonzero(masks[first + cursor + relative]) < 16]
                if not missing:
                    cursor = len(segment)
                    continue
                first_missing = missing[0]
                if recover_cuts and first_missing > 0 and restarts < 4:
                    cursor += first_missing
                    restarts += 1
                    progress(10 + round(75 * (completed + cursor) / max(1, total)),
                             f"检测到生成镜头切换，正在第 {region_index} 段重新锁定演员")
                    continue
                raise RuntimeError(f"第 {region_index} 段有 {len(missing)} 帧跟踪中断，请缩短到单一镜头后重新框选")
            completed += len(segment)
    finally:
        del predictor
        torch.cuda.empty_cache()
    active = [index for index in range(len(frames)) if any(
        region["fill"] == "track" and region["start"] <= index / 24 < region["end"] for region in regions
    )]
    if active:
        from tools.clone_depth.worker.quality_person_mask import extract_quality_masks

        quality = extract_quality_masks(
            frames[active],
            lambda percent, message: progress(85 + round(max(0, percent - 82) * 13 / 6),
                                              "正在细化跟踪人物全身轮廓 · " + message),
        )
        masks[active] = fuse_tracking_masks(masks[active], quality)
    return masks


def fuse_tracking_masks(tracking_masks, quality_masks):
    """用跟踪确定身份，只接纳与轨迹相交的 quality 人物连通区。"""
    import cv2
    import numpy as np

    result = tracking_masks.copy()
    kernel = np.ones((15, 15), np.uint8)
    for index, (tracking, quality) in enumerate(zip(tracking_masks, quality_masks, strict=True)):
        tracking = (tracking > 0).astype(np.uint8)
        nearby = cv2.dilate(tracking, kernel, iterations=1) > 0
        count, labels = cv2.connectedComponents((quality > 60).astype(np.uint8), connectivity=8)
        accepted = tracking > 0
        for label in range(1, count):
            component = labels == label
            overlap = int(np.count_nonzero(component & nearby))
            if overlap >= max(4, round(np.count_nonzero(component) * .01)):
                accepted |= component
        result[index] = accepted.astype(np.uint8) * 255
    return result


def resize_masks(masks, shape):
    import cv2
    import numpy as np

    if masks.shape[1:] == tuple(shape):
        return masks
    height, width = shape
    return np.stack([
        cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST) for mask in masks
    ]).astype(np.uint8)


def tracking_quality_warnings(stats):
    warnings = []
    for index, item in enumerate(stats, 1):
        if item["fill"] == "track" and item["max_area_jump"] > 8:
            warnings.append(f"第 {index} 段人物轮廓面积突变 {item['max_area_jump']:.1f} 倍，请重点复核")
    return warnings


def validate_tracking_stats(stats, *, strict_area=True):
    for index, item in enumerate(stats, 1):
        if item["fill"] != "track":
            continue
        if item["missing_frames"]:
            raise RuntimeError(f"第 {index} 段有 {item['missing_frames']} 帧未跟踪到演员，请重新框选")
        if strict_area and item["max_area_jump"] > 8:
            raise RuntimeError(f"第 {index} 段演员轮廓突变，可能跟错人物；请按单一镜头拆段")
        if item["max_frame_coverage"] > .8:
            raise RuntimeError(f"第 {index} 段跟踪范围覆盖画面过大，可能误选背景；请缩小首帧框")


def main():
    import numpy as np

    task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    directory = Path(task["directory"])
    frames = read_video(task["source"], tuple(task["size"]))
    regions = validate_regions(task["regions"], len(frames) / 24)
    if task.get("operation") == "align":
        generated = read_video(task["generated"], tuple(task["size"]))
        source_masks = generated_masks = None
        if task.get("source_masks"):
            source_masks = resize_masks(np.load(task["source_masks"])["masks"], frames.shape[1:3])
            generated_masks = np.zeros(frames.shape[:3], np.uint8)
            if any(region["fill"] == "track" for region in regions):
                _, initial_plan = align_region_result(
                    frames, generated, regions, allow_tracked_closeup=True,
                )
                mapped_generated = generated[np.asarray(initial_plan["mapping"])]
                generated_masks = extract_tracking_masks(
                    mapped_generated, regions, directory, task["tracking_model"],
                    lambda percent, message: print(json.dumps(
                        {"type": "progress", "percent": percent, "message": message}, ensure_ascii=False
                    ), flush=True),
                    recover_cuts=True,
                )
                _, _, generated_stats = neutralize(mapped_generated, generated_masks, regions)
                validate_tracking_stats(generated_stats, strict_area=False)
        aligned, plan = align_region_result(
            frames, generated, regions, source_masks, generated_masks,
            generated_masks_mapped=source_masks is not None,
        )
        effective_masks = plan.pop("effective_masks", None)
        if source_masks is not None and any(region["fill"] == "track" for region in regions):
            plan["generated_tracking"] = generated_stats
            plan["tracking_warnings"] = tracking_quality_warnings(generated_stats)
        if effective_masks is not None:
            np.savez_compressed(directory / "composite-masks.npz", masks=effective_masks,
                                source_masks=source_masks, generated_masks=generated_masks)
        (directory / "alignment.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        encode_video(aligned, task["output"], find_tool(ROOT, "ffmpeg"))
        if task.get("source_audio"):
            import subprocess

            output = Path(task["output"])
            muxed = output.with_suffix(".audio.partial.mp4")
            subprocess.run([find_tool(ROOT, "ffmpeg"), "-v", "error", "-y", "-i", str(output),
                            "-ss", str(task.get("audio_start", 0)), "-i", task["source_audio"],
                            "-map", "0:v:0", "-map", "1:a:0?", "-t", str(len(frames) / 24),
                            "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(muxed)],
                           capture_output=True, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            muxed.replace(output)
        print(json.dumps({"type": "result", "result": plan}, ensure_ascii=False), flush=True)
        return
    masks = np.zeros(frames.shape[:3], dtype=np.uint8)
    tracking_regions = [region for region in regions if region["fill"] == "track"]
    if tracking_regions:
        masks = np.maximum(masks, extract_tracking_masks(
            frames, tracking_regions, directory, task["tracking_model"],
            lambda percent, message: print(json.dumps(
                {"type": "progress", "percent": percent, "message": message}, ensure_ascii=False
            ), flush=True),
        ))
    indexes = [i for i in range(len(frames)) if any(r["fill"] == "auto" and r["start"] <= i / 24 < r["end"] for r in regions)]
    total_mask_frames = len(indexes) + sum(round((r["end"] - r["start"]) * 24) for r in regions if r["fill"] == "crop")
    completed_mask_frames = 0

    def report(percent, message, count):
        fraction = max(0, min(1, (percent - 82) / 6))
        value = 10 + round(80 * (completed_mask_frames + count * fraction) / max(1, total_mask_frames))
        print(json.dumps({"type": "progress", "percent": value, "message": message}, ensure_ascii=False), flush=True)

    if indexes or any(region["fill"] == "crop" for region in regions):
        from tools.clone_depth.worker.quality_person_mask import extract_quality_masks
    if indexes:
        masks[indexes] = extract_quality_masks(frames[indexes], lambda percent, message: report(percent, message, len(indexes)))
        completed_mask_frames += len(indexes)
    for region in regions:
        if region["fill"] != "crop":
            continue
        import math

        import cv2

        left, top, right, bottom = region["box"]
        height, width = frames.shape[1:3]
        left, top = int(left * width), int(top * height)
        right, bottom = math.ceil(right * width), math.ceil(bottom * height)
        first, last = round(region["start"] * 24), round(region["end"] * 24)
        crop_masks = extract_quality_masks(frames[first:last, top:bottom, left:right],
            lambda percent, message, count=last-first: report(percent, "局部精细轮廓 · " + message, count))
        for index, crop_mask in enumerate(crop_masks, first):
            masks[index, top:bottom, left:right] = cv2.resize(crop_mask, (right-left, bottom-top))
        completed_mask_frames += last-first
    edited, masks, stats = neutralize(frames, masks, regions)
    validate_tracking_stats(stats)
    np.savez_compressed(directory / "masks.npz", masks=masks)
    ffmpeg = find_tool(ROOT, "ffmpeg")
    encode_video(edited, directory / "region.mp4", ffmpeg)
    preview = frames.copy()
    selected = masks > 0
    preview[selected] = (frames[selected].astype(np.float32) * .4 + np.array([30, 220, 90]) * .6).astype(np.uint8)
    encode_video(preview, directory / "preview.mp4", ffmpeg)
    print(json.dumps({"type": "result", "result": {"duration": len(frames) / 24, "regions": regions, "stats": stats,
                                               "missing_frames": sum(r["missing_frames"] for r in stats)}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
