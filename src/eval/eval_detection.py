"""
本文件的作用：
- 保留元件检测 gold-truth 评估的轻量扩展接口。
- 当前正式评估入口是 agent_workflow.eval_harness。
"""

from __future__ import annotations


def evaluate_detection(predictions: dict, ground_truth: dict) -> dict:
    """Optional detection metric hook; not used by the current eval harness."""
    _ = predictions, ground_truth
    return {}
