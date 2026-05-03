"""Generate deterministic repair candidates without mutating topology."""

from __future__ import annotations

from collections import Counter


def _unique(items: list) -> list:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _all_pins(pin_result: dict) -> list[dict]:
    pins = []
    for pin_group in pin_result.get("pins", []):
        for pin in pin_group.get("pins", []):
            pins.append({**pin, "component_id": pin_group["component_id"]})
    return pins


def _match_by_pin(match_result: dict) -> dict[str, dict]:
    return {match["pin_id"]: match for match in match_result.get("matches", [])}


def _attachment_by_pin(terminal_attachments: dict) -> dict[str, dict]:
    return {
        attachment["pin_id"]: attachment
        for attachment in terminal_attachments.get("attachments", [])
    }


def _config_values(config: dict) -> dict:
    repair_cfg = config.get("repair", {})
    audit_cfg = config.get("audit", {})
    topology_cfg = config.get("topology", {})
    return {
        "low_confidence_threshold": float(
            repair_cfg.get(
                "low_confidence_match_threshold",
                audit_cfg.get("low_confidence_match_threshold", 0.6),
            )
        ),
        "weak_confidence_threshold": float(
            repair_cfg.get(
                "weak_confidence_match_threshold",
                audit_cfg.get("weak_confidence_match_threshold", 0.75),
            )
        ),
        "ambiguous_match_confidence_margin": float(
            repair_cfg.get("ambiguous_match_confidence_margin", 0.1)
        ),
        "ambiguous_attachment_score_margin": float(
            repair_cfg.get(
                "ambiguous_attachment_score_margin",
                topology_cfg.get("supported_graph_candidate_score_margin", 0.15),
            )
        ),
        "weak_attachment_score_threshold": float(
            repair_cfg.get("weak_attachment_score_threshold", 0.7)
        ),
        "candidate_alternative_limit": int(repair_cfg.get("candidate_alternative_limit", 3)),
        "unsupported_bridge_review_distance": float(
            repair_cfg.get(
                "unsupported_bridge_review_distance",
                topology_cfg.get("node_bridge_gap", 32),
            )
        ),
    }


def _candidate(
    candidates: list[dict],
    issue_type: str,
    severity: str,
    recommended_action: str,
    rationale: str,
    refs: dict,
    evidence: dict | None = None,
) -> None:
    candidates.append(
        {
            "repair_candidate_id": f"RCAND{len(candidates) + 1}",
            "issue_type": issue_type,
            "severity": severity,
            "status": "candidate_only",
            "recommended_action": recommended_action,
            "rationale": rationale,
            "refs": refs,
            "evidence": evidence or {},
        }
    )


def _compact_match(match: dict | None) -> dict | None:
    if not match:
        return None
    return {
        "pin_id": match.get("pin_id"),
        "node_id": match.get("node_id"),
        "confidence": match.get("confidence"),
        "distance": match.get("distance"),
        "match_type": match.get("match_type"),
        "evidence_type": match.get("evidence_type"),
        "evidence_id": match.get("evidence_id"),
        "alignment_score": match.get("alignment_score"),
    }


