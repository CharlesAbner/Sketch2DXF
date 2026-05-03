"""Build compact case-level summaries for review and regression."""

from __future__ import annotations


def _safe_counts(summary: dict, key: str) -> dict:
    value = summary.get(key, {})
    if isinstance(value, dict):
        return {
            "error": int(value.get("error", 0)),
            "warning": int(value.get("warning", 0)),
            "info": int(value.get("info", 0)),
        }
    return {"error": 0, "warning": 0, "info": 0}


def _top_items(items: list[dict], limit: int) -> list[dict]:
    return items[: max(0, int(limit))]


def _compact_risk(flag: dict) -> dict:
    return {
        "code": flag.get("code"),
        "severity": flag.get("severity"),
        "message": flag.get("message"),
        "refs": flag.get("refs", {}),
    }


def _compact_candidate(candidate: dict) -> dict:
    return {
        "repair_candidate_id": candidate.get("repair_candidate_id"),
        "issue_type": candidate.get("issue_type"),
        "severity": candidate.get("severity"),
        "recommended_action": candidate.get("recommended_action"),
        "rationale": candidate.get("rationale"),
        "refs": candidate.get("refs", {}),
    }


def _review_status(audit_inputs: dict, repair_candidates: dict) -> str:
    audit_summary = audit_inputs.get("summary", {})
    repair_summary = repair_candidates.get("summary", {})
    risk_counts = _safe_counts(audit_summary, "risk_counts")
    candidate_counts = _safe_counts(repair_summary, "severity_counts")

    if (
        audit_summary.get("quality_label") == "fail"
        or not audit_summary.get("export_success", False)
        or risk_counts["error"] > 0
        or candidate_counts["error"] > 0
    ):
        return "fail"
    if (
        audit_summary.get("quality_label") == "pass_with_warnings"
        or audit_summary.get("fallback_used")
        or risk_counts["warning"] > 0
        or candidate_counts["warning"] > 0
    ):
        return "needs_review"
    return "pass"


def _agent_ready(audit_inputs: dict, repair_candidates: dict) -> bool:
    audit_summary = audit_inputs.get("summary", {})
    repair_summary = repair_candidates.get("summary", {})
    risk_counts = _safe_counts(audit_summary, "risk_counts")
    candidate_counts = _safe_counts(repair_summary, "severity_counts")
    return bool(
        audit_summary.get("export_success", False)
        and risk_counts["error"] == 0
        and candidate_counts["error"] == 0
        and repair_candidates.get("topology_mutated") is False
    )


def _artifact_list(debug_run_name: str | None) -> dict:
    if not debug_run_name:
        return {}
    base = f"outputs/debug_runs/{debug_run_name}"
    return {
        "debug_dir": base,
        "overlay": f"{base}/13_overlay.png",
        "active_nodes": f"{base}/12_active_nodes.png",
        "audit_inputs": f"{base}/audit_inputs.json",
        "repair_candidates": f"{base}/repair_candidates.json",
        "topology": f"{base}/topology.json",
        "netlist": f"{base}/netlist.json",
        "dxf": f"{base}/14_export.dxf",
    }


def build_case_summary(
    case_id: str,
    image_path: str,
    audit_inputs: dict,
    repair_candidates: dict,
    debug_run_name: str | None = None,
    review_item_limit: int = 12,
) -> dict:
    """Build a compact stable entry point for humans, agents, and regression."""
    audit_summary = audit_inputs.get("summary", {})
    repair_summary = repair_candidates.get("summary", {})
    risk_flags = audit_inputs.get("risk_flags", [])
    candidates = repair_candidates.get("candidates", [])
    risk_counts = _safe_counts(audit_summary, "risk_counts")
    candidate_counts = _safe_counts(repair_summary, "severity_counts")
    status = _review_status(audit_inputs, repair_candidates)

    return {
        "schema_version": "2.2-case-summary",
        "case_id": case_id,
        "image_path": image_path,
        "review_status": status,
        "agent_ready": _agent_ready(audit_inputs, repair_candidates),
        "topology_mutated_by_repair": repair_candidates.get("topology_mutated", None),
        "summary": {
            "quality_label": audit_summary.get("quality_label"),
            "selected_node_source": audit_summary.get("selected_node_source"),
            "fallback_used": audit_summary.get("fallback_used"),
            "consistency_score": audit_summary.get("consistency_score"),
            "needs_repair": audit_summary.get("needs_repair"),
            "export_success": audit_summary.get("export_success"),
            "component_count": audit_summary.get("component_count", 0),
            "pin_count": audit_summary.get("pin_count", 0),
            "connection_count": audit_summary.get("connection_count", 0),
            "node_count": audit_summary.get("node_count", 0),
            "net_count": audit_summary.get("net_count", 0),
            "raw_component_count": audit_summary.get("raw_component_count", 0),
            "supported_raw_component_count": audit_summary.get("supported_raw_component_count", 0),
            "unsupported_raw_component_count": audit_summary.get("unsupported_raw_component_count", 0),
            "relay_supported_raw_component_count": audit_summary.get(
                "relay_supported_raw_component_count",
                0,
            ),
            "risk_counts": risk_counts,
            "repair_candidate_count": repair_summary.get("candidate_count", 0),
            "repair_candidate_counts": candidate_counts,
        },
        "issue_overview": {
            "risk_type_counts": {
                flag.get("code", "unknown"): sum(
                    1 for item in risk_flags if item.get("code") == flag.get("code")
                )
                for flag in risk_flags
            },
            "repair_type_counts": repair_summary.get("type_counts", {}),
            "repair_action_counts": repair_summary.get("action_counts", {}),
        },
        "review_focus": {
            "risks": [_compact_risk(flag) for flag in _top_items(risk_flags, review_item_limit)],
            "repair_candidates": [
                _compact_candidate(candidate)
                for candidate in _top_items(candidates, review_item_limit)
            ],
        },
        "artifacts": _artifact_list(debug_run_name),
    }
