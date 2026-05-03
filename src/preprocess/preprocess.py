"""
本文件的作用：
- 把灰度化、二值化、去噪、纠偏、骨架化串成统一预处理入口。
- 为后续 perception 层提供标准化中间结果字典。

建议说明：
- 这里不要过早追求复杂配置分支，先保证返回字段稳定。
- 一旦字段固定，后面整个系统就会顺很多。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from src.io_utils.image_io import to_gray
from src.preprocess.binarize import adaptive_binarize
from src.preprocess.denoise import median_denoise, morph_close, morph_open
from src.preprocess.deskew import deskew, estimate_skew_angle
from src.preprocess.skeletonize import to_skeleton


def run_preprocess(image: np.ndarray, config: dict[str, Any]) -> dict[str, np.ndarray | float]:
    """Run the preprocessing stack and return structured intermediate artifacts."""
    gray = to_gray(image)
    preprocess_cfg = config["preprocess"]
    binary = adaptive_binarize(
        gray,
        block_size=preprocess_cfg["adaptive_block_size"],
        c=preprocess_cfg["adaptive_c"],
    )
    clean = median_denoise(binary, preprocess_cfg["median_ksize"])
    clean = morph_open(clean, preprocess_cfg["morph_ksize"])
    clean = morph_close(clean, preprocess_cfg["morph_ksize"])
    angle = estimate_skew_angle(clean)
    deskewed = deskew(clean, angle) if preprocess_cfg["enable_deskew"] else clean
    skeleton = to_skeleton(deskewed) if preprocess_cfg["enable_skeleton"] else deskewed
    return {
        "image": image,
        "gray": gray,
        "binary": binary,
        "clean": clean,
        "deskewed": deskewed,
        "skeleton": skeleton,
        "angle": angle,
        "stats": {
            "foreground_ratio": float(np.count_nonzero(clean)) / float(clean.size),
            "height": int(clean.shape[0]),
            "width": int(clean.shape[1]),
        },
    }
