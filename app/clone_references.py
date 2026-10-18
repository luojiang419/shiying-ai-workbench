"""旧版白线裁切辅助，保留历史兼容；正式自动流程使用clone_reference_analysis。"""

from itertools import pairwise
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


def prepare_reference(source, directory, prefix):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        pixels = np.asarray(image)
        height, width = pixels.shape[:2]
        cuts = []
        if width > height * 1.4:
            white = (pixels.min(axis=2) >= 250).mean(axis=0)
            for fraction in (1 / 3, 2 / 3):
                lo, hi = (
                    int(width * (fraction - 0.025)),
                    int(width * (fraction + 0.025)),
                )
                candidates = np.flatnonzero(white[lo:hi] >= 0.995) + lo
                if 2 <= len(candidates) <= max(2, int(width * 0.02)):
                    cuts.append(int(np.median(candidates)))
        bounds = [0, *cuts, width] if len(cuts) == 2 else [0, width]
        outputs = []
        for index, (left, right) in enumerate(pairwise(bounds)):
            target = directory / f"{prefix}-{index + 1}.png"
            image.crop((left, 0, right, height)).save(target)
            outputs.append(target)
        return outputs
