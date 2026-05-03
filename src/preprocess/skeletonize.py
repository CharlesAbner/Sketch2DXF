"""
本文件的作用：
- 对二值线条做骨架化，便于提取线段、端点和交点。
- 是从粗线条视觉表示走向结构恢复的重要桥梁。

建议说明：
- 前期只要先接通基础 skeleton 流程即可。
- 如果样例图里粗细变化明显，这一层会非常关键。
"""

from __future__ import annotations

import numpy as np
from skimage.morphology import skeletonize as sk_skeletonize


def to_skeleton(binary_img: np.ndarray) -> np.ndarray:
    """Convert a binary image into a 1-pixel skeleton."""
    skeleton = sk_skeletonize(binary_img > 0)
    return (skeleton.astype(np.uint8)) * 255
