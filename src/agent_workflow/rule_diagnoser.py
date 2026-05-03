"""Rule-based diagnosis used by artifact-driven agent workflows."""

from __future__ import annotations

from typing import Any

from src.agent_workflow.schemas import RecommendedAction, StageDiagnosis, TopologySemanticAudit


def _diagnosis(
    stage: str,
    issue_type: str,
    severity: str,
    confidence: float,
    rationale: str,
    evidence_codes: list[str],
) -> StageDiagnosis:
    return StageDiagnosis(
        suspected_stage=stage,  # type: ignore[arg-type]
        issue_type=issue_type,
        severity=severity,  # type: ignore[arg-type]
        confidence=round(float(confidence), 3),
        rationale=rationale,
        evidence_codes=evidence_codes,
    )


def _action(
    action_id: str,
    action_type: str,
    priority: str,
    description: str,
    target_refs: dict | None = None,
    requires_human_review: bool = True,
) -> RecommendedAction:
    return RecommendedAction(
        action_id=action_id,
        action_type=action_type,
        priority=priority,  # type: ignore[arg-type]
        description=description,
        target_refs=target_refs or {},
        requires_human_review=requires_human_review,
    )


def build_stage_diagnoses(facts: dict[str, Any], semantic_audit: TopologySemanticAudit) -> list[StageDiagnosis]:
    """Infer likely failure stages from deterministic facts."""
    diagnoses: list[StageDiagnosis] = []
    stressor_text = " ".join(facts.get("known_stressors", [])).lower()
    risk_counts = facts.get("risk_counts", {})
    repair_counts = facts.get("repair_counts", {})
    terminal_stats = facts.get("terminal_attachment_stats", {})
    supported_stats = facts.get("supported_graph_stats", {})
    graph_stats = facts.get("graph_nodes_stats", {})
    summary = facts.get("summary", {})

    if not facts.get("core_artifacts_ok", True):
        return [
            _diagnosis(
                "artifact_loading",
                "insufficient_artifacts",
                "error",
                1.0,
                "Required debug artifacts are missing, so the case cannot be audited reliably.",
                ["insufficient_artifacts"],
            )
        ]

    if risk_counts.get("error", 0) > 0 or repair_counts.get("error", 0) > 0:
        diagnoses.append(
            _diagnosis(
                "topology_semantics",
                "error_level_audit_or_repair_item",
                "error",
                0.9,
                "The deterministic audit produced error-level findings.",
                ["case_summary"],
            )
        )

    if semantic_audit.single_pin_nets:
        if facts.get("unmatched_pin_ids"):
            stage = "terminal_attachment"
            issue = "unmatched_terminal_connection"
            rationale = "Single-pin nets appear together with unmatched terminals."
            evidence = ["single_pin_nets", "unmatched_pins"]
        else:
            stage = "graph_node_merge"
            issue = "split_real_connection_or_missing_bridge"
            rationale = (
                "All terminals are matched, but single-pin nets remain; this points to a "
                "graph/node merge or bridge-support issue rather than component detection."
            )
            evidence = ["single_pin_nets"]
        diagnoses.append(_diagnosis(stage, issue, "warning", 0.82, rationale, evidence))

    if "slanted" in stressor_text:
        diagnoses.append(
            _diagnosis(
                "capability_boundary",
                "non_orthogonal_wire_not_supported",
                "warning",
                0.92,
                "This case is tagged as a slanted-wire stressor; the current evidence pipeline is primarily h/v oriented.",
                ["known_slanted_stressor"],
            )
        )

    low_confidence_count = len(facts.get("low_confidence_matches", []))
    if low_confidence_count > 0:
        diagnoses.append(
            _diagnosis(
                "terminal_attachment",
                "low_confidence_terminal_match",
                "warning",
                min(0.85, 0.55 + low_confidence_count * 0.08),
                f"{low_confidence_count} terminal match risk(s) were reported.",
                ["low_confidence_matches"],
            )
        )
    weak_confidence_count = len(facts.get("weak_confidence_matches", []))
    if weak_confidence_count > 0:
        diagnoses.append(
            _diagnosis(
                "terminal_attachment",
                "weak_confidence_terminal_match",
                "info",
                min(0.75, 0.45 + weak_confidence_count * 0.05),
                f"{weak_confidence_count} terminal match(es) are usable but moderate-confidence.",
                ["weak_confidence_matches"],
            )
        )

    unsupported_count = int(supported_stats.get("unsupported_raw_component_count", 0))
    raw_count = int(supported_stats.get("raw_component_count", 0))
    if raw_count > 0 and unsupported_count / raw_count >= 0.4:
        diagnoses.append(
            _diagnosis(
                "wire_evidence",
                "high_unsupported_evidence_ratio",
                "info",
                0.65,
                "A large fraction of raw evidence is unsupported; this may be noise or weak terminal anchoring.",
                ["unsupported_evidence"],
            )
        )

    attached = int(terminal_stats.get("attached_pin_count", 0))
    pin_count = int(terminal_stats.get("pin_count", facts.get("pin_count", 0)))
    if pin_count > 0 and attached < pin_count:
        diagnoses.append(
            _diagnosis(
                "terminal_attachment",
                "missing_terminal_attachment",
                "error",
                0.9,
                f"Only {attached}/{pin_count} pins have attachment candidates.",
                ["unmatched_pins"],
            )
        )

    if facts.get("node_selection", {}).get("fallback_used"):
        diagnoses.append(
            _diagnosis(
                "supported_graph",
                "graph_derived_nodes_rejected",
                "warning",
                0.75,
                "Graph-derived nodes were rejected and the pipeline fell back to legacy nodes.",
                ["fallback_used"],
            )
        )

    if int(graph_stats.get("relay_graph_node_count", 0)) > 0:
        diagnoses.append(
            _diagnosis(
                "supported_graph",
                "relay_supported_node_used",
                "info",
                0.55,
                "At least one selected graph node uses relay evidence.",
                ["case_summary"],
            )
        )

    if not diagnoses and summary.get("quality_label") in {"pass", "pass_with_warnings"}:
        diagnoses.append(
            _diagnosis(
                "unknown",
                "no_blocking_issue_detected",
                "info",
                0.7,
                "No blocking stage-specific issue was found in the structured artifacts.",
                ["case_summary"],
            )
        )

    return sorted(diagnoses, key=lambda item: ({"error": 0, "warning": 1, "info": 2}[item.severity], -item.confidence))


