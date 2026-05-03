"""
本文件的作用：
- 对恢复出的结构做轻量布局规整，供导出层使用。
- 当前版本仍然基本保留原图的相对几何位置，只预留统一入口。
"""

from __future__ import annotations


def normalize_layout(topology: dict, config: dict) -> dict:
    """Return a lightly normalized topology layout."""
    _ = config
    return topology
