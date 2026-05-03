"""
本文件的作用：
- 保留端到端 gold-truth/人工统计评估的轻量扩展接口。
- 当前正式评估入口是 agent_workflow.eval_harness。
"""

from __future__ import annotations


def evaluate_end_to_end(results: list[dict]) -> dict:
    """Optional end-to-end metric hook; not used by the current eval harness."""
    _ = results
    return {}
