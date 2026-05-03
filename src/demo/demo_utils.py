"""
本文件的作用：
- 存放 demo 相关的小工具函数，比如样例加载、结果整理和展示格式转换。
- 它能避免把界面逻辑和杂项工具都堆进 app.py。

建议说明：
- 前期先留空即可，等 demo 需求变多再逐步补充。
- 保持这个文件偏轻量，别让它反过来成为新的杂物间。
"""

from __future__ import annotations


def format_demo_summary(result: dict) -> str:
    """Format a short human-readable summary for demo output."""
    return (
        f"components={len(result.get('topology', {}).get('components', []))}, "
        f"nodes={len(result.get('topology', {}).get('nodes', []))}"
    )
