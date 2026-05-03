"""Skeleton dry-run repair report for agent-guided repair workflows."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent_workflow.candidate_validator import (
    VALIDATOR_VERSION,
    validate_topology_candidate,
)
from src.agent_workflow.candidate_ranker import RANKER_VERSION, rank_repair_candidates


PROTECTED_OUTPUT_FILES = ("topology.json", "netlist.json", "14_export.dxf")
MERGE_NODE_MAX_BBOX_GAP = 80.0
MERGE_NODE_MAX_CANDIDATES = 8
REATTACH_PIN_MAX_CANDIDATES = 8
EVIDENCE_REVIEW_MAX_CANDIDATES = 8
REPAIR_DRY_RUN_MAX_CANDIDATES = 16
REATTACH_PIN_MIN_ATTACHMENT_SCORE = 0.55
REATTACH_PIN_WEAK_CONFIDENCE = 0.75
REQUIRED_DEBUG_ARTIFACTS = (
    "case_summary.json",
    "audit_inputs.json",
    "repair_candidates.json",
    "terminal_attachments.json",
    "supported_graph.json",
    "graph_nodes_dry_run.json",
    "topology.json",
    "validation.json",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _file_digest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "sha256": None,
            "size_bytes": None,
        }
    return {
        "path": str(path),
        "exists": True,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _tool(tool_id: str, reason: str, status: str = "selected") -> dict[str, str]:
    return {
        "tool_id": tool_id,
        "status": status,
        "reason": reason,
    }


def _component_class_lookup(topology: dict[str, Any]) -> dict[str, str]:
    return {
        str(component.get("id")): str(component.get("class_name", "")).lower()
        for component in topology.get("components", [])
    }


def _is_power_class(class_name: str) -> bool:
    return (
        class_name in {"power_source", "voltage_source", "battery", "source"}
        or "voltage" in class_name
        or "battery" in class_name
    )


def _pin_component_lookup(topology: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for group in topology.get("pins", []):
        component_id = group.get("component_id")
        for pin in group.get("pins", []):
            lookup[str(pin.get("pin_id"))] = str(pin.get("component_id") or component_id)
    for connection in topology.get("connections", []):
        lookup[str(connection.get("pin_id"))] = str(connection.get("component_id"))
    return lookup


def _all_pin_ids(topology: dict[str, Any]) -> set[str]:
    pin_ids: set[str] = set()
    for group in topology.get("pins", []):
        for pin in group.get("pins", []):
            if pin.get("pin_id"):
                pin_ids.add(str(pin.get("pin_id")))
    return pin_ids


def _find(parent: dict[str, str], node_id: str) -> str:
    parent.setdefault(node_id, node_id)
    if parent[node_id] != node_id:
        parent[node_id] = _find(parent, parent[node_id])
    return parent[node_id]


def _union(parent: dict[str, str], left: str, right: str) -> None:
    left_root = _find(parent, left)
    right_root = _find(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


def _metrics_for_merge(
    topology: dict[str, Any],
    merge_nodes: list[str] | None = None,
    pin_node_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    merge_nodes = merge_nodes or []
    pin_node_overrides = pin_node_overrides or {}
    parent: dict[str, str] = {}
    for node in topology.get("nodes", []):
        node_id = str(node.get("node_id"))
        parent[node_id] = node_id
    if len(merge_nodes) > 1:
        first = merge_nodes[0]
        for other in merge_nodes[1:]:
            _union(parent, first, other)

    pin_to_component = _pin_component_lookup(topology)
    component_classes = _component_class_lookup(topology)
    net_pins: dict[str, list[dict[str, str]]] = {}
    connected_pin_ids: set[str] = set()
    for connection in topology.get("connections", []):
        pin_id = str(connection.get("pin_id"))
        component_id = str(connection.get("component_id") or pin_to_component.get(pin_id, ""))
        node_id = str(pin_node_overrides.get(pin_id) or connection.get("node_id"))
        parent.setdefault(node_id, node_id)
        merged_node_id = _find(parent, node_id)
        connected_pin_ids.add(pin_id)
        net_pins.setdefault(merged_node_id, []).append(
            {
                "pin_id": pin_id,
                "component_id": component_id,
            }
        )

    all_pins = _all_pin_ids(topology)
    unmatched_pin_ids = sorted(pin_id for pin_id in all_pins if pin_id not in connected_pin_ids)
    zero_pin_net_ids = sorted(node_id for node_id in parent if _find(parent, node_id) not in net_pins)
    single_pin_net_ids = sorted(
        net_id for net_id, pins in net_pins.items() if len({pin["pin_id"] for pin in pins}) == 1
    )

    component_to_nets: dict[str, list[str]] = {}
    net_to_components: dict[str, set[str]] = {}
    for net_id, pins in net_pins.items():
        for pin in pins:
            component_id = pin["component_id"]
            if not component_id:
                continue
            component_to_nets.setdefault(component_id, []).append(net_id)
            net_to_components.setdefault(net_id, set()).add(component_id)

    self_shorts = []
    source_shorts = []
    for component_id, net_ids in component_to_nets.items():
        duplicate_net_ids = sorted({net_id for net_id in net_ids if net_ids.count(net_id) > 1})
        if not duplicate_net_ids:
            continue
        record = {
            "component_id": component_id,
            "net_ids": duplicate_net_ids,
        }
        self_shorts.append(record)
        if _is_power_class(component_classes.get(component_id, "")):
            source_shorts.append(record)

    component_ids = set(component_to_nets)
    visited: set[str] = set()
    groups = []
    for component_id in sorted(component_ids):
        if component_id in visited:
            continue
        stack = [component_id]
        visited.add(component_id)
        group = []
        while stack:
            current = stack.pop()
            group.append(current)
            for net_id in component_to_nets.get(current, []):
                for neighbor in net_to_components.get(net_id, set()):
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    stack.append(neighbor)
        groups.append(sorted(group))

    return {
        "node_count": len(set(_find(parent, node_id) for node_id in parent)),
        "net_count": len(net_pins),
        "single_pin_net_count": len(single_pin_net_ids),
        "single_pin_net_ids": single_pin_net_ids,
        "zero_pin_net_count": len(zero_pin_net_ids),
        "zero_pin_net_ids": zero_pin_net_ids,
        "unmatched_pin_count": len(unmatched_pin_ids),
        "unmatched_pin_ids": unmatched_pin_ids,
        "component_self_short_count": len(self_shorts),
        "component_self_shorts": self_shorts,
        "source_terminal_short_count": len(source_shorts),
        "source_terminal_shorts": source_shorts,
        "connected_component_count": len(groups),
        "connected_component_groups": groups,
        "component_count": len(topology.get("components", [])),
        "pin_count": len(all_pins),
    }


def _bbox_gap(left: list[float] | None, right: list[float] | None) -> dict[str, float | bool]:
    if not left or not right or len(left) < 4 or len(right) < 4:
        return {"dx": 999999.0, "dy": 999999.0, "distance": 999999.0, "axis_aligned": False}
    lx1, ly1, lx2, ly2 = [float(value) for value in left[:4]]
    rx1, ry1, rx2, ry2 = [float(value) for value in right[:4]]
    dx = max(rx1 - lx2, lx1 - rx2, 0.0)
    dy = max(ry1 - ly2, ly1 - ry2, 0.0)
    distance = math.hypot(dx, dy)
    axis_aligned = dx <= 6.0 or dy <= 6.0
    return {"dx": dx, "dy": dy, "distance": distance, "axis_aligned": axis_aligned}


def _node_lookup(nodes_payload: dict[str, Any], topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = nodes_payload.get("nodes") or topology.get("nodes", [])
    return {str(node.get("node_id")): node for node in nodes if node.get("node_id")}


def _single_pin_node_ids(agent_audit: dict[str, Any] | None, topology: dict[str, Any]) -> list[str]:
    audit_net_ids = []
    if agent_audit:
        audit_net_ids = agent_audit.get("topology_semantic_audit", {}).get("single_pin_nets", [])
    if audit_net_ids:
        return [str(net_id) for net_id in audit_net_ids]
    return [
        str(net.get("node_id") or net.get("net_id"))
        for net in topology.get("nets", [])
        if int(net.get("pin_count", 0)) == 1
    ]


def _score_merge_candidate(
    before: dict[str, Any],
    after: dict[str, Any],
    gap: dict[str, float | bool],
    both_single_pin: bool,
    agent_audit: dict[str, Any] | None,
) -> float:
    improvement = before["single_pin_net_count"] - after["single_pin_net_count"]
    score = 0.2
    if both_single_pin:
        score += 0.25
    if improvement > 0:
        score += min(0.3, improvement * 0.15)
    distance = float(gap["distance"])
    if distance <= 12:
        score += 0.15
    elif distance <= 40:
        score += 0.12
    elif distance <= MERGE_NODE_MAX_BBOX_GAP:
        score += 0.06
    if gap["axis_aligned"]:
        score += 0.08
    if agent_audit and agent_audit.get("primary_issue") == "split_real_connection_or_missing_bridge":
        score += 0.08
    return round(min(score, 0.99), 3)


def _risk_level(score: float, blocking_issues: list[str], gap_distance: float) -> str:
    if blocking_issues:
        return "high"
    if score >= 0.78 and gap_distance <= MERGE_NODE_MAX_BBOX_GAP:
        return "low"
    if score >= 0.55:
        return "medium"
    return "high"


def _generate_merge_node_candidates(
    debug_dir: Path,
    agent_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    topology = _read_json(debug_dir / "topology.json") or {}
    nodes_payload = _read_json(debug_dir / "nodes.json") or {}
    nodes = _node_lookup(nodes_payload, topology)
    single_pin_node_ids = [
        node_id for node_id in _single_pin_node_ids(agent_audit, topology) if node_id in nodes
    ]
    if len(single_pin_node_ids) < 2:
        return []

    before = _metrics_for_merge(topology)
    candidates: list[dict[str, Any]] = []
    candidate_index = 1
    for left_index, left_id in enumerate(single_pin_node_ids):
        for right_id in single_pin_node_ids[left_index + 1 :]:
            left_node = nodes[left_id]
            right_node = nodes[right_id]
            gap = _bbox_gap(left_node.get("bbox"), right_node.get("bbox"))
            if float(gap["distance"]) > MERGE_NODE_MAX_BBOX_GAP:
                continue
            after = _metrics_for_merge(topology, [left_id, right_id])
            validation = validate_topology_candidate("merge_nodes", before, after)
            blocking = validation["blocking_issues"]
            score = _score_merge_candidate(before, after, gap, True, agent_audit)
            improved = validation["improved_metrics"]
            regressed = validation["regressed_metrics"]
            validation_result = validation["validation_result"]
            risk = _risk_level(score, blocking, float(gap["distance"]))
            target_pins = sorted(set(left_node.get("pin_ids", []) + right_node.get("pin_ids", [])))
            reasons = [
                "Both target nodes are single-pin nets.",
                f"BBox gap is {round(float(gap['distance']), 3)} px.",
                "Candidate is axis-aligned." if gap["axis_aligned"] else "Candidate is not axis-aligned.",
                f"Single-pin net count changes {before['single_pin_net_count']} -> {after['single_pin_net_count']}.",
            ]
            if agent_audit and agent_audit.get("primary_issue"):
                reasons.append(f"Source audit issue: {agent_audit.get('primary_issue')}.")
            candidates.append(
                {
                    "candidate_id": f"MRG{candidate_index}",
                    "repair_type": "merge_nodes",
                    "summary": f"Dry-run merge {left_id} + {right_id}.",
                    "target_nodes": [left_id, right_id],
                    "affected_nets": [left_id, right_id],
                    "target_pins": target_pins,
                    "geometry": {
                        "bbox_gap": gap,
                        "left_bbox": left_node.get("bbox"),
                        "right_bbox": right_node.get("bbox"),
                    },
                    "before_metrics": before,
                    "after_metrics": after,
                    "validation": validation,
                    "improved_metrics": improved,
                    "regressed_metrics": regressed,
                    "validation_result": validation_result,
                    "blocking_issues": blocking,
                    "score": score,
                    "risk_level": risk,
                    "reasons": reasons,
                    "mutates_topology": False,
                }
            )
            candidate_index += 1

    return candidates[:MERGE_NODE_MAX_CANDIDATES]


def _connection_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(connection.get("pin_id")): connection
        for connection in topology.get("connections", [])
        if connection.get("pin_id")
    }


def _pin_ids_from_agent_refs(agent_audit: dict[str, Any] | None) -> set[str]:
    if not agent_audit:
        return set()
    pin_ids: set[str] = set()
    for item in agent_audit.get("evidence", []):
        for pin_id in item.get("refs", {}).get("pin_ids", []) or []:
            pin_ids.add(str(pin_id))
    for action in agent_audit.get("recommended_actions", []):
        for pin_id in action.get("target_refs", {}).get("pin_ids", []) or []:
            pin_ids.add(str(pin_id))
    return pin_ids


def _raw_component_to_node_lookup(
    topology: dict[str, Any],
    nodes_payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    lookup = {
        str(raw_component_id): str(node_id)
        for raw_component_id, node_id in topology.get("node_id_map", {}).items()
    }
    for node in topology.get("nodes", []):
        node_id = str(node.get("node_id"))
        for raw_component_id in (
            list(node.get("raw_component_ids", []) or [])
            + list(node.get("merged_raw_node_ids", []) or [])
        ):
            lookup[str(raw_component_id)] = node_id
    if nodes_payload:
        for node in nodes_payload.get("nodes", []):
            node_id = str(node.get("node_id"))
            for raw_component_id in (
                list(node.get("raw_component_ids", []) or [])
                + list(node.get("merged_raw_node_ids", []) or [])
            ):
                lookup.setdefault(str(raw_component_id), node_id)
    return lookup


def _best_attachment_by_raw_component(attachment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    best_by_raw: dict[str, dict[str, Any]] = {}
    for candidate in attachment.get("candidates", []) or []:
        raw_component_id = candidate.get("raw_component_id")
        if not raw_component_id:
            continue
        score = float(candidate.get("attachment_score", 0.0) or 0.0)
        current_best = best_by_raw.get(str(raw_component_id))
        if current_best is None or score > float(current_best.get("attachment_score", 0.0) or 0.0):
            best_by_raw[str(raw_component_id)] = candidate
    return best_by_raw


def _score_reattach_candidate(
    before: dict[str, Any],
    after: dict[str, Any],
    current_confidence: float,
    current_attachment_score: float,
    alternative_attachment_score: float,
    target_pin_from_audit: bool,
) -> float:
    score = 0.12 + min(0.3, alternative_attachment_score * 0.3)
    if current_confidence < 0.6:
        score += 0.16
    elif current_confidence < REATTACH_PIN_WEAK_CONFIDENCE:
        score += 0.11
    elif current_confidence < 0.85:
        score += 0.06
    if target_pin_from_audit:
        score += 0.1
    if alternative_attachment_score >= current_attachment_score:
        score += 0.05
    elif alternative_attachment_score >= current_attachment_score - 0.12:
        score += 0.03
    single_pin_delta = before["single_pin_net_count"] - after["single_pin_net_count"]
    unmatched_delta = before["unmatched_pin_count"] - after["unmatched_pin_count"]
    if single_pin_delta > 0:
        score += min(0.18, single_pin_delta * 0.09)
    if unmatched_delta > 0:
        score += min(0.18, unmatched_delta * 0.12)
    return round(min(score, 0.95), 3)


def _risk_level_for_validation(validation: dict[str, Any], score: float) -> str:
    if validation.get("blocking_issues"):
        return "high"
    if validation.get("validation_result") == "viable" and score >= 0.65:
        return "low"
    if score >= 0.45:
        return "medium"
    return "high"


def _generate_reattach_pin_candidates(
    debug_dir: Path,
    agent_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    topology = _read_json(debug_dir / "topology.json") or {}
    terminal_attachments = _read_json(debug_dir / "terminal_attachments.json") or {}
    nodes_payload = _read_json(debug_dir / "nodes.json") or {}
    raw_to_node = _raw_component_to_node_lookup(topology, nodes_payload)
    connections = _connection_lookup(topology)
    target_pins = _pin_ids_from_agent_refs(agent_audit)
    before = _metrics_for_merge(topology)
    candidates: list[dict[str, Any]] = []
    candidate_index = 1

    for attachment in terminal_attachments.get("attachments", []) or []:
        pin_id = str(attachment.get("pin_id"))
        if not pin_id or pin_id not in connections:
            continue
        current_connection = connections[pin_id]
        current_node_id = str(current_connection.get("node_id"))
        current_confidence = float(current_connection.get("confidence", 0.0) or 0.0)
        current_attachment_score = float(attachment.get("best_attachment_score", 0.0) or 0.0)
        is_target_pin = pin_id in target_pins
        if (
            not is_target_pin
            and current_confidence >= REATTACH_PIN_WEAK_CONFIDENCE
            and current_attachment_score >= REATTACH_PIN_WEAK_CONFIDENCE
        ):
            continue

        for raw_component_id, alternative in _best_attachment_by_raw_component(attachment).items():
            new_node_id = raw_to_node.get(raw_component_id)
            if not new_node_id or new_node_id == current_node_id:
                continue
            alternative_score = float(alternative.get("attachment_score", 0.0) or 0.0)
            if alternative_score < REATTACH_PIN_MIN_ATTACHMENT_SCORE:
                continue
            if not is_target_pin and alternative_score < current_attachment_score - 0.15:
                continue

            after = _metrics_for_merge(topology, pin_node_overrides={pin_id: new_node_id})
            validation = validate_topology_candidate("reattach_pin", before, after)
            score = _score_reattach_candidate(
                before,
                after,
                current_confidence,
                current_attachment_score,
                alternative_score,
                is_target_pin,
            )
            risk = _risk_level_for_validation(validation, score)
            reasons = [
                f"Pin {pin_id} currently connects to {current_node_id}.",
                f"Alternative raw component {raw_component_id} maps to {new_node_id}.",
                f"Current confidence is {round(current_confidence, 3)}.",
                f"Alternative attachment score is {round(alternative_score, 3)}.",
            ]
            if is_target_pin:
                reasons.append("Pin was referenced by the source audit.")

            candidates.append(
                {
                    "candidate_id": f"RPN{candidate_index}",
                    "repair_type": "reattach_pin",
                    "summary": f"Dry-run reattach {pin_id} from {current_node_id} to {new_node_id}.",
                    "target_nodes": [current_node_id, new_node_id],
                    "affected_nets": [current_node_id, new_node_id],
                    "target_pins": [pin_id],
                    "geometry": {
                        "current_projected_point": current_connection.get("projected_point"),
                        "alternative_projected_point": alternative.get("projected_point"),
                        "alternative_raw_component_id": raw_component_id,
                    },
                    "before_metrics": before,
                    "after_metrics": after,
                    "validation": validation,
                    "improved_metrics": validation["improved_metrics"],
                    "regressed_metrics": validation["regressed_metrics"],
                    "validation_result": validation["validation_result"],
                    "blocking_issues": validation["blocking_issues"],
                    "score": score,
                    "risk_level": risk,
                    "reasons": reasons,
                    "evidence": {
                        "current_connection": current_connection,
                        "alternative_attachment": alternative,
                    },
                    "mutates_topology": False,
                }
            )
            candidate_index += 1

    return candidates[:REATTACH_PIN_MAX_CANDIDATES]


def _score_evidence_review_candidate(source_candidate: dict[str, Any]) -> float:
    issue_type = str(source_candidate.get("issue_type", ""))
    severity = str(source_candidate.get("severity", "info"))
    action = str(source_candidate.get("recommended_action", ""))
    score = 0.32
    if issue_type == "possible_gap_bridge":
        score += 0.36
    elif issue_type == "unsupported_evidence_review":
        score += 0.1
    if severity == "warning":
        score += 0.1
    elif severity == "error":
        score += 0.18
    if action == "confirm_bridge":
        score += 0.08
    return round(min(score, 0.85), 3)


def _generate_evidence_review_candidates(
    debug_dir: Path,
    agent_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    topology = _read_json(debug_dir / "topology.json") or {}
    source_payload = _read_json(debug_dir / "repair_candidates.json") or {}
    nodes_payload = _read_json(debug_dir / "nodes.json") or {}
    raw_to_node = _raw_component_to_node_lookup(topology, nodes_payload)
    before = _metrics_for_merge(topology)
    candidates: list[dict[str, Any]] = []
    candidate_index = 1
    audit_issue = str(agent_audit.get("primary_issue", "")) if agent_audit else ""

    for source_candidate in source_payload.get("candidates", []) or []:
        issue_type = str(source_candidate.get("issue_type", ""))
        if issue_type not in {"unsupported_evidence_review", "possible_gap_bridge"}:
            continue
        refs = source_candidate.get("refs", {})
        evidence = source_candidate.get("evidence", {})
        raw_refs = [
            refs.get("raw_component_id"),
            refs.get("from_component_id"),
            refs.get("to_component_id"),
        ]
        target_nodes = sorted(
            {
                raw_to_node[str(raw_component_id)]
                for raw_component_id in raw_refs
                if raw_component_id and str(raw_component_id) in raw_to_node
            }
        )
        validation = validate_topology_candidate("evidence_review", before, before)
        score = _score_evidence_review_candidate(source_candidate)
        if audit_issue == "split_real_connection_or_missing_bridge" and issue_type == "possible_gap_bridge":
            score = round(min(0.9, score + 0.08), 3)
        risk = "low"
        source_id = source_candidate.get("repair_candidate_id")
        reasons = [
            f"Imported from repair_candidates.json as {source_id}.",
            f"Issue type is {issue_type}.",
            f"Recommended action is {source_candidate.get('recommended_action')}.",
        ]
        if evidence.get("distance") is not None:
            reasons.append(f"Evidence distance is {round(float(evidence.get('distance')), 3)} px.")

        candidates.append(
            {
                "candidate_id": f"EVR{candidate_index}",
                "repair_type": "evidence_review",
                "summary": str(source_candidate.get("rationale") or issue_type),
                "target_nodes": target_nodes,
                "affected_nets": target_nodes,
                "target_pins": evidence.get("support_pin_ids", []),
                "geometry": {
                    "bbox": evidence.get("bbox"),
                    "points": evidence.get("points"),
                    "distance": evidence.get("distance"),
                },
                "before_metrics": before,
                "after_metrics": before,
                "validation": validation,
                "improved_metrics": validation["improved_metrics"],
                "regressed_metrics": validation["regressed_metrics"],
                "validation_result": validation["validation_result"],
                "blocking_issues": validation["blocking_issues"],
                "score": score,
                "risk_level": risk,
                "reasons": reasons,
                "evidence": {
                    "source_repair_candidate": source_candidate,
                },
                "mutates_topology": False,
            }
        )
        candidate_index += 1

    return candidates[:EVIDENCE_REVIEW_MAX_CANDIDATES]


def _select_repair_tools(agent_audit: dict[str, Any] | None) -> list[dict[str, str]]:
    if not agent_audit:
        return []
    if agent_audit.get("primary_issue") == "insufficient_artifacts":
        return []

    selected: list[dict[str, str]] = []
    issue = str(agent_audit.get("primary_issue", ""))
    stage = str(agent_audit.get("suspected_stage", ""))
    semantic = agent_audit.get("topology_semantic_audit", {})
    diagnoses = agent_audit.get("stage_diagnoses", [])
    actions = agent_audit.get("recommended_actions", [])
    action_types = {str(action.get("action_type")) for action in actions}
    diagnosis_types = {str(item.get("issue_type")) for item in diagnoses}

    if (
        issue == "split_real_connection_or_missing_bridge"
        or stage == "graph_node_merge"
        or semantic.get("single_pin_nets")
        or "prepare_merge_repair_dry_run" in action_types
    ):
        selected.append(
            _tool(
                "merge_nodes_dry_run",
                "Audit points to split nodes, single-pin nets, or missing bridge support.",
            )
        )

    if (
        "low_confidence_terminal_match" in diagnosis_types
        or "weak_confidence_terminal_match" in diagnosis_types
        or "inspect_terminal_attachments" in action_types
        or "spot_check_terminal_attachments" in action_types
    ):
        selected.append(
            _tool(
                "reattach_pin_dry_run",
                "Audit includes low/weak confidence terminal attachment evidence.",
            )
        )

    if (
        "high_unsupported_evidence_ratio" in diagnosis_types
        or "review_unsupported_evidence" in action_types
    ):
        selected.append(
            _tool(
                "evidence_review_dry_run",
                "Audit asks to inspect unsupported evidence or possible bridge support.",
            )
        )

    return selected


def _missing_required_artifacts(debug_dir: Path) -> list[str]:
    return [file_name for file_name in REQUIRED_DEBUG_ARTIFACTS if not (debug_dir / file_name).exists()]


def _recommended_next_step(
    agent_audit: dict[str, Any] | None,
    selected_tools: list[dict[str, str]],
    missing_required_artifacts: list[str],
) -> str:
    if missing_required_artifacts:
        return "regenerate_full_debug_run"
    if not agent_audit:
        return "run_agent_audit_first"
    if agent_audit.get("primary_issue") == "insufficient_artifacts":
        return "regenerate_full_debug_run"
    if selected_tools:
        return "implement_or_run_selected_dry_run_tools"
    return "no_actionable_candidate"


def render_repair_dry_run_markdown(report: dict[str, Any]) -> str:
    source = report.get("source_agent_audit_report", {})
    validation = report.get("validation_summary", {})
    selected_tools = report.get("selected_repair_tools", [])
    candidates = report.get("repair_candidates", [])

    lines = [
        f"# Agent Repair Dry-run Report: {report.get('case_id')}",
        "",
        "## 结论",
        "",
        f"- schema：`{report.get('schema_version')}`",
        f"- dry-run only：`{report.get('dry_run_only')}`",
        f"- topology mutated：`{report.get('topology_mutated')}`",
        f"- debug artifacts ok：`{report.get('debug_artifacts_ok')}`",
        f"- source audit：`{source.get('path')}`",
        f"- audit status：`{source.get('overall_status')}`",
        f"- audit issue：`{source.get('primary_issue')}`",
        f"- recommended next step：`{report.get('recommended_next_step')}`",
        "",
        "## 选中的 Repair Tools",
        "",
    ]

    if selected_tools:
        for item in selected_tools:
            lines.append(
                f"- `{item.get('tool_id')}` ({item.get('status')}): {item.get('reason')}"
            )
    else:
        lines.append("- 无。")

    if report.get("missing_required_artifacts"):
        lines.extend(
            [
                "",
                "## 缺失的核心 Artifacts",
                "",
            ]
        )
        for file_name in report.get("missing_required_artifacts", []):
            lines.append(f"- `{file_name}`")

    lines.extend(["", "## Repair Candidates", ""])
    if candidates:
        for candidate in candidates:
            lines.append(
                f"- `{candidate.get('candidate_id')}` / `{candidate.get('repair_type')}` "
                f"rank=`{candidate.get('rank')}` ranking_score=`{candidate.get('ranking_score')}` "
                f"tool_score=`{candidate.get('score')}` "
                f"risk=`{candidate.get('risk_level')}` validation=`{candidate.get('validation_result')}`: "
                f"{candidate.get('summary')}"
            )
            before = candidate.get("before_metrics", {})
            after = candidate.get("after_metrics", {})
            lines.append(
                "  - single_pin_net_count: "
                f"`{before.get('single_pin_net_count')}` -> `{after.get('single_pin_net_count')}`"
            )
            lines.append(
                "  - zero/unmatched/self-short/source-short: "
                f"`{before.get('zero_pin_net_count')}` -> `{after.get('zero_pin_net_count')}`, "
                f"`{before.get('unmatched_pin_count')}` -> `{after.get('unmatched_pin_count')}`, "
                f"`{before.get('component_self_short_count')}` -> `{after.get('component_self_short_count')}`, "
                f"`{before.get('source_terminal_short_count')}` -> `{after.get('source_terminal_short_count')}`"
            )
            lines.append(
                "  - connected_component_count: "
                f"`{before.get('connected_component_count')}` -> `{after.get('connected_component_count')}`"
            )
            lines.append(
                "  - target_nodes: "
                f"`{candidate.get('target_nodes')}`; target_pins: `{candidate.get('target_pins')}`"
            )
            validator_version = candidate.get("validation", {}).get("validator_version")
            if validator_version:
                lines.append(f"  - validator: `{validator_version}`")
            ranker_version = candidate.get("ranking", {}).get("ranker_version")
            if ranker_version:
                lines.append(
                    f"  - ranker: `{ranker_version}`; recommendation: "
                    f"`{candidate.get('recommendation')}`"
                )
            for reason in candidate.get("ranking_reasons", [])[:3]:
                lines.append(f"  - ranking: {reason}")
            if candidate.get("blocking_issues"):
                lines.append(f"  - blocking_issues: `{candidate.get('blocking_issues')}`")
            if candidate.get("validation", {}).get("non_blocking_warnings"):
                lines.append(
                    f"  - non_blocking_warnings: "
                    f"`{candidate.get('validation', {}).get('non_blocking_warnings')}`"
                )
            for reason in candidate.get("reasons", [])[:4]:
                lines.append(f"  - {reason}")
    else:
        lines.append("- No actionable candidate generated.")

    lines.extend(
        [
            "",
            "## Validation Summary",
            "",
            f"- candidate_count：`{validation.get('candidate_count')}`",
            f"- viable_candidate_count：`{validation.get('viable_candidate_count')}`",
            f"- blocked_candidate_count：`{validation.get('blocked_candidate_count')}`",
            f"- topology_mutated：`{validation.get('topology_mutated')}`",
            f"- protected_output_count：`{validation.get('protected_output_count')}`",
            "",
            "## Protected Outputs",
            "",
        ]
    )
    for item in report.get("protected_outputs", []):
        lines.append(
            f"- `{Path(item.get('path', '')).name}` exists=`{item.get('exists')}` "
            f"sha256=`{item.get('sha256')}`"
        )
    return "\n".join(lines)


def run_agent_repair_dry_run(
    debug_dir: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Create the repair dry-run report without mutating topology."""
    base_dir = Path(debug_dir)
    out_dir = Path(output_dir) if output_dir else base_dir
    source_audit_path = base_dir / "agent_audit_report.json"
    agent_audit = _read_json(source_audit_path)
    missing_required = _missing_required_artifacts(base_dir)
    selected_tools = [] if missing_required else _select_repair_tools(agent_audit)
    protected_outputs = [_file_digest(base_dir / file_name) for file_name in PROTECTED_OUTPUT_FILES]
    repair_candidates: list[dict[str, Any]] = []
    selected_tool_ids = {tool["tool_id"] for tool in selected_tools}
    if not missing_required and "merge_nodes_dry_run" in selected_tool_ids:
        repair_candidates.extend(_generate_merge_node_candidates(base_dir, agent_audit))
    if not missing_required and "reattach_pin_dry_run" in selected_tool_ids:
        repair_candidates.extend(_generate_reattach_pin_candidates(base_dir, agent_audit))
    if not missing_required and "evidence_review_dry_run" in selected_tool_ids:
        repair_candidates.extend(_generate_evidence_review_candidates(base_dir, agent_audit))
    repair_candidates = rank_repair_candidates(repair_candidates)[:REPAIR_DRY_RUN_MAX_CANDIDATES]
    viable_count = sum(1 for candidate in repair_candidates if candidate.get("validation_result") == "viable")
    blocked_count = sum(1 for candidate in repair_candidates if candidate.get("validation_result") == "blocked")
    reviewable_count = sum(
        1 for candidate in repair_candidates if candidate.get("validation_result") == "reviewable"
    )

    report = {
        "schema_version": "3.1-step5-repair-dry-run",
        "case_id": agent_audit.get("case_id") if agent_audit else base_dir.name,
        "debug_dir": str(base_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run_only": True,
        "topology_mutated": False,
        "debug_artifacts_ok": not missing_required,
        "missing_required_artifacts": missing_required,
        "source_agent_audit_report": {
            "path": str(source_audit_path),
            "exists": agent_audit is not None,
            "overall_status": agent_audit.get("overall_status") if agent_audit else None,
            "primary_issue": agent_audit.get("primary_issue") if agent_audit else None,
            "suspected_stage": agent_audit.get("suspected_stage") if agent_audit else None,
        },
        "selected_repair_tools": selected_tools,
        "repair_candidates": repair_candidates,
        "validation_summary": {
            "candidate_count": len(repair_candidates),
            "viable_candidate_count": viable_count,
            "reviewable_candidate_count": reviewable_count,
            "blocked_candidate_count": blocked_count,
            "validator_version": VALIDATOR_VERSION,
            "ranker_version": RANKER_VERSION,
            "topology_mutated": False,
            "protected_output_count": len(protected_outputs),
            "notes": [
                "Repair candidates are dry-run only and do not mutate topology.json.",
                "Dry-run tools implement merge_nodes_dry_run, reattach_pin_dry_run, evidence_review_dry_run, validation, and ranking.",
            ],
        },
        "recommended_next_step": _recommended_next_step(agent_audit, selected_tools, missing_required),
        "protected_outputs": protected_outputs,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "agent_repair_dry_run.json"
    md_path = out_dir / "agent_repair_dry_run.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_repair_dry_run_markdown(report), encoding="utf-8")
    return {
        "report": report,
        "outputs": {
            "json": str(json_path),
            "markdown": str(md_path),
        },
    }