def _compact_attachment_candidate(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    return {
        "attachment_id": candidate.get("attachment_id"),
        "raw_component_id": candidate.get("raw_component_id"),
        "evidence_kind": candidate.get("evidence_kind"),
        "evidence_id": candidate.get("evidence_id"),
        "attachment_score": candidate.get("attachment_score"),
        "distance": candidate.get("distance"),
        "forward_distance": candidate.get("forward_distance"),
        "lateral_distance": candidate.get("lateral_distance"),
        "in_corridor": candidate.get("in_corridor"),
        "projected_point": candidate.get("projected_point"),
    }


def _alternative_matches(
    pin_id: str,
    match_result: dict,
    current_match: dict | None,
    limit: int,
) -> list[dict]:
    alternatives = []
    current_key = None
    if current_match:
        current_key = (
            current_match.get("node_id"),
            current_match.get("evidence_type"),
            current_match.get("evidence_id"),
        )
    for candidate in match_result.get("candidates_by_pin", {}).get(pin_id, []):
        key = (
            candidate.get("node_id"),
            candidate.get("evidence_type"),
            candidate.get("evidence_id"),
        )
        if key == current_key:
            continue
        alternatives.append(_compact_match(candidate))
        if len(alternatives) >= limit:
            break
    return [item for item in alternatives if item is not None]


def _has_distinct_node_alternative(current_match: dict | None, alternatives: list[dict]) -> bool:
    if current_match is None:
        return bool(alternatives)
    current_node_id = current_match.get("node_id")
    return any(alternative.get("node_id") != current_node_id for alternative in alternatives)


def _alternative_attachments(attachment: dict, limit: int) -> list[dict]:
    alternatives = []
    best_raw_component_id = attachment.get("best_raw_component_id")
    best_attachment_id = attachment.get("best_attachment_id")
    for candidate in attachment.get("candidates", []):
        if candidate.get("attachment_id") == best_attachment_id:
            continue
        if candidate.get("raw_component_id") == best_raw_component_id:
            continue
        alternatives.append(_compact_attachment_candidate(candidate))
        if len(alternatives) >= limit:
            break
    return [item for item in alternatives if item is not None]


def _component_bridges(raw_component_id: str, supported_graph: dict) -> list[dict]:
    bridges = []
    for bridge in supported_graph.get("bridge_candidates", []):
        if raw_component_id not in {bridge.get("from_component_id"), bridge.get("to_component_id")}:
            continue
        bridges.append(
            {
                "bridge_candidate_id": bridge.get("bridge_candidate_id"),
                "candidate_type": bridge.get("candidate_type"),
                "from_component_id": bridge.get("from_component_id"),
                "to_component_id": bridge.get("to_component_id"),
                "support_status": bridge.get("support_status"),
                "distance": bridge.get("distance"),
                "points": bridge.get("points"),
                "point": bridge.get("point"),
                "projected_point": bridge.get("projected_point"),
                "support_pin_ids": bridge.get("support_pin_ids", []),
            }
        )
    return bridges


def _add_pin_match_candidates(
    candidates: list[dict],
    pin_result: dict,
    match_result: dict,
    terminal_attachments: dict,
    config_values: dict,
) -> None:
    matches = _match_by_pin(match_result)
    attachments = _attachment_by_pin(terminal_attachments)
    matched_pin_ids = set(matches)
    limit = config_values["candidate_alternative_limit"]

    for pin in _all_pins(pin_result):
        pin_id = pin["pin_id"]
        match = matches.get(pin_id)
        attachment = attachments.get(pin_id, {})
        if pin_id not in matched_pin_ids:
            _candidate(
                candidates,
                "unmatched_pin",
                "error",
                "inspect_terminal_and_evidence",
                "This terminal has no recovered node connection.",
                {"pin_id": pin_id, "component_id": pin["component_id"]},
                {
                    "best_attachment": _compact_attachment_candidate(
                        (attachment.get("candidates") or [None])[0]
                    ),
                    "attachment_candidate_count": attachment.get("candidate_count", 0),
                },
            )
            continue

        confidence = float(match.get("confidence", 0.0))
        alternatives = _alternative_matches(pin_id, match_result, match, limit)
        if confidence < config_values["low_confidence_threshold"]:
            action = (
                "review_alternative_match"
                if _has_distinct_node_alternative(match, alternatives)
                else "review_current_match"
            )
            _candidate(
                candidates,
                "low_confidence_pin_match",
                "warning",
                action,
                "The pin-node match is below the low-confidence threshold.",
                {
                    "pin_id": pin_id,
                    "component_id": pin["component_id"],
                    "node_id": match.get("node_id"),
                },
                {
                    "current_match": _compact_match(match),
                    "alternatives": alternatives,
                    "best_terminal_attachment": {
                        "raw_component_id": attachment.get("best_raw_component_id"),
                        "evidence_kind": attachment.get("best_evidence_kind"),
                        "evidence_id": attachment.get("best_evidence_id"),
                        "attachment_score": attachment.get("best_attachment_score"),
                    },
                    "threshold": config_values["low_confidence_threshold"],
                },
            )
        elif confidence < config_values["weak_confidence_threshold"]:
            _candidate(
                candidates,
                "weak_confidence_pin_match",
                "info",
                "accept_current_with_review",
                "The pin-node match is usable but should remain visible during audit.",
                {
                    "pin_id": pin_id,
                    "component_id": pin["component_id"],
                    "node_id": match.get("node_id"),
                },
                {
                    "current_match": _compact_match(match),
                    "alternatives": alternatives,
                    "threshold": config_values["weak_confidence_threshold"],
                },
            )


def _add_ambiguous_attachment_candidates(
    candidates: list[dict],
    terminal_attachments: dict,
    config_values: dict,
) -> None:
    margin = config_values["ambiguous_attachment_score_margin"]
    weak_threshold = config_values["weak_attachment_score_threshold"]
    limit = config_values["candidate_alternative_limit"]
    for attachment in terminal_attachments.get("attachments", []):
        attachment_candidates = attachment.get("candidates", [])
        if len(attachment_candidates) < 2:
            continue
        best = attachment_candidates[0]
        alternatives = _alternative_attachments(attachment, limit)
        if not alternatives:
            continue
        second = alternatives[0]
        best_score = float(best.get("attachment_score", 0.0))
        second_score = float(second.get("attachment_score", 0.0))
        score_gap = best_score - second_score
        if score_gap > margin and best_score >= weak_threshold:
            continue
        severity = "warning" if best_score < weak_threshold or score_gap <= margin else "info"
        _candidate(
            candidates,
            "ambiguous_terminal_attachment",
            severity,
            "review_terminal_attachment",
            "This terminal has a plausible attachment to another raw evidence component.",
            {
                "pin_id": attachment["pin_id"],
                "component_id": attachment["component_id"],
                "best_raw_component_id": attachment.get("best_raw_component_id"),
            },
            {
                "best_attachment": _compact_attachment_candidate(best),
                "alternatives": alternatives,
                "score_gap": round(float(score_gap), 3),
                "margin": margin,
            },
        )


def _add_unsupported_evidence_candidates(
    candidates: list[dict],
    supported_graph: dict,
    config_values: dict,
) -> None:
    max_review_distance = config_values["unsupported_bridge_review_distance"]
    for component in supported_graph.get("raw_components", []):
        if component.get("support_status") != "unsupported":
            continue
        bridges = _component_bridges(component["raw_component_id"], supported_graph)
        near_supported_bridges = [
            bridge
            for bridge in bridges
            if bridge.get("support_status") == "one_sided_supported"
            and float(bridge.get("distance", 10**9)) <= max_review_distance
        ]
        if near_supported_bridges:
            severity = "warning"
            action = "inspect_possible_missing_support"
            rationale = "Unsupported evidence lies near supported graph evidence and may indicate a gap or missed terminal support."
        else:
            severity = "info"
            action = "confirm_discard_as_noise"
            rationale = "Unsupported evidence was discarded because no terminal or path support was found."
        _candidate(
            candidates,
            "unsupported_evidence_review",
            severity,
            action,
            rationale,
            {"raw_component_id": component["raw_component_id"]},
            {
                "segment_ids": component.get("segment_ids", []),
                "bbox": component.get("bbox"),
                "bridge_candidates": bridges[: config_values["candidate_alternative_limit"]],
            },
        )


def _add_bridge_candidates(candidates: list[dict], supported_graph: dict) -> None:
    for bridge in supported_graph.get("bridge_candidates", []):
        status = bridge.get("support_status")
        if status not in {
            "between_best_supported_components",
            "between_supported_components",
            "between_path_supported_components",
            "one_sided_supported",
        }:
            continue
        severity = "warning" if status == "one_sided_supported" else "info"
        action = "inspect_gap_bridge" if status == "one_sided_supported" else "confirm_bridge"
        _candidate(
            candidates,
            "possible_gap_bridge",
            severity,
            action,
            "A bridge candidate may explain a gap between evidence components.",
            {
                "bridge_candidate_id": bridge.get("bridge_candidate_id"),
                "from_component_id": bridge.get("from_component_id"),
                "to_component_id": bridge.get("to_component_id"),
            },
            {
                "candidate_type": bridge.get("candidate_type"),
                "support_status": status,
                "distance": bridge.get("distance"),
                "support_pin_ids": bridge.get("support_pin_ids", []),
                "points": bridge.get("points"),
                "point": bridge.get("point"),
                "projected_point": bridge.get("projected_point"),
            },
        )


def _add_relay_node_candidates(candidates: list[dict], node_result: dict) -> None:
    for node in node_result.get("nodes", []):
        if node.get("support_status") != "terminal_supported_with_relay":
            continue
        _candidate(
            candidates,
            "relay_node_review",
            "info",
            "confirm_relay_supported_node",
            "This node uses relay evidence between terminal-supported components.",
            {"node_id": node["node_id"]},
            {
                "pin_ids": node.get("pin_ids", []),
                "raw_component_ids": node.get("raw_component_ids", []),
                "relay_raw_component_ids": node.get("relay_raw_component_ids", []),
                "bridge_candidate_ids": node.get("bridge_candidate_ids", []),
            },
        )


def _add_graph_diff_candidates(candidates: list[dict], graph_nodes_dry_run: dict, node_selection: dict) -> None:
    if node_selection.get("fallback_used"):
        _candidate(
            candidates,
            "fallback_used_review",
            "warning",
            "inspect_graph_node_rejection",
            "Graph-derived nodes were rejected and legacy nodes were selected.",
            {"fallback_reasons": node_selection.get("fallback_reasons", [])},
            {"node_diff_stats": node_selection.get("node_diff_stats", {})},
        )

    for comparison in graph_nodes_dry_run.get("node_diff", {}).get("comparisons", []):
        status = comparison.get("status")
        if status in {"exact_match", "near_match"}:
            continue
        _candidate(
            candidates,
            "graph_legacy_diff_review",
            "warning",
            "inspect_graph_legacy_diff",
            "Graph-derived node comparison differs from the legacy node result.",
            {
                "graph_node_id": comparison.get("graph_node_id"),
                "current_node_id": comparison.get("current_node_id"),
                "status": status,
            },
            {
                "segment_jaccard": comparison.get("segment_jaccard"),
                "pin_jaccard": comparison.get("pin_jaccard"),
                "graph_pin_ids": comparison.get("graph_pin_ids", []),
                "current_pin_ids": comparison.get("current_pin_ids", []),
                "graph_segment_ids": comparison.get("graph_segment_ids", []),
                "current_segment_ids": comparison.get("current_segment_ids", []),
            },
        )


def _add_consistency_candidates(candidates: list[dict], consistency_result: dict, topology_result: dict) -> None:
    _ = topology_result
    for warning in consistency_result.get("warnings", []):
        _candidate(
            candidates,
            "consistency_warning_review",
            "warning",
            "inspect_consistency_warning",
            warning,
            {},
        )
    for error in consistency_result.get("errors", []):
        _candidate(
            candidates,
            "consistency_error_review",
            "error",
            "fix_consistency_error",
            error,
            {},
        )


def _severity_counts(candidates: list[dict]) -> dict:
    counts = Counter(candidate["severity"] for candidate in candidates)
    return {
        "error": counts.get("error", 0),
        "warning": counts.get("warning", 0),
        "info": counts.get("info", 0),
    }


def _counts_by(candidates: list[dict], field_name: str) -> dict:
    counts = Counter(candidate.get(field_name, "unknown") for candidate in candidates)
    return dict(sorted(counts.items()))


def build_repair_candidates(
    pin_result: dict,
    terminal_attachments: dict,
    supported_graph: dict,
    graph_nodes_dry_run: dict,
    node_selection: dict,
    node_result: dict,
    match_result: dict,
    topology_result: dict,
    consistency_result: dict,
    config: dict,
) -> dict:
    """Build deterministic repair candidates for audit; never changes topology."""
    config_values = _config_values(config)
    candidates: list[dict] = []

    _add_pin_match_candidates(
        candidates,
        pin_result,
        match_result,
        terminal_attachments,
        config_values,
    )
    _add_ambiguous_attachment_candidates(candidates, terminal_attachments, config_values)
    _add_unsupported_evidence_candidates(candidates, supported_graph, config_values)
    _add_bridge_candidates(candidates, supported_graph)
    _add_relay_node_candidates(candidates, node_result)
    _add_graph_diff_candidates(candidates, graph_nodes_dry_run, node_selection)
    _add_consistency_candidates(candidates, consistency_result, topology_result)

    severity_counts = _severity_counts(candidates)
    return {
        "schema_version": "2.1-repair-candidates",
        "topology_mutated": False,
        "summary": {
            "candidate_count": len(candidates),
            "severity_counts": severity_counts,
            "type_counts": _counts_by(candidates, "issue_type"),
            "action_counts": _counts_by(candidates, "recommended_action"),
            "has_error_candidate": severity_counts["error"] > 0,
            "has_warning_candidate": severity_counts["warning"] > 0,
        },
        "candidates": candidates,
        "config": config_values,
    }