def build_recommended_actions(
    facts: dict[str, Any],
    semantic_audit: TopologySemanticAudit,
    diagnoses: list[StageDiagnosis],
) -> list[RecommendedAction]:
    """Create conservative, non-mutating next actions."""
    actions: list[RecommendedAction] = []
    action_index = 1

    def add(
        action_type: str,
        priority: str,
        description: str,
        target_refs: dict | None = None,
        requires_human_review: bool = True,
    ) -> None:
        nonlocal action_index
        actions.append(
            _action(
                f"A{action_index}",
                action_type,
                priority,
                description,
                target_refs,
                requires_human_review=requires_human_review,
            )
        )
        action_index += 1

    if not facts.get("core_artifacts_ok", True):
        add(
            "regenerate_full_debug_run",
            "high",
            "Regenerate this case with a full debug run before running agent audit.",
            {"missing_required_artifacts": facts.get("missing_required_artifacts", [])},
        )
        return actions

    if semantic_audit.single_pin_nets:
        add(
            "inspect_single_pin_nets",
            "high",
            "Inspect single-pin nets as likely open-circuit or split-connection symptoms.",
            {"net_ids": semantic_audit.single_pin_nets},
        )
        add(
            "prepare_merge_repair_dry_run",
            "high",
            "Try dry-run node merge candidates for nearby terminal-supported single-pin nets.",
            {"net_ids": semantic_audit.single_pin_nets},
        )

    if any(d.issue_type == "non_orthogonal_wire_not_supported" for d in diagnoses):
        add(
            "mark_capability_boundary",
            "high",
            "Mark this case as a non-orthogonal wire capability boundary rather than tuning h/v-only rules.",
            {"stressors": facts.get("known_stressors", [])},
        )

    if facts.get("low_confidence_matches"):
        add(
            "inspect_terminal_attachments",
            "medium",
            "Inspect terminal_attachments for low-confidence or ambiguous pin-node matches.",
        )
    elif facts.get("weak_confidence_matches"):
        add(
            "spot_check_terminal_attachments",
            "low",
            "Spot-check moderate-confidence terminal matches if the topology is otherwise semantically complete.",
            {
                "pin_ids": [
                    flag.get("refs", {}).get("pin_id")
                    for flag in facts.get("weak_confidence_matches", [])
                ]
            },
        )

    unsupported_count = int(facts.get("supported_graph_stats", {}).get("unsupported_raw_component_count", 0))
    if unsupported_count:
        add(
            "review_unsupported_evidence",
            "medium",
            "Confirm whether unsupported evidence is noise or missed bridge support.",
            {"unsupported_raw_component_count": unsupported_count},
        )

    if not facts.get("has_power_source"):
        add(
            "confirm_missing_power_source",
            "low",
            "Confirm whether the drawing is a passive subcircuit or the detector missed a power source.",
        )

    if not actions:
        add(
            "accept_with_spot_check",
            "low",
            "No blocking issue detected; spot-check overlay and active nodes before accepting.",
            requires_human_review=True,
        )

    return actions


def choose_overall_status(
    facts: dict[str, Any],
    semantic_audit: TopologySemanticAudit,
    diagnoses: list[StageDiagnosis],
) -> tuple[str, str, str, float]:
    """Return overall status, primary issue, suspected stage, confidence."""
    if not facts.get("core_artifacts_ok", True):
        return "fail", "insufficient_artifacts", "artifact_loading", 1.0
    if any(risk.severity == "error" for risk in semantic_audit.semantic_risks):
        primary = next(risk for risk in semantic_audit.semantic_risks if risk.severity == "error")
        return "fail", primary.risk_type, "topology_semantics", 0.9
    if diagnoses and diagnoses[0].severity == "error":
        return "fail", diagnoses[0].issue_type, diagnoses[0].suspected_stage, diagnoses[0].confidence
    if diagnoses:
        main = diagnoses[0]
        if main.severity == "warning" and main.issue_type != "no_blocking_issue_detected":
            return "needs_review", main.issue_type, main.suspected_stage, main.confidence
    if facts.get("summary", {}).get("quality_label") == "pass_with_warnings":
        return "needs_review", "audit_warnings_present", "unknown", 0.65
    return "pass", "no_blocking_issue_detected", "unknown", 0.75
