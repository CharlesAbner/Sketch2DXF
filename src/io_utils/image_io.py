"""
本文件的作用：
- 负责读图、写图和基础颜色空间转换。
- 给上层模块提供统一、稳定的图像输入输出接口。

建议说明：
- 这里尽量只做薄封装，不要混入复杂算法。
- 所有图像数组约定在这里先统一格式，后面模块会轻松很多。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def load_image(path: str | Path) -> np.ndarray:
    """Load an image from disk using OpenCV."""
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Failed to load image: {path}")
    return image


def save_image(path: str | Path, image: np.ndarray) -> None:
    """Save an image to disk, creating parent folders when needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)


def to_gray(image: np.ndarray) -> np.ndarray:
    """Convert a BGR image into grayscale."""
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
