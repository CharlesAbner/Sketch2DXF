"""Generate a lightweight rule audit summary for the production pipeline.

The richer LLM/tool-driven agent audit lives in ``src.agent_workflow``. This
module stays intentionally small so ``run_pipeline`` can return a compact
validation summary without invoking the agent workflow.
"""

from __future__ import annotations


def generate_audit_report(topology: dict, consistency: dict, config: dict) -> dict:
    """Generate a lightweight audit report from topology and rule checks."""
    _ = config
    return {
        "summary": "Audit skeleton ready.",
        "topology_component_count": len(topology["components"]),
        "warning_count": len(consistency["warnings"]),
        "warnings": consistency["warnings"],
    }
