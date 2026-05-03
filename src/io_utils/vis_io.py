"""
本文件的作用：
- 统一保存中间可视化图，例如 binary、skeleton、overlay、nodes。
- 后续答辩、调参和排错都会频繁依赖这些输出。

建议说明：
- 不要求这里很复杂，重点是文件命名规范和输出路径稳定。
- 每完成一个子模块，都建议把关键可视化接进来。
"""

from __future__ import annotations

from pathlib import Path

from src.io_utils.image_io import save_image


def save_debug_image(path: str | Path, image) -> str:
    """Persist a debug image and return the saved path."""
    save_image(path, image)
    return str(Path(path))
