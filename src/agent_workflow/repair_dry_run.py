"""Skeleton dry-run repair report for agent-guided repair workflows."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_default_config
from src.agent_workflow.candidate_validator import (
    VALIDATOR_VERSION,
    validate_topology_candidate,
)
from src.agent_workflow.candidate_ranker import RANKER_VERSION, rank_repair_candidates
from src.topology.terminal_attachment import build_terminal_attachments
from src.topology.symbol_library import get_symbol_definition


PROTECTED_OUTPUT_FILES = ("topology.json", "netlist.json", "14_export.dxf")
MERGE_NODE_MAX_BBOX_GAP = 80.0
MERGE_NODE_MAX_CANDIDATES = 8
REATTACH_PIN_MAX_CANDIDATES = 8
AXIS_FLIP_MAX_CANDIDATES = 8
CLASS_OVERRIDE_MAX_CANDIDATES = 8
EVIDENCE_REVIEW_MAX_CANDIDATES = 8
GAP_BRIDGE_MERGE_MAX_CANDIDATES = 8
SINGLE_PIN_STUB_BRIDGE_MAX_CANDIDATES = 8
SINGLE_PIN_STUB_BRIDGE_MAX_BBOX_GAP = 90.0
SINGLE_PIN_PAIR_BRIDGE_MAX_BBOX_GAP = 145.0
AXIS_FLIP_NEAREST_NODE_MAX_DISTANCE = 150.0
REPAIR_DRY_RUN_MAX_CANDIDATES = 16
REATTACH_PIN_MIN_ATTACHMENT_SCORE = 0.55
REATTACH_PIN_WEAK_CONFIDENCE = 0.75
STUB_BRIDGE_MIN_ATTACHMENT_SCORE = 0.55
GRANULAR_DRY_RUN_TOOLS = {
    "dry_run_merge_nodes",
    "dry_run_component_class_override",
    "dry_run_component_axis_flip",
    "dry_run_reattach_pin",
    "dry_run_gap_bridge_merge",
    "dry_run_single_pin_stub_bridge",
}
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
    class_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    merge_nodes = merge_nodes or []
    pin_node_overrides = pin_node_overrides or {}
    class_overrides = class_overrides or {}
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
    for component_id, class_name in class_overrides.items():
        component_classes[str(component_id)] = str(class_name).lower()
    all_pins = _all_pin_ids(topology)
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

    for pin_id, node_id in pin_node_overrides.items():
        pin_id = str(pin_id)
        if pin_id in connected_pin_ids or pin_id not in all_pins:
            continue
        component_id = str(pin_to_component.get(pin_id, ""))
        if not component_id:
            continue
        parent.setdefault(str(node_id), str(node_id))
        merged_node_id = _find(parent, str(node_id))
        connected_pin_ids.add(pin_id)
        net_pins.setdefault(merged_node_id, []).append(
            {
                "pin_id": pin_id,
                "component_id": component_id,
            }
        )

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
        "power_source_count": sum(1 for class_name in component_classes.values() if _is_power_class(class_name)),
        "has_power_source": any(_is_power_class(class_name) for class_name in component_classes.values()),
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


def _point_to_bbox_gap(point: tuple[float, float], bbox: list[float] | None) -> dict[str, float | bool]:
    if not bbox or len(bbox) < 4:
        return {"dx": 999999.0, "dy": 999999.0, "distance": 999999.0, "axis_aligned": False}
    x, y = point
    x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
    dx = max(x1 - x, x - x2, 0.0)
    dy = max(y1 - y, y - y2, 0.0)
    distance = math.hypot(dx, dy)
    return {"dx": dx, "dy": dy, "distance": distance, "axis_aligned": dx <= 8.0 or dy <= 8.0}


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
    target_pin_count: int,
    agent_audit: dict[str, Any] | None,
) -> float:
    improvement = before["single_pin_net_count"] - after["single_pin_net_count"]
    score = 0.2
    if both_single_pin:
        score += 0.25
    elif target_pin_count >= 2:
        score += 0.16
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
    if not single_pin_node_ids:
        return []

    before = _metrics_for_merge(topology)
    candidates: list[dict[str, Any]] = []
    candidate_index = 1
    for left_id in single_pin_node_ids:
        for right_id, right_node in nodes.items():
            if right_id == left_id:
                continue
            left_node = nodes[left_id]
            if set(left_node.get("component_ids", []) or []).intersection(right_node.get("component_ids", []) or []):
                continue
            gap = _bbox_gap(left_node.get("bbox"), right_node.get("bbox"))
            if float(gap["distance"]) > MERGE_NODE_MAX_BBOX_GAP:
                continue
            after = _metrics_for_merge(topology, [left_id, right_id])
            validation = validate_topology_candidate("merge_nodes", before, after)
            blocking = validation["blocking_issues"]
            target_pin_count = _node_pin_count(right_node)
            both_single_pin = _node_pin_count(left_node) == 1 and target_pin_count == 1
            score = _score_merge_candidate(before, after, gap, both_single_pin, target_pin_count, agent_audit)
            improved = validation["improved_metrics"]
            regressed = validation["regressed_metrics"]
            validation_result = validation["validation_result"]
            risk = _risk_level(score, blocking, float(gap["distance"]))
            target_pins = sorted(set(left_node.get("pin_ids", []) + right_node.get("pin_ids", [])))
            reasons = [
                "Source node is a single-pin net.",
                f"Target node has {target_pin_count} pin(s).",
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


def _component_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(component.get("id")): component for component in topology.get("components", [])}


def _pin_group_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(group.get("component_id")): group
        for group in topology.get("pins", [])
        if group.get("component_id")
    }


def _pin_ids_for_component(topology: dict[str, Any], component_id: str) -> list[str]:
    for group in topology.get("pins", []):
        if str(group.get("component_id")) != component_id:
            continue
        return [
            str(pin.get("pin_id"))
            for pin in group.get("pins", [])
            if pin.get("pin_id")
        ]
    return []


def _node_ids_for_component(topology: dict[str, Any], component_id: str) -> list[str]:
    return sorted(
        {
            str(connection.get("node_id"))
            for connection in topology.get("connections", [])
            if str(connection.get("component_id")) == component_id and connection.get("node_id")
        }
    )


def _symbol_pin_count(class_name: str) -> int:
    return int(get_symbol_definition(class_name).get("pin_count", 0) or 0)


def _candidate_class_score(component: dict[str, Any], class_name: str) -> float:
    for candidate in component.get("class_candidates", []):
        if str(candidate.get("class_name")) == class_name:
            return float(candidate.get("score", 0.0) or 0.0)
    for candidate in component.get("class_alternatives", []):
        if str(candidate.get("class_name")) == class_name:
            return float(candidate.get("score", 0.0) or 0.0)
    return 0.0


def _component_class_source_candidates(debug_dir: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(debug_dir / "repair_candidates.json") or {}
    result: dict[str, dict[str, Any]] = {}
    for candidate in payload.get("candidates", []) or []:
        if str(candidate.get("issue_type")) != "component_class_ambiguity":
            continue
        component_id = str(candidate.get("refs", {}).get("component_id", ""))
        if component_id:
            result[component_id] = candidate
    return result


def _score_component_class_override(
    before: dict[str, Any],
    after: dict[str, Any],
    component: dict[str, Any],
    alternative: dict[str, Any],
    source_candidate: dict[str, Any] | None,
    agent_audit: dict[str, Any] | None,
) -> float:
    score = 0.26
    alt_class = str(alternative.get("class_name", ""))
    alt_score = float(alternative.get("score", 0.0) or 0.0)
    selected_score = _candidate_class_score(component, str(component.get("class_name", "")))
    if alt_score > 0:
        score += min(0.18, alt_score * 0.22)
    if selected_score > 0 and alt_score >= selected_score * 0.35:
        score += 0.08
    if _is_power_class(alt_class) and not before.get("has_power_source"):
        score += 0.28
    if after.get("power_source_count", 0) > before.get("power_source_count", 0):
        score += 0.12
    if source_candidate and source_candidate.get("severity") == "warning":
        score += 0.06
    if agent_audit:
        semantic = agent_audit.get("topology_semantic_audit", {})
        evidence_codes = {str(item.get("code")) for item in agent_audit.get("evidence", [])}
        action_types = {str(action.get("action_type")) for action in agent_audit.get("recommended_actions", [])}
        if semantic.get("circuit_completeness") == "passive_or_missing_power_source":
            score += 0.08
        if "missing_power_source" in evidence_codes or "confirm_missing_power_source" in action_types:
            score += 0.08
    return round(min(score, 0.95), 3)


def _generate_component_class_override_candidates(
    debug_dir: Path,
    agent_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    topology = _read_json(debug_dir / "topology.json") or {}
    before = _metrics_for_merge(topology)
    source_candidates = _component_class_source_candidates(debug_dir)
    candidates: list[dict[str, Any]] = []
    candidate_index = 1

    for component in topology.get("components", []):
        component_id = str(component.get("id", ""))
        current_class = str(component.get("class_name", "unknown"))
        current_pin_count = _symbol_pin_count(current_class)
        for alternative in component.get("class_alternatives", []) or []:
            alternate_class = str(alternative.get("class_name", "unknown"))
            if alternate_class == current_class:
                continue
            alternate_pin_count = _symbol_pin_count(alternate_class)
            if alternate_pin_count != current_pin_count:
                continue

            after = _metrics_for_merge(
                topology,
                class_overrides={component_id: alternate_class},
            )
            validation = validate_topology_candidate("component_class_override", before, after)
            source_candidate = source_candidates.get(component_id)
            score = _score_component_class_override(
                before,
                after,
                component,
                alternative,
                source_candidate,
                agent_audit,
            )
            risk = _risk_level_for_validation(validation, score)
            if alternate_pin_count == 0:
                risk = "high"
            reasons = [
                f"Current class is {current_class}; alternative class is {alternate_class}.",
                f"Both classes have compatible pin_count={current_pin_count}.",
                f"Selected score is {round(_candidate_class_score(component, current_class), 3)}; alternative score is {round(float(alternative.get('score', 0.0) or 0.0), 3)}.",
                "Dry-run does not change terminal geometry or node topology.",
            ]
            if _is_power_class(alternate_class) and not before.get("has_power_source"):
                reasons.append("The current recovered topology has no power-source component.")

            candidates.append(
                {
                    "candidate_id": f"CLS{candidate_index}",
                    "repair_type": "component_class_override",
                    "summary": (
                        f"Dry-run override {component_id} class from {current_class} to {alternate_class}."
                    ),
                    "target_nodes": _node_ids_for_component(topology, component_id),
                    "affected_nets": _node_ids_for_component(topology, component_id),
                    "target_pins": _pin_ids_for_component(topology, component_id),
                    "target_component_id": component_id,
                    "geometry": {
                        "component_id": component_id,
                        "bbox": component.get("bbox"),
                        "current_class_name": current_class,
                        "alternate_class_name": alternate_class,
                        "current_pin_count": current_pin_count,
                        "alternate_pin_count": alternate_pin_count,
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
                        "selected_class": {
                            "class_name": current_class,
                            "score": _candidate_class_score(component, current_class),
                        },
                        "alternative_class": alternative,
                        "class_candidates": component.get("class_candidates", []),
                        "source_repair_candidate": source_candidate,
                    },
                    "mutates_topology": False,
                }
            )
            candidate_index += 1

    return candidates[:CLASS_OVERRIDE_MAX_CANDIDATES]


def _pin_group_for_axis(component: dict[str, Any], current_group: dict[str, Any], axis: str) -> dict[str, Any] | None:
    bbox = component.get("bbox")
    current_pins = current_group.get("pins", [])
    if not bbox or len(bbox) < 4 or len(current_pins) != 2:
        return None
    x1, y1, x2, y2 = [int(value) for value in bbox[:4]]
    cx = int(round((x1 + x2) / 2))
    cy = int(round((y1 + y2) / 2))
    pin_ids = [str(pin.get("pin_id")) for pin in current_pins]
    component_id = str(component.get("id"))
    if axis == "vertical":
        pins = [
            {
                "pin_id": pin_ids[0],
                "component_id": component_id,
                "x": cx,
                "y": y1,
                "side": "top",
                "terminal_role": "top",
                "axis": "vertical",
                "axis_source": "agent_axis_flip",
                "confidence": 0.5,
            },
            {
                "pin_id": pin_ids[1],
                "component_id": component_id,
                "x": cx,
                "y": y2,
                "side": "bottom",
                "terminal_role": "bottom",
                "axis": "vertical",
                "axis_source": "agent_axis_flip",
                "confidence": 0.5,
            },
        ]
    else:
        pins = [
            {
                "pin_id": pin_ids[0],
                "component_id": component_id,
                "x": x1,
                "y": cy,
                "side": "left",
                "terminal_role": "left",
                "axis": "horizontal",
                "axis_source": "agent_axis_flip",
                "confidence": 0.5,
            },
            {
                "pin_id": pin_ids[1],
                "component_id": component_id,
                "x": x2,
                "y": cy,
                "side": "right",
                "terminal_role": "right",
                "axis": "horizontal",
                "axis_source": "agent_axis_flip",
                "confidence": 0.5,
            },
        ]
    return {
        "component_id": component_id,
        "pin_count": 2,
        "axis": axis,
        "axis_source": "agent_axis_flip",
        "confidence": 0.5,
        "pins": pins,
    }


def _score_axis_group(
    pin_group: dict[str, Any],
    evidence_graph: dict[str, Any],
    raw_to_node: dict[str, str],
) -> dict[str, Any]:
    attachment_result = build_terminal_attachments(
        {"pins": [pin_group]},
        evidence_graph,
        get_default_config(),
    )
    overrides: dict[str, str] = {}
    attached_scores = []
    attachments = attachment_result.get("attachments", [])
    for attachment in attachments:
        score = float(attachment.get("best_attachment_score", 0.0) or 0.0)
        raw_component_id = attachment.get("best_raw_component_id")
        if raw_component_id and str(raw_component_id) in raw_to_node:
            overrides[str(attachment.get("pin_id"))] = raw_to_node[str(raw_component_id)]
            attached_scores.append(score)
    best_scores = [
        float(attachment.get("best_attachment_score", 0.0) or 0.0)
        for attachment in attachments
    ]
    return {
        "axis": pin_group.get("axis"),
        "attached_pin_count": len(overrides),
        "attachment_score_sum": round(float(sum(best_scores)), 3),
        "attached_score_sum": round(float(sum(attached_scores)), 3),
        "min_best_attachment_score": round(float(min(best_scores)), 3) if best_scores else 0.0,
        "pin_node_overrides": overrides,
        "attachments": attachments,
    }


def _score_axis_flip_candidate(
    before: dict[str, Any],
    after: dict[str, Any],
    current_score: dict[str, Any],
    alternate_score: dict[str, Any],
    current_axis_source: str,
    agent_audit: dict[str, Any] | None,
) -> float:
    score = 0.2
    attached_delta = int(alternate_score.get("attached_pin_count", 0)) - int(current_score.get("attached_pin_count", 0))
    attachment_delta = float(alternate_score.get("attached_score_sum", 0.0)) - float(
        current_score.get("attached_score_sum", 0.0)
    )
    unmatched_delta = before["unmatched_pin_count"] - after["unmatched_pin_count"]
    if current_axis_source == "fallback":
        score += 0.15
    if attached_delta > 0:
        score += min(0.28, attached_delta * 0.14)
    if attachment_delta > 0:
        score += min(0.2, attachment_delta * 0.12)
    if unmatched_delta > 0:
        score += min(0.24, unmatched_delta * 0.12)
    if agent_audit and agent_audit.get("suspected_stage") in {"terminal_matching", "pin_location"}:
        score += 0.08
    return round(min(score, 0.95), 3)


def _nearest_node_overrides_for_pin_group(
    pin_group: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    component_id: str,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    overrides: dict[str, str] = {}
    evidence: list[dict[str, Any]] = []
    for pin in pin_group.get("pins", []) or []:
        pin_id = str(pin.get("pin_id") or "")
        if not pin_id:
            continue
        point = (float(pin.get("x", 0.0) or 0.0), float(pin.get("y", 0.0) or 0.0))
        ranked = []
        for node_id, node in nodes.items():
            if component_id in {str(item) for item in node.get("component_ids", []) or []}:
                continue
            gap = _point_to_bbox_gap(point, node.get("bbox"))
            distance = float(gap.get("distance", 999999.0) or 999999.0)
            if distance > AXIS_FLIP_NEAREST_NODE_MAX_DISTANCE:
                continue
            if not gap.get("axis_aligned") and distance > 80:
                continue
            ranked.append((distance, node_id, node, gap))
        ranked.sort(key=lambda item: item[0])
        if not ranked:
            continue
        distance, node_id, node, gap = ranked[0]
        overrides[pin_id] = node_id
        evidence.append(
            {
                "pin_id": pin_id,
                "target_node_id": node_id,
                "distance": round(distance, 3),
                "bbox_gap": gap,
                "target_pin_ids": node.get("pin_ids", []),
                "target_component_ids": node.get("component_ids", []),
                "source": "nearest_node_after_axis_flip",
            }
        )
    return overrides, evidence


def _generate_axis_flip_candidates(
    debug_dir: Path,
    agent_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    topology = _read_json(debug_dir / "topology.json") or {}
    evidence_graph = _read_json(debug_dir / "evidence_graph.json") or {}
    nodes_payload = _read_json(debug_dir / "nodes.json") or {}
    raw_to_node = _raw_component_to_node_lookup(topology, nodes_payload)
    nodes = _node_lookup(nodes_payload, topology)
    components = _component_lookup(topology)
    pin_groups = _pin_group_lookup(topology)
    before = _metrics_for_merge(topology)
    candidates: list[dict[str, Any]] = []
    candidate_index = 1

    for component_id, current_group in pin_groups.items():
        component = components.get(component_id)
        if not component or int(current_group.get("pin_count", 0) or 0) != 2:
            continue
        current_axis = str(current_group.get("axis") or "horizontal")
        if current_axis not in {"horizontal", "vertical"}:
            continue
        current_pin_ids = [str(pin.get("pin_id")) for pin in current_group.get("pins", []) if pin.get("pin_id")]
        current_has_unmatched_pin = bool(set(current_pin_ids).intersection(before.get("unmatched_pin_ids", [])))
        alternate_axis = "vertical" if current_axis == "horizontal" else "horizontal"
        alternate_group = _pin_group_for_axis(component, current_group, alternate_axis)
        if not alternate_group:
            continue
        current_score = _score_axis_group(current_group, evidence_graph, raw_to_node)
        alternate_score = _score_axis_group(alternate_group, evidence_graph, raw_to_node)
        pin_node_overrides = dict(alternate_score["pin_node_overrides"])
        nearest_evidence: list[dict[str, Any]] = []
        if not pin_node_overrides:
            pin_node_overrides, nearest_evidence = _nearest_node_overrides_for_pin_group(
                alternate_group,
                nodes,
                component_id,
            )
            if nearest_evidence and not current_has_unmatched_pin and current_group.get("axis_source") != "fallback":
                pin_node_overrides = {}
                nearest_evidence = []
        if not pin_node_overrides:
            continue
        if (
            alternate_score["attached_pin_count"] <= current_score["attached_pin_count"]
            and alternate_score["attached_score_sum"] <= current_score["attached_score_sum"] + 0.25
            and before["unmatched_pin_count"] == 0
            and not nearest_evidence
        ):
            continue

        after = _metrics_for_merge(topology, pin_node_overrides=pin_node_overrides)
        validation = validate_topology_candidate("component_pin_axis_flip", before, after)
        score = _score_axis_flip_candidate(
            before,
            after,
            current_score,
            alternate_score,
            str(current_group.get("axis_source", "")),
            agent_audit,
        )
        if nearest_evidence:
            score = round(min(0.95, score + 0.08), 3)
        risk = _risk_level_for_validation(validation, score)
        target_pins = current_pin_ids
        candidates.append(
            {
                "candidate_id": f"AXF{candidate_index}",
                "repair_type": "component_pin_axis_flip",
                "summary": (
                    f"Dry-run flip {component_id} pins from {current_axis} to {alternate_axis}."
                ),
                "affected_nets": sorted(set(pin_node_overrides.values())),
                "target_nodes": sorted(set(pin_node_overrides.values())),
                "target_pins": target_pins,
                "target_component_id": component_id,
                "geometry": {
                    "bbox": component.get("bbox"),
                    "current_axis": current_axis,
                    "alternate_axis": alternate_axis,
                    "replacement_pin_group": alternate_group,
                    "pin_node_overrides": pin_node_overrides,
                    "nearest_node_evidence": nearest_evidence,
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
                "reasons": [
                    f"Current axis is {current_axis} from {current_group.get('axis_source')}.",
                    f"Alternate axis is {alternate_axis}.",
                    f"Current attached pins: {current_score['attached_pin_count']}; alternate attached pins: {alternate_score['attached_pin_count']}.",
                    f"Current attachment score sum: {current_score['attached_score_sum']}; alternate: {alternate_score['attached_score_sum']}.",
                    f"Unmatched pin count changes {before['unmatched_pin_count']} -> {after['unmatched_pin_count']}.",
                ],
                "evidence": {
                    "current_axis_score": current_score,
                    "alternate_axis_score": alternate_score,
                    "nearest_node_after_axis_flip": nearest_evidence,
                },
                "mutates_topology": False,
            }
        )
        candidate_index += 1

    return candidates[:AXIS_FLIP_MAX_CANDIDATES]


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


def _score_gap_bridge_merge_candidate(
    before: dict[str, Any],
    after: dict[str, Any],
    distance: float,
    support_status: str,
    agent_audit: dict[str, Any] | None,
) -> float:
    score = 0.24
    if support_status in {"between_best_supported_components", "between_supported_components"}:
        score += 0.2
    elif support_status == "between_path_supported_components":
        score += 0.15
    elif support_status == "one_sided_supported":
        score += 0.06
    if distance <= 12:
        score += 0.14
    elif distance <= 32:
        score += 0.1
    single_pin_delta = before["single_pin_net_count"] - after["single_pin_net_count"]
    unmatched_delta = before["unmatched_pin_count"] - after["unmatched_pin_count"]
    if single_pin_delta > 0:
        score += min(0.22, single_pin_delta * 0.11)
    if unmatched_delta > 0:
        score += min(0.18, unmatched_delta * 0.12)
    if agent_audit and agent_audit.get("primary_issue") == "split_real_connection_or_missing_bridge":
        score += 0.08
    return round(min(score, 0.95), 3)


def _generate_gap_bridge_merge_candidates(
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
    seen_node_pairs: set[tuple[str, str]] = set()

    for source_candidate in source_payload.get("candidates", []) or []:
        if str(source_candidate.get("issue_type", "")) != "possible_gap_bridge":
            continue
        refs = source_candidate.get("refs", {})
        evidence = source_candidate.get("evidence", {})
        source_raw_ids = [
            refs.get("from_component_id"),
            refs.get("to_component_id"),
        ]
        target_nodes = sorted(
            {
                raw_to_node[str(raw_component_id)]
                for raw_component_id in source_raw_ids
                if raw_component_id and str(raw_component_id) in raw_to_node
            }
        )
        if len(target_nodes) != 2:
            continue
        node_pair = tuple(target_nodes)
        if node_pair in seen_node_pairs:
            continue
        seen_node_pairs.add(node_pair)

        after = _metrics_for_merge(topology, target_nodes)
        validation = validate_topology_candidate("gap_bridge_merge", before, after)
        distance = float(evidence.get("distance", 999999.0) or 999999.0)
        support_status = str(evidence.get("support_status", ""))
        score = _score_gap_bridge_merge_candidate(before, after, distance, support_status, agent_audit)
        risk = _risk_level_for_validation(validation, score)
        source_id = source_candidate.get("repair_candidate_id")
        reasons = [
            f"Imported from possible_gap_bridge {source_id}.",
            f"Bridge maps raw components {source_raw_ids} to nodes {target_nodes}.",
            f"Bridge support status is {support_status or 'unknown'}.",
            f"Bridge distance is {round(distance, 3)} px.",
        ]
        candidates.append(
            {
                "candidate_id": f"GBR{candidate_index}",
                "repair_type": "gap_bridge_merge",
                "summary": f"Dry-run bridge merge {target_nodes[0]} + {target_nodes[1]}.",
                "target_nodes": target_nodes,
                "affected_nets": target_nodes,
                "target_pins": evidence.get("support_pin_ids", []),
                "geometry": {
                    "bridge_candidate_id": refs.get("bridge_candidate_id"),
                    "from_component_id": refs.get("from_component_id"),
                    "to_component_id": refs.get("to_component_id"),
                    "points": evidence.get("points"),
                    "point": evidence.get("point"),
                    "projected_point": evidence.get("projected_point"),
                    "distance": evidence.get("distance"),
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
                    "source_repair_candidate": source_candidate,
                },
                "mutates_topology": False,
            }
        )
        candidate_index += 1

    return candidates[:GAP_BRIDGE_MERGE_MAX_CANDIDATES]


def _attachment_lookup(terminal_attachments: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(attachment.get("pin_id")): attachment
        for attachment in terminal_attachments.get("attachments", []) or []
        if attachment.get("pin_id")
    }


def _node_pin_ids(node: dict[str, Any]) -> list[str]:
    return [str(item) for item in node.get("pin_ids", []) or [] if item]


def _node_pin_count(node: dict[str, Any]) -> int:
    return len(set(_node_pin_ids(node)))


def _score_single_pin_stub_bridge_candidate(
    before: dict[str, Any],
    after: dict[str, Any],
    gap: dict[str, float | bool],
    attachment_score: float,
    target_pin_count: int,
    agent_audit: dict[str, Any] | None,
) -> float:
    score = 0.18
    if attachment_score >= 0.9:
        score += 0.22
    elif attachment_score >= 0.75:
        score += 0.16
    elif attachment_score >= STUB_BRIDGE_MIN_ATTACHMENT_SCORE:
        score += 0.1
    distance = float(gap.get("distance", 999999.0) or 999999.0)
    if distance <= 16:
        score += 0.18
    elif distance <= 40:
        score += 0.12
    elif distance <= SINGLE_PIN_STUB_BRIDGE_MAX_BBOX_GAP:
        score += 0.06
    if gap.get("axis_aligned"):
        score += 0.12
    if target_pin_count >= 2:
        score += 0.12
    single_pin_delta = before["single_pin_net_count"] - after["single_pin_net_count"]
    if single_pin_delta > 0:
        score += min(0.24, single_pin_delta * 0.12)
    if agent_audit and agent_audit.get("primary_issue") == "split_real_connection_or_missing_bridge":
        score += 0.08
    return round(min(score, 0.95), 3)


def _generate_single_pin_stub_bridge_candidates(
    debug_dir: Path,
    agent_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    topology = _read_json(debug_dir / "topology.json") or {}
    nodes_payload = _read_json(debug_dir / "nodes.json") or {}
    terminal_attachments = _read_json(debug_dir / "terminal_attachments.json") or {}
    nodes = _node_lookup(nodes_payload, topology)
    attachments_by_pin = _attachment_lookup(terminal_attachments)
    audit_single_pin_nodes = set(_single_pin_node_ids(agent_audit, topology))
    before = _metrics_for_merge(topology)
    candidates: list[dict[str, Any]] = []
    candidate_index = 1
    seen_pairs: set[tuple[str, str]] = set()

    single_pin_nodes = [
        (node_id, node)
        for node_id, node in nodes.items()
        if _node_pin_count(node) == 1 and (not audit_single_pin_nodes or node_id in audit_single_pin_nodes)
    ]
    if not single_pin_nodes and audit_single_pin_nodes:
        single_pin_nodes = [
            (node_id, node)
            for node_id, node in nodes.items()
            if node_id in audit_single_pin_nodes and _node_pin_count(node) == 1
        ]

    target_nodes = [
        (node_id, node)
        for node_id, node in nodes.items()
        if _node_pin_count(node) >= 2
    ]

    for source_id, source_node in single_pin_nodes:
        pin_id = _node_pin_ids(source_node)[0]
        attachment = attachments_by_pin.get(pin_id, {})
        attachment_score = float(attachment.get("best_attachment_score", 0.0) or 0.0)
        if attachment_score < STUB_BRIDGE_MIN_ATTACHMENT_SCORE:
            continue
        for target_id, target_node in target_nodes:
            if target_id == source_id:
                continue
            pair = tuple(sorted([source_id, target_id]))
            if pair in seen_pairs:
                continue
            gap = _bbox_gap(source_node.get("bbox"), target_node.get("bbox"))
            distance = float(gap.get("distance", 999999.0) or 999999.0)
            if distance > SINGLE_PIN_STUB_BRIDGE_MAX_BBOX_GAP:
                continue
            if not gap.get("axis_aligned") and distance > 32:
                continue
            seen_pairs.add(pair)
            target_pin_count = _node_pin_count(target_node)
            after = _metrics_for_merge(topology, [source_id, target_id])
            validation = validate_topology_candidate("single_pin_stub_bridge", before, after)
            score = _score_single_pin_stub_bridge_candidate(
                before,
                after,
                gap,
                attachment_score,
                target_pin_count,
                agent_audit,
            )
            risk = _risk_level_for_validation(validation, score)
            reasons = [
                f"Source node {source_id} has one pin: {pin_id}.",
                f"Target node {target_id} has {target_pin_count} pins.",
                f"BBox gap is {round(distance, 3)} px.",
                "Source and target bboxes are axis-aligned." if gap.get("axis_aligned") else "Source and target bboxes are not axis-aligned.",
                f"Terminal attachment score for {pin_id} is {round(attachment_score, 3)}.",
                f"Single-pin net count changes {before['single_pin_net_count']} -> {after['single_pin_net_count']}.",
            ]
            candidates.append(
                {
                    "candidate_id": f"STB{candidate_index}",
                    "repair_type": "single_pin_stub_bridge",
                    "summary": f"Dry-run bridge single-pin stub {source_id} into {target_id}.",
                    "target_nodes": [source_id, target_id],
                    "affected_nets": [source_id, target_id],
                    "target_pins": [pin_id],
                    "target_component_id": (source_node.get("component_ids") or [None])[0],
                    "geometry": {
                        "bbox_gap": gap,
                        "source_bbox": source_node.get("bbox"),
                        "target_bbox": target_node.get("bbox"),
                        "source_node_id": source_id,
                        "target_node_id": target_id,
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
                        "source_node": {
                            "node_id": source_id,
                            "pin_ids": _node_pin_ids(source_node),
                            "bbox": source_node.get("bbox"),
                            "segment_ids": source_node.get("segment_ids", []),
                            "raw_component_ids": source_node.get("raw_component_ids", []),
                        },
                        "target_node": {
                            "node_id": target_id,
                            "pin_ids": _node_pin_ids(target_node),
                            "bbox": target_node.get("bbox"),
                            "segment_ids": target_node.get("segment_ids", []),
                            "raw_component_ids": target_node.get("raw_component_ids", []),
                        },
                        "terminal_attachment": attachment,
                    },
                    "mutates_topology": False,
                }
            )
            candidate_index += 1

    for left_index, (left_id, left_node) in enumerate(single_pin_nodes):
        left_pin_id = _node_pin_ids(left_node)[0]
        left_attachment = attachments_by_pin.get(left_pin_id, {})
        left_score = float(left_attachment.get("best_attachment_score", 0.0) or 0.0)
        if left_score < STUB_BRIDGE_MIN_ATTACHMENT_SCORE:
            continue
        for right_id, right_node in single_pin_nodes[left_index + 1 :]:
            right_pin_id = _node_pin_ids(right_node)[0]
            if set(left_node.get("component_ids", []) or []).intersection(right_node.get("component_ids", []) or []):
                continue
            right_attachment = attachments_by_pin.get(right_pin_id, {})
            right_score = float(right_attachment.get("best_attachment_score", 0.0) or 0.0)
            if right_score < STUB_BRIDGE_MIN_ATTACHMENT_SCORE:
                continue
            pair = tuple(sorted([left_id, right_id]))
            if pair in seen_pairs:
                continue
            gap = _bbox_gap(left_node.get("bbox"), right_node.get("bbox"))
            distance = float(gap.get("distance", 999999.0) or 999999.0)
            if distance > SINGLE_PIN_PAIR_BRIDGE_MAX_BBOX_GAP:
                continue
            if not gap.get("axis_aligned") and distance > 80:
                continue
            seen_pairs.add(pair)
            after = _metrics_for_merge(topology, [left_id, right_id])
            validation = validate_topology_candidate("single_pin_stub_bridge", before, after)
            score = _score_single_pin_stub_bridge_candidate(
                before,
                after,
                gap,
                min(left_score, right_score),
                1,
                agent_audit,
            )
            score = round(min(0.95, score + 0.1), 3)
            risk = _risk_level_for_validation(validation, score)
            target_pins = sorted(set(_node_pin_ids(left_node) + _node_pin_ids(right_node)))
            candidates.append(
                {
                    "candidate_id": f"STB{candidate_index}",
                    "repair_type": "single_pin_stub_bridge",
                    "summary": f"Dry-run bridge single-pin pair {left_id} + {right_id}.",
                    "target_nodes": [left_id, right_id],
                    "affected_nets": [left_id, right_id],
                    "target_pins": target_pins,
                    "target_component_id": None,
                    "geometry": {
                        "bridge_mode": "single_pin_pair",
                        "bbox_gap": gap,
                        "source_bbox": left_node.get("bbox"),
                        "target_bbox": right_node.get("bbox"),
                        "source_node_id": left_id,
                        "target_node_id": right_id,
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
                    "reasons": [
                        f"Both {left_id} and {right_id} are single-pin terminal stubs.",
                        f"BBox gap is {round(distance, 3)} px.",
                        "The two stubs belong to different components.",
                        f"Attachment scores are {round(left_score, 3)} and {round(right_score, 3)}.",
                        f"Single-pin net count changes {before['single_pin_net_count']} -> {after['single_pin_net_count']}.",
                    ],
                    "evidence": {
                        "bridge_mode": "single_pin_pair",
                        "left_node": {
                            "node_id": left_id,
                            "pin_ids": _node_pin_ids(left_node),
                            "bbox": left_node.get("bbox"),
                            "segment_ids": left_node.get("segment_ids", []),
                        },
                        "right_node": {
                            "node_id": right_id,
                            "pin_ids": _node_pin_ids(right_node),
                            "bbox": right_node.get("bbox"),
                            "segment_ids": right_node.get("segment_ids", []),
                        },
                        "left_terminal_attachment": left_attachment,
                        "right_terminal_attachment": right_attachment,
                    },
                    "mutates_topology": False,
                }
            )
            candidate_index += 1

    return candidates[:SINGLE_PIN_STUB_BRIDGE_MAX_CANDIDATES]


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
    evidence_codes = {str(item.get("code")) for item in agent_audit.get("evidence", [])}

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
        or semantic.get("floating_pins")
        or semantic.get("unmatched_pins")
    ):
        selected.append(
            _tool(
                "component_axis_flip_dry_run",
                "Audit includes terminal evidence issues where a whole component pin axis may be wrong.",
            )
        )
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

    if (
        "missing_power_source" in evidence_codes
        or "confirm_missing_power_source" in action_types
        or semantic.get("circuit_completeness") == "passive_or_missing_power_source"
    ):
        selected.append(
            _tool(
                "component_class_override_dry_run",
                "Audit indicates a passive-or-missing-power-source case; inspect class alternatives before changing topology.",
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


def _candidate_matches_arguments(candidate: dict[str, Any], arguments: dict[str, Any] | None) -> bool:
    if not arguments:
        return True
    component_id = str(arguments.get("component_id") or "").strip()
    target_class = str(
        arguments.get("target_class")
        or arguments.get("alternate_class_name")
        or arguments.get("class_name")
        or ""
    ).strip()
    target_axis = str(arguments.get("target_axis") or arguments.get("alternate_axis") or "").strip()
    pin_id = str(arguments.get("pin_id") or "").strip()
    target_node_id = str(arguments.get("target_node_id") or arguments.get("node_id") or "").strip()
    raw_node_ids = arguments.get("node_ids", []) or arguments.get("target_node_ids", [])
    if not raw_node_ids and arguments.get("node_id_1") and arguments.get("node_id_2"):
        raw_node_ids = [arguments.get("node_id_1"), arguments.get("node_id_2")]
    if not raw_node_ids and arguments.get("node_id") and arguments.get("target_node_id"):
        raw_node_ids = [arguments.get("node_id"), arguments.get("target_node_id")]
    if raw_node_ids is None:
        raw_node_ids = []
    if not isinstance(raw_node_ids, list):
        raw_node_ids = [raw_node_ids]
    node_ids = {str(item) for item in raw_node_ids if item}
    raw_target_nodes = arguments.get("target_nodes", [])
    if raw_target_nodes is None:
        raw_target_nodes = []
    if not isinstance(raw_target_nodes, list):
        raw_target_nodes = [raw_target_nodes]
    if raw_target_nodes and not node_ids:
        node_ids = {str(item) for item in raw_target_nodes if item}

    geometry = candidate.get("geometry", {})
    if component_id and str(candidate.get("target_component_id") or geometry.get("component_id")) != component_id:
        return False
    if target_class and str(geometry.get("alternate_class_name") or "") != target_class:
        return False
    if target_axis and str(geometry.get("alternate_axis") or "") != target_axis:
        return False
    if pin_id and pin_id not in {str(item) for item in candidate.get("target_pins", [])}:
        return False
    if target_node_id and target_node_id not in {str(item) for item in candidate.get("target_nodes", [])}:
        return False
    if node_ids and not node_ids.issubset({str(item) for item in candidate.get("target_nodes", [])}):
        return False
    return True


def _generate_candidates_for_granular_tool(
    tool_name: str,
    debug_dir: Path,
    agent_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if tool_name == "dry_run_merge_nodes":
        return _generate_merge_node_candidates(debug_dir, agent_audit)
    if tool_name == "dry_run_component_class_override":
        return _generate_component_class_override_candidates(debug_dir, agent_audit)
    if tool_name == "dry_run_component_axis_flip":
        return _generate_axis_flip_candidates(debug_dir, agent_audit)
    if tool_name == "dry_run_reattach_pin":
        return _generate_reattach_pin_candidates(debug_dir, agent_audit)
    if tool_name == "dry_run_gap_bridge_merge":
        return _generate_gap_bridge_merge_candidates(debug_dir, agent_audit)
    if tool_name == "dry_run_single_pin_stub_bridge":
        return _generate_single_pin_stub_bridge_candidates(debug_dir, agent_audit)
    raise ValueError(f"Unsupported granular dry-run tool: {tool_name}")


def run_granular_repair_dry_run(
    debug_dir: str | Path,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run one repair dry-run tool explicitly selected by the agent planner."""
    if tool_name not in GRANULAR_DRY_RUN_TOOLS:
        raise ValueError(f"Unsupported granular dry-run tool: {tool_name}")
    base_dir = Path(debug_dir)
    out_dir = Path(output_dir) if output_dir else base_dir
    source_audit_path = base_dir / "agent_audit_report.json"
    agent_audit = _read_json(source_audit_path)
    missing_required = _missing_required_artifacts(base_dir)
    protected_outputs = [_file_digest(base_dir / file_name) for file_name in PROTECTED_OUTPUT_FILES]
    repair_candidates: list[dict[str, Any]] = []
    if not missing_required:
        repair_candidates = _generate_candidates_for_granular_tool(tool_name, base_dir, agent_audit)
        repair_candidates = [
            candidate
            for candidate in repair_candidates
            if _candidate_matches_arguments(candidate, arguments)
        ]
    repair_candidates = rank_repair_candidates(repair_candidates)[:REPAIR_DRY_RUN_MAX_CANDIDATES]
    viable_count = sum(1 for candidate in repair_candidates if candidate.get("validation_result") == "viable")
    blocked_count = sum(1 for candidate in repair_candidates if candidate.get("validation_result") == "blocked")
    reviewable_count = sum(
        1 for candidate in repair_candidates if candidate.get("validation_result") == "reviewable"
    )
    applyable_count = sum(1 for candidate in repair_candidates if candidate.get("candidate_mode") == "applyable")
    review_only_count = sum(1 for candidate in repair_candidates if candidate.get("candidate_mode") == "review_only")
    selected_tool = _tool(tool_name, "Planner explicitly selected this granular dry-run tool.")
    report = {
        "schema_version": "3.9-granular-repair-dry-run",
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
        "selected_repair_tools": [selected_tool] if not missing_required else [],
        "tool_name": tool_name,
        "tool_arguments": arguments or {},
        "repair_candidates": repair_candidates,
        "candidate_groups": {
            "applyable": [
                candidate for candidate in repair_candidates if candidate.get("candidate_mode") == "applyable"
            ],
            "review_only": [
                candidate for candidate in repair_candidates if candidate.get("candidate_mode") == "review_only"
            ],
        },
        "validation_summary": {
            "candidate_count": len(repair_candidates),
            "applyable_candidate_count": applyable_count,
            "review_only_candidate_count": review_only_count,
            "viable_candidate_count": viable_count,
            "reviewable_candidate_count": reviewable_count,
            "blocked_candidate_count": blocked_count,
            "validator_version": VALIDATOR_VERSION,
            "ranker_version": RANKER_VERSION,
            "topology_mutated": False,
            "protected_output_count": len(protected_outputs),
            "notes": [
                "This granular dry-run was explicitly selected by the planner.",
                "The tool validates candidate effects without mutating topology.json.",
            ],
        },
        "recommended_next_step": "review_ranked_candidate" if repair_candidates else "no_actionable_candidate",
        "protected_outputs": protected_outputs,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    safe_tool_name = tool_name.replace("/", "_")
    json_path = out_dir / f"{safe_tool_name}.json"
    md_path = out_dir / f"{safe_tool_name}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_repair_dry_run_markdown(report), encoding="utf-8")
    return {
        "report": report,
        "outputs": {
            "json": str(json_path),
            "markdown": str(md_path),
        },
    }


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
    grouped = report.get("candidate_groups", {})
    if grouped:
        lines.append(
            f"- applyable: `{len(grouped.get('applyable', []))}`; "
            f"review_only: `{len(grouped.get('review_only', []))}`"
        )
        lines.append("")
    if candidates:
        for candidate in candidates:
            lines.append(
                f"- `{candidate.get('candidate_id')}` / `{candidate.get('repair_type')}` "
                f"mode=`{candidate.get('candidate_mode')}` "
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
    if not missing_required and "component_axis_flip_dry_run" in selected_tool_ids:
        repair_candidates.extend(_generate_axis_flip_candidates(base_dir, agent_audit))
    if not missing_required and "component_class_override_dry_run" in selected_tool_ids:
        repair_candidates.extend(_generate_component_class_override_candidates(base_dir, agent_audit))
    if not missing_required and "reattach_pin_dry_run" in selected_tool_ids:
        repair_candidates.extend(_generate_reattach_pin_candidates(base_dir, agent_audit))
    if not missing_required and "evidence_review_dry_run" in selected_tool_ids:
        repair_candidates.extend(_generate_gap_bridge_merge_candidates(base_dir, agent_audit))
        repair_candidates.extend(_generate_evidence_review_candidates(base_dir, agent_audit))
    repair_candidates = rank_repair_candidates(repair_candidates)[:REPAIR_DRY_RUN_MAX_CANDIDATES]
    viable_count = sum(1 for candidate in repair_candidates if candidate.get("validation_result") == "viable")
    blocked_count = sum(1 for candidate in repair_candidates if candidate.get("validation_result") == "blocked")
    reviewable_count = sum(
        1 for candidate in repair_candidates if candidate.get("validation_result") == "reviewable"
    )
    applyable_count = sum(1 for candidate in repair_candidates if candidate.get("candidate_mode") == "applyable")
    review_only_count = sum(1 for candidate in repair_candidates if candidate.get("candidate_mode") == "review_only")

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
        "candidate_groups": {
            "applyable": [
                candidate for candidate in repair_candidates if candidate.get("candidate_mode") == "applyable"
            ],
            "review_only": [
                candidate for candidate in repair_candidates if candidate.get("candidate_mode") == "review_only"
            ],
        },
        "validation_summary": {
            "candidate_count": len(repair_candidates),
            "applyable_candidate_count": applyable_count,
            "review_only_candidate_count": review_only_count,
            "viable_candidate_count": viable_count,
            "reviewable_candidate_count": reviewable_count,
            "blocked_candidate_count": blocked_count,
            "validator_version": VALIDATOR_VERSION,
            "ranker_version": RANKER_VERSION,
            "topology_mutated": False,
            "protected_output_count": len(protected_outputs),
            "notes": [
                "Repair candidates are dry-run only and do not mutate topology.json.",
                "Dry-run tools implement merge_nodes_dry_run, component_axis_flip_dry_run, component_class_override_dry_run, reattach_pin_dry_run, evidence_review_dry_run, validation, and ranking.",
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
