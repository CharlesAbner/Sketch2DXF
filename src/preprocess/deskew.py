"""
本文件的作用：
- 处理输入图像的轻微倾斜，避免后续水平/垂直导线提取受干扰。
- 当前阶段先保留轻量实现接口，后续视样例难度再加强。

建议说明：
- 如果你的样例图都较规整，可以先把这层弱化。
- 但文件和函数接口最好先放好，避免后续返工。
"""

from __future__ import annotations

import numpy as np


def estimate_skew_angle(img: np.ndarray) -> float:
    """Estimate image skew angle; placeholder for future implementation."""
    _ = img
    return 0.0


def deskew(img: np.ndarray, angle: float) -> np.ndarray:
    """Return a deskewed image; placeholder keeps the input unchanged."""
    _ = angle
    return img
