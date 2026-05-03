"""
本文件的作用：
- 提供基础去噪和形态学清理操作，减少扫描噪声、毛刺和小伪连通域。
- 为后续线提取和候选框提取提供更稳定的输入。

建议说明：
- 先做最常用的中值滤波、开运算、闭运算即可。
- 不建议在这里堆太多策略，先保证参数容易理解和调试。
"""

from __future__ import annotations

import cv2
import numpy as np


def median_denoise(img: np.ndarray, ksize: int = 3) -> np.ndarray:
    """Apply a median filter to reduce salt-and-pepper noise."""
    return cv2.medianBlur(img, ksize)


def morph_open(img: np.ndarray, ksize: int = 3) -> np.ndarray:
    """Apply a morphology open operation."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (ksize, ksize))
    return cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel)


def morph_close(img: np.ndarray, ksize: int = 3) -> np.ndarray:
    """Apply a morphology close operation."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (ksize, ksize))
    return cv2.morphologyEx(img, cv2.MORPH_CLOSE, kernel)
