"""
本文件的作用：
- 对元件 proposals 做轻量分类封装。
- 当前主流程在使用 YOLO proposal 时，很多类别信息会直接透传到这里。
"""

from __future__ import annotations


def classify_component_proposals(proposal_result: dict, config: dict) -> dict:
    """Classify each proposal with a lightweight placeholder strategy."""
    _ = config
    classified = []
    for proposal in proposal_result["proposals"]:
        classified.append(
            {
                **proposal,
                "class_name": proposal.get("class_name", "unknown"),
                "score": proposal.get("score", 0.0),
                "orientation": proposal.get("orientation", "unknown"),
            }
        )
    return {"components": classified}
