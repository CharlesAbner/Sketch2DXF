"""Generate a compact template explanation for pipeline results.

This is not the dynamic LLM agent layer. It is a deterministic convenience
summary returned by ``run_pipeline``.
"""

from __future__ import annotations


def generate_explanation(topology: dict, audit_report: dict, config: dict) -> dict:
    """Generate a concise explanation for the recovered result."""
    _ = config
    return {
        "summary": (
            f"Recovered {len(topology['components'])} components, "
            f"{len(topology['nodes'])} nodes, and {len(topology['connections'])} connections."
        ),
        "audit_summary": audit_report["summary"],
    }
