"""
本文件的作用：
- 把导线、交点、候选框、分类结果整合成统一的感知输出。
- 为 topology 层提供单一且稳定的输入格式。

建议说明：
- 这一步非常适合早做，因为它能逼你统一字段名和数据结构。
- 先做简单融合即可，不必一开始就做复杂冲突裁决。
"""

from __future__ import annotations


def fuse_perception_results(
    wire_result: dict,
    junction_result: dict,
    proposal_result: dict,
    classification_result: dict,
    config: dict,
) -> dict:
    """Merge perception-stage outputs into a single result object."""
    _ = config
    return {
        "wire_segments": wire_result["segments"],
        "wire_mask": wire_result["wire_mask"],
        "junctions": junction_result["junctions"],
        "endpoints": junction_result["endpoints"],
        "proposals": proposal_result["proposals"],
        "components": classification_result["components"],
    }
