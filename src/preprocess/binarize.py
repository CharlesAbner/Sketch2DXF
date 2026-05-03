"""
本文件的作用：
- 实现灰度图到二值图的转换，是后续线条提取和骨架化的基础。
- 适合先提供 Otsu 和自适应阈值两套基本方案。

建议说明：
- 前期先保证简单图稳定，不要急着追求复杂场景最优。
- 二值化效果会直接影响后面几乎所有模块，值得尽早打磨。
"""

from __future__ import annotations

import cv2
import numpy as np


def adaptive_binarize(
    gray: np.ndarray,
    method: str = "gaussian",
    block_size: int = 31,
    c: int = 8,
) -> np.ndarray:
    """Apply adaptive thresholding to a grayscale image."""
    adaptive_method = cv2.ADAPTIVE_THRESH_GAUSSIAN_C
    if method == "mean":
        adaptive_method = cv2.ADAPTIVE_THRESH_MEAN_C
    if block_size % 2 == 0:
        block_size += 1
    block_size = max(block_size, 3)
    return cv2.adaptiveThreshold(gray, 255, adaptive_method, cv2.THRESH_BINARY_INV, block_size, c)


def otsu_binarize(gray: np.ndarray) -> np.ndarray:
    """Apply Otsu thresholding to a grayscale image."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary
