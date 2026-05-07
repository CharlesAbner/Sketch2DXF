"""Apply human-approved dry-run repair candidates without overwriting originals."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_default_config
from src.export.dxf_exporter import export_to_dxf
from src.topology.topology_builder import _build_net_views, _build_netlist
from src.topology.symbol_library import get_symbol_definition


APPLY_SCHEMA_VERSION = "3.5-human-approved-repair-apply"
APPROVAL_REQUEST_JSON = "approval_request.json"
APPROVAL_REQUEST_MD = "approval_request.md"
APPROVAL_DECISION_JSON = "approval_decision.json"
CORRECTED_TOPOLOGY_JSON = "corrected_topology.json"
CORRECTED_NETLIST_JSON = "corrected_netlist.json"
CORRECTED_EXPORT_DXF = "corrected_export.dxf"
REPLAY_REPORT_JSON = "repair_replay_report.json"
REPLAY_REPORT_MD = "repair_replay_report.md"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _unique(items: list[Any]) -> list[Any]:
    result = []
    seen = set()
    for item in items:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _load_advisor_report(debug_dir: Path, advisor_dir: Path | None, report_path: Path | None) -> dict[str, Any]:
    candidates = []
    if report_path:
        candidates.append(report_path)
    if advisor_dir:
        candidates.append(advisor_dir / "agent_repair_advisor_report.json")
    candidates.append(debug_dir / "agent_repair_advisor_report.json")
    for path in candidates:
        doc = _read_json(path)
        if isinstance(doc, dict):
            doc["_source_path"] = str(path)
            return doc
    raise FileNotFoundError(
        "No agent_repair_advisor_report.json found. Pass --advisor-dir or --advisor-report."
    )


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _case_id_from_debug_dir(debug_dir: Path) -> str:
    case_summary = _read_json(debug_dir / "case_summary.json")
    if isinstance(case_summary, dict) and case_summary.get("case_id"):
        return str(case_summary["case_id"])
    return debug_dir.name


def _validate_report_matches_debug_dir(debug_dir: Path, advisor_report: dict[str, Any]) -> None:
    """Fail before writing anything if advisor/apply inputs point to different cases."""
    target_case_id = _case_id_from_debug_dir(debug_dir)
    advisor_case_id = str(advisor_report.get("case_id") or "")
    if advisor_case_id and advisor_case_id != target_case_id:
        raise ValueError(
            f"Advisor report case {advisor_case_id} does not match target case {target_case_id}."
        )

    advisor_debug_dir = advisor_report.get("debug_dir")
    if advisor_debug_dir:
        target_path = _resolved(debug_dir)
        advisor_path = _resolved(Path(str(advisor_debug_dir)))
        if advisor_path != target_path:
            raise ValueError(
                "Advisor report debug_dir does not match target debug_dir: "
                f"advisor={advisor_path}, target={target_path}."
            )


def _candidate_pool(advisor_report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    dossier = advisor_report.get("human_review_dossier", {})
    candidates.extend(dossier.get("selected_candidates", []))
    for step in advisor_report.get("repair_plan", {}).get("steps", []) or []:
        if isinstance(step, dict) and isinstance(step.get("candidate"), dict):
            candidates.append(step["candidate"])
    for step in dossier.get("repair_plan", {}).get("steps", []) or []:
        if isinstance(step, dict) and isinstance(step.get("candidate"), dict):
            candidates.append(step["candidate"])
    for result in advisor_report.get("tool_results", []):
        summary = result.get("result_summary", {})
        candidates.extend(summary.get("top_candidates", []))
        candidates.extend(summary.get("candidates", []))
    deduped = []
    seen = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("repair_candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        deduped.append(candidate)
    return deduped


def _select_candidate(advisor_report: dict[str, Any], candidate_id: str | None) -> dict[str, Any]:
    candidates = _candidate_pool(advisor_report)
    plan_ids = [
        str(step.get("candidate_id"))
        for step in advisor_report.get("repair_plan", {}).get("steps", []) or []
        if isinstance(step, dict) and step.get("candidate_id")
    ]
    wanted_id = candidate_id or (plan_ids[0] if plan_ids else None)
    if wanted_id:
        for candidate in candidates:
            if str(candidate.get("candidate_id")) == wanted_id:
                return candidate
    if candidates:
        return candidates[0]
    raise ValueError("No repair candidate found in advisor report.")


def _select_plan_candidates(
    advisor_report: dict[str, Any],
    plan_id: str | None = None,
    candidate_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if candidate_id:
        candidate = _select_candidate(advisor_report, candidate_id)
        plan = {
            "plan_id": "MANUAL_CANDIDATE",
            "status": "pending_human_review",
            "steps": [
                {
                    "step_id": "S1",
                    "candidate_id": candidate.get("candidate_id"),
                    "repair_type": candidate.get("repair_type"),
                    "candidate_mode": candidate.get("candidate_mode"),
                    "expected_improvement": candidate.get("improved_metrics", []),
                    "depends_on": [],
                    "candidate": candidate,
                }
            ],
        }
        return plan, [candidate]

    raw_plan = advisor_report.get("repair_plan")
    if not isinstance(raw_plan, dict):
        raw_plan = {"plan_id": "PLAN1", "status": "no_repair_plan", "steps": []}
    if plan_id and str(raw_plan.get("plan_id")) != str(plan_id):
        raise ValueError(f"Repair plan {plan_id} was not found in advisor report.")
    candidate_lookup = {
        str(candidate.get("candidate_id") or candidate.get("repair_candidate_id")): candidate
        for candidate in _candidate_pool(advisor_report)
    }
    candidates = []
    steps = []
    for step in raw_plan.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        candidate_id_value = str(step.get("candidate_id") or "")
        candidate = candidate_lookup.get(candidate_id_value)
        if not candidate:
            continue
        candidates.append(candidate)
        steps.append({**step, "candidate": candidate})
    plan = {**raw_plan, "steps": steps}
    if candidates:
        return plan, candidates

    candidate = _select_candidate(advisor_report, None)
    return {
        "plan_id": "PLAN1",
        "status": "fallback_single_candidate",
        "steps": [
            {
                "step_id": "S1",
                "candidate_id": candidate.get("candidate_id"),
                "repair_type": candidate.get("repair_type"),
                "candidate_mode": candidate.get("candidate_mode"),
                "expected_improvement": candidate.get("improved_metrics", []),
                "depends_on": [],
                "candidate": candidate,
            }
        ],
    }, [candidate]


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": plan.get("plan_id"),
        "status": plan.get("status"),
        "steps": [
            {key: value for key, value in step.items() if key != "candidate"}
            for step in plan.get("steps", [])
        ],
    }


def _approval_decision(
    approval: str,
    approval_file: Path | None,
    candidates: list[dict[str, Any]],
    approved_by: str,
    notes: str,
) -> dict[str, Any]:
    if approval_file:
        doc = _read_json(approval_file)
        if not isinstance(doc, dict):
            raise ValueError(f"Invalid approval file: {approval_file}")
        return doc
    return {
        "schema_version": "3.5-human-approval-decision",
        "decision": approval,
        "candidate_ids": [candidate.get("candidate_id") for candidate in candidates],
        "approved_by": approved_by,
        "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_approval_request(
    debug_dir: Path,
    advisor_report: dict[str, Any],
    plan: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_summaries = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "repair_type": candidate.get("repair_type"),
            "recommendation": candidate.get("recommendation"),
            "validation_result": candidate.get("validation_result"),
            "ranking_score": candidate.get("ranking_score"),
            "target_nodes": candidate.get("target_nodes", []),
            "target_pins": candidate.get("target_pins", []),
            "improved_metrics": candidate.get("improved_metrics", []),
            "regressed_metrics": candidate.get("regressed_metrics", []),
            "blocking_issues": candidate.get("blocking_issues", []),
            "reasons": candidate.get("reasons", []),
        }
        for candidate in candidates
    ]
    return {
        "schema_version": "3.5-human-approval-request",
        "case_id": advisor_report.get("case_id") or debug_dir.name,
        "debug_dir": str(debug_dir),
        "advisor_report": advisor_report.get("_source_path"),
        "request_status": "pending_human_decision",
        "repair_plan": {
            "plan_id": plan.get("plan_id"),
            "status": plan.get("status"),
            "steps": [
                {key: value for key, value in step.items() if key != "candidate"}
                for step in plan.get("steps", [])
            ],
        },
        "candidates": candidate_summaries,
        "candidate": candidate_summaries[0] if candidate_summaries else {},
        "instructions": {
            "accept": "Run apply with --approval accept after checking overlay/debug images.",
            "reject": "Run apply with --approval reject, or edit approval_decision.json with decision=reject.",
            "approval_file_schema": {
                "decision": "accept | reject",
                "candidate_ids": [candidate.get("candidate_id") for candidate in candidates],
                "approved_by": "your_name",
                "notes": "human review notes",
            },
        },
    }


def render_approval_request_markdown(request: dict[str, Any]) -> str:
    candidate = request.get("candidate", {})
    plan = request.get("repair_plan", {})
    lines = [
        f"# Approval Request: {request.get('case_id')}",
        "",
        f"- status: `{request.get('request_status')}`",
        f"- repair plan: `{plan.get('plan_id')}` status=`{plan.get('status')}`",
        f"- candidate: `{candidate.get('candidate_id')}` / `{candidate.get('repair_type')}`",
        f"- recommendation: `{candidate.get('recommendation')}`",
        f"- validation: `{candidate.get('validation_result')}`",
        f"- ranking score: `{candidate.get('ranking_score')}`",
        f"- target nodes: `{candidate.get('target_nodes')}`",
        f"- target pins: `{candidate.get('target_pins')}`",
        f"- improved metrics: `{candidate.get('improved_metrics')}`",
        f"- regressed metrics: `{candidate.get('regressed_metrics')}`",
        "",
        "## Reasons",
        "",
    ]
    if request.get("candidates"):
        lines.extend(["", "## Plan Steps", ""])
        for index, item in enumerate(request.get("candidates", []), start=1):
            lines.append(
                f"- S{index}: `{item.get('candidate_id')}` / `{item.get('repair_type')}` "
                f"validation=`{item.get('validation_result')}` ranking_score=`{item.get('ranking_score')}`"
            )
    for reason in candidate.get("reasons", []):
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Human Check",
            "",
            "- Open `13_overlay.png` and `12_active_nodes.png` for the same debug run.",
            "- Confirm the listed repair matches the visible circuit intent.",
            "- Apply only if the candidate improves the topology without creating a short.",
        ]
    )
    return "\n".join(lines)


def _bbox_union(nodes: list[dict[str, Any]]) -> list[float] | None:
    boxes = [node.get("bbox") for node in nodes if isinstance(node.get("bbox"), list) and len(node.get("bbox")) >= 4]
    if not boxes:
        return None
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _merge_node_payload(nodes: list[dict[str, Any]], canonical_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(nodes[0])
    bbox = _bbox_union(nodes)
    if bbox:
        base["bbox"] = bbox
        base["x"] = round((bbox[0] + bbox[2]) / 2.0)
        base["y"] = round((bbox[1] + bbox[3]) / 2.0)
    base["node_id"] = canonical_id
    base["source"] = "human_approved_repair"
    base["support_status"] = "human_approved_merge"

    union_fields = [
        "pin_ids",
        "component_ids",
        "raw_component_ids",
        "relay_raw_component_ids",
        "merged_raw_node_ids",
        "bridge_candidate_ids",
        "segment_ids",
        "edge_ids",
        "vertex_ids",
        "endpoint_ids",
        "junction_ids",
        "keep_reasons",
        "noise_flags",
    ]
    for field in union_fields:
        merged_items: list[Any] = []
        for node in nodes:
            merged_items.extend(_as_list(node.get(field)))
        base[field] = _unique(merged_items)

    points: list[Any] = []
    members: list[Any] = []
    bridge_connections: list[Any] = []
    for node in nodes:
        points.extend(_as_list(node.get("points")))
        members.extend(_as_list(node.get("members")))
        bridge_connections.extend(_as_list(node.get("bridge_connections")))
    base["points"] = _unique(points)
    base["members"] = _unique(members)
    base["bridge_connections"] = _unique(bridge_connections)
    base["terminal_support_count"] = len(base.get("pin_ids", []))
    base["repair_applied"] = {
        "candidate_id": candidate.get("candidate_id"),
        "repair_type": candidate.get("repair_type"),
        "merged_node_ids": [node.get("node_id") for node in nodes],
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    return base


def _rebuild_topology_views(topology: dict[str, Any]) -> dict[str, Any]:
    nets, component_nets, metadata = _build_net_views(
        topology.get("components", []),
        topology.get("pins", []),
        topology.get("nodes", []),
        topology.get("connections", []),
    )
    topology["components"] = metadata["components"]
    topology["nets"] = nets
    topology["component_nets"] = component_nets
    topology["netlist"] = _build_netlist(
        metadata["components"],
        nets,
        component_nets,
        metadata["pins"],
        metadata["lookup"],
    )
    topology["stats"] = {
        **topology.get("stats", {}),
        "component_count": len(topology.get("components", [])),
        "pin_group_count": len(topology.get("pins", [])),
        "node_count": len(topology.get("nodes", [])),
        "wire_count": len(topology.get("wires", [])),
        "connection_count": len(topology.get("connections", [])),
        "net_count": len(nets),
    }
    return topology


def _simple_metrics(topology: dict[str, Any]) -> dict[str, Any]:
    nets = topology.get("netlist", {}).get("nets", topology.get("nets", []))
    single_pin = [net.get("net_id") for net in nets if int(net.get("pin_count", 0)) == 1]
    zero_pin = [net.get("net_id") for net in nets if int(net.get("pin_count", 0)) == 0]
    return {
        "node_count": len(topology.get("nodes", [])),
        "net_count": len(nets),
        "single_pin_net_count": len(single_pin),
        "single_pin_net_ids": single_pin,
        "zero_pin_net_count": len(zero_pin),
        "zero_pin_net_ids": zero_pin,
        "connection_count": len(topology.get("connections", [])),
    }


def _apply_merge_nodes(topology: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    target_nodes = [str(item) for item in candidate.get("target_nodes", [])]
    if len(target_nodes) < 2:
        raise ValueError("merge_nodes candidate requires at least two target_nodes.")
    canonical_id = target_nodes[0]
    node_lookup = {str(node.get("node_id")): node for node in topology.get("nodes", [])}
    missing = [node_id for node_id in target_nodes if node_id not in node_lookup]
    if missing:
        raise ValueError(f"Cannot apply merge; target nodes missing from topology: {missing}")

    target_payloads = [node_lookup[node_id] for node_id in target_nodes]
    merged_node = _merge_node_payload(target_payloads, canonical_id, candidate)
    merged_set = set(target_nodes)
    new_nodes = [node for node in topology.get("nodes", []) if str(node.get("node_id")) not in merged_set]
    new_nodes.append(merged_node)
    new_nodes.sort(key=lambda node: str(node.get("node_id")))

    node_id_map = {str(node_id): canonical_id for node_id in target_nodes}
    new_connections = []
    for connection in topology.get("connections", []):
        updated = deepcopy(connection)
        raw_node_id = str(updated.get("node_id"))
        if raw_node_id in node_id_map:
            updated["raw_node_id_before_repair"] = raw_node_id
            updated["node_id"] = canonical_id
        new_connections.append(updated)

    repaired = deepcopy(topology)
    repaired["nodes"] = new_nodes
    repaired["connections"] = new_connections
    repaired["node_id_map"] = {
        **{str(key): str(value) for key, value in topology.get("node_id_map", {}).items()},
        **node_id_map,
    }
    repaired.setdefault("repair_history", [])
    repaired["repair_history"].append(
        {
            "candidate_id": candidate.get("candidate_id"),
            "repair_type": candidate.get("repair_type", "merge_nodes"),
            "target_nodes": target_nodes,
            "canonical_node_id": canonical_id,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return _rebuild_topology_views(repaired)


def _node_lookup(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(node.get("node_id")): node for node in topology.get("nodes", []) if node.get("node_id")}


def _remove_once(items: list[Any], value: Any) -> list[Any]:
    removed = False
    result = []
    for item in items:
        if not removed and item == value:
            removed = True
            continue
        result.append(item)
    return result


def _component_id_for_pin(topology: dict[str, Any], pin_id: str) -> str | None:
    for group in topology.get("pins", []):
        component_id = group.get("component_id")
        for pin in group.get("pins", []):
            if str(pin.get("pin_id")) == pin_id:
                return str(pin.get("component_id") or component_id)
    for connection in topology.get("connections", []):
        if str(connection.get("pin_id")) == pin_id and connection.get("component_id"):
            return str(connection.get("component_id"))
    return None


def _remove_pin_from_nodes(topology: dict[str, Any], pin_id: str, component_id: str) -> None:
    for node in topology.get("nodes", []):
        node["pin_ids"] = _remove_once(_as_list(node.get("pin_ids")), pin_id)
        node["component_ids"] = _remove_once(_as_list(node.get("component_ids")), component_id)
        node["terminal_support_count"] = len(_as_list(node.get("pin_ids")))


def _add_pin_to_node(topology: dict[str, Any], node_id: str, pin_id: str, component_id: str) -> None:
    for node in topology.get("nodes", []):
        if str(node.get("node_id")) != node_id:
            continue
        node["pin_ids"] = _unique(_as_list(node.get("pin_ids")) + [pin_id])
        node["component_ids"] = _unique(_as_list(node.get("component_ids")) + [component_id])
        node["terminal_support_count"] = len(_as_list(node.get("pin_ids")))
        return


def _set_or_add_pin_connection(
    topology: dict[str, Any],
    pin_id: str,
    component_id: str,
    node_id: str,
    candidate: dict[str, Any],
) -> None:
    for connection in topology.get("connections", []):
        if str(connection.get("pin_id")) != pin_id:
            continue
        old_node_id = str(connection.get("node_id"))
        connection["raw_node_id_before_repair"] = old_node_id
        connection["node_id"] = node_id
        connection["source"] = "human_approved_repair"
        connection["repair_applied"] = {
            "candidate_id": candidate.get("candidate_id"),
            "repair_type": candidate.get("repair_type"),
            "from_node_id": old_node_id,
            "to_node_id": node_id,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
        return
    topology.setdefault("connections", [])
    topology["connections"].append(
        {
            "pin_id": pin_id,
            "component_id": component_id,
            "node_id": node_id,
            "match_type": "human_approved_repair",
            "confidence": candidate.get("score"),
            "source": "human_approved_repair",
            "repair_applied": {
                "candidate_id": candidate.get("candidate_id"),
                "repair_type": candidate.get("repair_type"),
                "to_node_id": node_id,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    )


def _apply_reattach_pin(topology: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    target_pins = [str(item) for item in candidate.get("target_pins", []) if item]
    target_nodes = [str(item) for item in candidate.get("target_nodes", []) if item]
    if len(target_pins) != 1:
        raise ValueError("reattach_pin candidate requires exactly one target pin.")
    if len(target_nodes) < 2:
        raise ValueError("reattach_pin candidate requires current and target node ids.")

    pin_id = target_pins[0]
    current_node_id = target_nodes[0]
    target_node_id = target_nodes[-1]
    nodes = _node_lookup(topology)
    if current_node_id not in nodes:
        raise ValueError(f"Cannot apply reattach; current node missing: {current_node_id}")
    if target_node_id not in nodes:
        raise ValueError(f"Cannot apply reattach; target node missing: {target_node_id}")

    component_id = _component_id_for_pin(topology, pin_id)
    if not component_id:
        raise ValueError(f"Cannot apply reattach; component for pin is unknown: {pin_id}")

    repaired = deepcopy(topology)
    updated_connection = False
    for connection in repaired.get("connections", []):
        if str(connection.get("pin_id")) == pin_id:
            updated_connection = True
            break
    if not updated_connection:
        raise ValueError(f"Cannot apply reattach; pin connection missing: {pin_id}")

    _remove_pin_from_nodes(repaired, pin_id, component_id)
    _set_or_add_pin_connection(repaired, pin_id, component_id, target_node_id, candidate)
    _add_pin_to_node(repaired, target_node_id, pin_id, component_id)
    for node in repaired.get("nodes", []):
        if str(node.get("node_id")) in {current_node_id, target_node_id}:
            node.setdefault("repair_history", [])
            node["repair_history"].append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "repair_type": "reattach_pin",
                    "pin_id": pin_id,
                    "from_node_id": current_node_id,
                    "to_node_id": target_node_id,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                }
            )

    repaired.setdefault("repair_history", [])
    repaired["repair_history"].append(
        {
            "candidate_id": candidate.get("candidate_id"),
            "repair_type": "reattach_pin",
            "pin_id": pin_id,
            "from_node_id": current_node_id,
            "to_node_id": target_node_id,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return _rebuild_topology_views(repaired)


def _apply_component_pin_axis_flip(topology: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    geometry = candidate.get("geometry", {})
    replacement_group = geometry.get("replacement_pin_group")
    pin_node_overrides = geometry.get("pin_node_overrides", {})
    if not isinstance(replacement_group, dict):
        raise ValueError("component_pin_axis_flip candidate requires a replacement_pin_group.")
    component_id = str(candidate.get("target_component_id") or replacement_group.get("component_id"))
    if not component_id:
        raise ValueError("component_pin_axis_flip candidate requires a target component id.")
    if not pin_node_overrides:
        raise ValueError("component_pin_axis_flip candidate requires pin_node_overrides.")

    repaired = deepcopy(topology)
    replaced = False
    for index, group in enumerate(repaired.get("pins", [])):
        if str(group.get("component_id")) == component_id:
            repaired["pins"][index] = replacement_group
            replaced = True
            break
    if not replaced:
        raise ValueError(f"Cannot apply axis flip; component pins missing: {component_id}")

    valid_node_ids = {str(node.get("node_id")) for node in repaired.get("nodes", [])}
    for pin_id, node_id in pin_node_overrides.items():
        pin_id = str(pin_id)
        node_id = str(node_id)
        if node_id not in valid_node_ids:
            raise ValueError(f"Cannot apply axis flip; target node missing: {node_id}")
        _remove_pin_from_nodes(repaired, pin_id, component_id)
        _set_or_add_pin_connection(repaired, pin_id, component_id, node_id, candidate)
        _add_pin_to_node(repaired, node_id, pin_id, component_id)

    repaired.setdefault("repair_history", [])
    repaired["repair_history"].append(
        {
            "candidate_id": candidate.get("candidate_id"),
            "repair_type": "component_pin_axis_flip",
            "component_id": component_id,
            "target_pins": candidate.get("target_pins", []),
            "pin_node_overrides": pin_node_overrides,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return _rebuild_topology_views(repaired)


def _symbol_pin_count(class_name: str) -> int:
    return int(get_symbol_definition(class_name).get("pin_count", 0) or 0)


def _apply_component_class_override(topology: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    geometry = candidate.get("geometry", {})
    component_id = str(candidate.get("target_component_id") or geometry.get("component_id") or "")
    alternate_class = str(geometry.get("alternate_class_name") or "")
    current_class = str(geometry.get("current_class_name") or "")
    if not component_id:
        raise ValueError("component_class_override candidate requires a target component id.")
    if not alternate_class:
        raise ValueError("component_class_override candidate requires an alternate_class_name.")

    repaired = deepcopy(topology)
    target_component = None
    for component in repaired.get("components", []):
        if str(component.get("id")) == component_id:
            target_component = component
            break
    if target_component is None:
        raise ValueError(f"Cannot apply class override; component missing: {component_id}")

    actual_current_class = str(target_component.get("class_name", "unknown"))
    current_pin_count = _symbol_pin_count(actual_current_class)
    alternate_pin_count = _symbol_pin_count(alternate_class)
    if current_pin_count != alternate_pin_count:
        raise ValueError(
            "component_class_override only supports compatible pin counts: "
            f"{actual_current_class}={current_pin_count}, {alternate_class}={alternate_pin_count}."
        )

    alternative_payload = None
    for option in candidate.get("evidence", {}).get("class_candidates", []):
        if str(option.get("class_name")) == alternate_class:
            alternative_payload = option
            break
    if alternative_payload is None:
        for option in target_component.get("class_candidates", []) + target_component.get("class_alternatives", []):
            if str(option.get("class_name")) == alternate_class:
                alternative_payload = option
                break

    target_component["class_name"] = alternate_class
    if isinstance(alternative_payload, dict):
        if alternative_payload.get("class_id") is not None:
            target_component["class_id"] = alternative_payload.get("class_id")
        if alternative_payload.get("score") is not None:
            target_component["score"] = alternative_payload.get("score")
    target_component["class_override"] = {
        "candidate_id": candidate.get("candidate_id"),
        "repair_type": "component_class_override",
        "previous_class_name": actual_current_class,
        "requested_current_class_name": current_class,
        "new_class_name": alternate_class,
        "source": "human_approved_agent_repair",
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

    repaired.setdefault("repair_history", [])
    repaired["repair_history"].append(
        {
            "candidate_id": candidate.get("candidate_id"),
            "repair_type": "component_class_override",
            "component_id": component_id,
            "from_class_name": actual_current_class,
            "to_class_name": alternate_class,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return _rebuild_topology_views(repaired)


def _apply_candidate(topology: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    repair_type = candidate.get("repair_type")
    if repair_type in {"merge_nodes", "gap_bridge_merge", "single_pin_stub_bridge"}:
        return _apply_merge_nodes(topology, candidate)
    if repair_type == "reattach_pin":
        return _apply_reattach_pin(topology, candidate)
    if repair_type == "component_pin_axis_flip":
        return _apply_component_pin_axis_flip(topology, candidate)
    if repair_type == "component_class_override":
        return _apply_component_class_override(topology, candidate)
    raise ValueError(f"Unsupported apply repair_type: {repair_type}")


def _candidate_is_applyable(candidate: dict[str, Any]) -> bool:
    return candidate.get("repair_type") in {
        "merge_nodes",
        "reattach_pin",
        "gap_bridge_merge",
        "single_pin_stub_bridge",
        "component_pin_axis_flip",
        "component_class_override",
    }


def _export_corrected_dxf(corrected_topology: dict[str, Any], output_dir: Path, output_stem: str) -> dict[str, Any]:
    config = get_default_config()
    config.setdefault("export", {})
    config["export"]["output_stem"] = output_stem
    export_result = export_to_dxf(corrected_topology, config)
    corrected_dxf_path = output_dir / CORRECTED_EXPORT_DXF
    corrected_netlist_path = output_dir / CORRECTED_NETLIST_JSON
    if export_result.get("dxf_path") and Path(export_result["dxf_path"]).exists():
        shutil.copyfile(export_result["dxf_path"], corrected_dxf_path)
    _write_json(corrected_netlist_path, corrected_topology.get("netlist", {}))
    return {
        **export_result,
        "corrected_dxf_path": str(corrected_dxf_path),
        "corrected_netlist_path": str(corrected_netlist_path),
    }


def render_replay_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Repair Replay Report: {report.get('case_id')}",
        "",
        f"- status: `{report.get('status')}`",
        f"- decision: `{report.get('approval_decision', {}).get('decision')}`",
        f"- repair plan: `{report.get('repair_plan', {}).get('plan_id')}`",
        f"- candidate: `{report.get('candidate', {}).get('candidate_id')}`",
        f"- repair type: `{report.get('candidate', {}).get('repair_type')}`",
        f"- topology mutated in place: `{report.get('topology_mutated_in_place')}`",
        f"- corrected topology: `{report.get('outputs', {}).get('corrected_topology')}`",
        f"- corrected DXF: `{report.get('outputs', {}).get('corrected_dxf')}`",
        "",
        "## Metrics",
        "",
        f"- before: `{report.get('before_metrics')}`",
        f"- after: `{report.get('after_metrics')}`",
        "",
        "## Export",
        "",
        f"- export success: `{report.get('export', {}).get('export_success')}`",
        f"- export errors: `{report.get('export', {}).get('export_errors')}`",
    ]
    return "\n".join(lines)


def run_human_approved_repair_apply(
    debug_dir: str | Path,
    advisor_dir: str | Path | None = None,
    advisor_report: str | Path | None = None,
    output_dir: str | Path | None = None,
    candidate_id: str | None = None,
    plan_id: str | None = None,
    approval: str = "pending",
    approval_file: str | Path | None = None,
    approved_by: str = "manual_cli",
    notes: str = "",
) -> dict[str, Any]:
    """Create an approval request and optionally apply an accepted repair."""
    base_dir = Path(debug_dir)
    out_dir = Path(output_dir) if output_dir else base_dir / "repair_apply"
    report = _load_advisor_report(
        base_dir,
        Path(advisor_dir) if advisor_dir else None,
        Path(advisor_report) if advisor_report else None,
    )
    _validate_report_matches_debug_dir(base_dir, report)
    plan, candidates = _select_plan_candidates(report, plan_id=plan_id, candidate_id=candidate_id)
    candidate = candidates[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    approval_request = _make_approval_request(base_dir, report, plan, candidates)
    _write_json(out_dir / APPROVAL_REQUEST_JSON, approval_request)
    (out_dir / APPROVAL_REQUEST_MD).write_text(
        render_approval_request_markdown(approval_request),
        encoding="utf-8",
    )

    decision = _approval_decision(
        approval,
        Path(approval_file) if approval_file else None,
        candidates,
        approved_by,
        notes,
    )
    _write_json(out_dir / APPROVAL_DECISION_JSON, decision)
    if decision.get("decision") != "accept":
        replay_report = {
            "schema_version": APPLY_SCHEMA_VERSION,
            "case_id": report.get("case_id") or base_dir.name,
            "status": "approval_not_accepted",
            "approval_decision": decision,
            "repair_plan": _plan_summary(plan),
            "candidate": {
                "candidate_id": candidate.get("candidate_id"),
                "repair_type": candidate.get("repair_type"),
            },
            "topology_mutated_in_place": False,
            "outputs": {
                "approval_request": str(out_dir / APPROVAL_REQUEST_JSON),
                "approval_request_markdown": str(out_dir / APPROVAL_REQUEST_MD),
                "approval_decision": str(out_dir / APPROVAL_DECISION_JSON),
            },
        }
        _write_json(out_dir / REPLAY_REPORT_JSON, replay_report)
        (out_dir / REPLAY_REPORT_MD).write_text(render_replay_report_markdown(replay_report), encoding="utf-8")
        return {
            "report": replay_report,
            "outputs": replay_report["outputs"],
        }

    unsupported_candidates = [item for item in candidates if not _candidate_is_applyable(item)]
    if unsupported_candidates:
        replay_report = {
            "schema_version": APPLY_SCHEMA_VERSION,
            "case_id": report.get("case_id") or base_dir.name,
            "status": "unsupported_apply_type",
            "approval_decision": decision,
            "repair_plan": _plan_summary(plan),
            "candidate": {
                "candidate_id": candidate.get("candidate_id"),
                "repair_type": candidate.get("repair_type"),
                "recommendation": candidate.get("recommendation"),
                "validation_result": candidate.get("validation_result"),
                "ranking_score": candidate.get("ranking_score"),
            },
            "topology_mutated_in_place": False,
            "message": (
                "At least one plan candidate is review-only in the current apply layer. "
                "No topology correction was applied."
            ),
            "outputs": {
                "approval_request": str(out_dir / APPROVAL_REQUEST_JSON),
                "approval_request_markdown": str(out_dir / APPROVAL_REQUEST_MD),
                "approval_decision": str(out_dir / APPROVAL_DECISION_JSON),
                "replay_report": str(out_dir / REPLAY_REPORT_JSON),
                "replay_report_markdown": str(out_dir / REPLAY_REPORT_MD),
            },
        }
        _write_json(out_dir / REPLAY_REPORT_JSON, replay_report)
        (out_dir / REPLAY_REPORT_MD).write_text(render_replay_report_markdown(replay_report), encoding="utf-8")
        return {
            "report": replay_report,
            "outputs": replay_report["outputs"],
        }

    topology = _read_json(base_dir / "topology.json")
    if not isinstance(topology, dict):
        raise FileNotFoundError(f"Missing topology.json under {base_dir}")
    before_metrics = _simple_metrics(topology)
    corrected_topology = topology
    applied_steps = []
    for index, item in enumerate(candidates, start=1):
        step_before = _simple_metrics(corrected_topology)
        corrected_topology = _apply_candidate(corrected_topology, item)
        step_after = _simple_metrics(corrected_topology)
        applied_steps.append(
            {
                "step_id": f"S{index}",
                "candidate_id": item.get("candidate_id"),
                "repair_type": item.get("repair_type"),
                "before_metrics": step_before,
                "after_metrics": step_after,
            }
        )
    after_metrics = _simple_metrics(corrected_topology)
    corrected_topology["human_approval"] = decision
    corrected_topology["source_topology_path"] = str(base_dir / "topology.json")
    corrected_topology["topology_mutated_in_place"] = False
    corrected_topology_path = out_dir / CORRECTED_TOPOLOGY_JSON
    _write_json(corrected_topology_path, corrected_topology)

    export_result = _export_corrected_dxf(
        corrected_topology,
        out_dir,
        f"{base_dir.name}_corrected",
    )
    replay_report = {
        "schema_version": APPLY_SCHEMA_VERSION,
        "case_id": report.get("case_id") or base_dir.name,
        "status": "applied",
        "approval_decision": decision,
        "repair_plan": _plan_summary(plan),
        "candidate": {
            "candidate_id": candidate.get("candidate_id"),
            "repair_type": candidate.get("repair_type"),
            "target_nodes": candidate.get("target_nodes", []),
            "target_pins": candidate.get("target_pins", []),
            "recommendation": candidate.get("recommendation"),
            "validation_result": candidate.get("validation_result"),
            "ranking_score": candidate.get("ranking_score"),
        },
        "applied_candidates": [
            {
                "candidate_id": item.get("candidate_id"),
                "repair_type": item.get("repair_type"),
                "target_nodes": item.get("target_nodes", []),
                "target_pins": item.get("target_pins", []),
                "recommendation": item.get("recommendation"),
                "validation_result": item.get("validation_result"),
                "ranking_score": item.get("ranking_score"),
            }
            for item in candidates
        ],
        "applied_steps": applied_steps,
        "before_metrics": before_metrics,
        "after_metrics": after_metrics,
        "expected_after_metrics": candidate.get("after_metrics", {}),
        "topology_mutated_in_place": False,
        "export_success": bool(export_result.get("export_success")),
        "export": export_result,
        "outputs": {
            "approval_request": str(out_dir / APPROVAL_REQUEST_JSON),
            "approval_request_markdown": str(out_dir / APPROVAL_REQUEST_MD),
            "approval_decision": str(out_dir / APPROVAL_DECISION_JSON),
            "corrected_topology": str(corrected_topology_path),
            "corrected_netlist": str(out_dir / CORRECTED_NETLIST_JSON),
            "corrected_dxf": str(out_dir / CORRECTED_EXPORT_DXF),
            "replay_report": str(out_dir / REPLAY_REPORT_JSON),
            "replay_report_markdown": str(out_dir / REPLAY_REPORT_MD),
        },
    }
    _write_json(out_dir / REPLAY_REPORT_JSON, replay_report)
    (out_dir / REPLAY_REPORT_MD).write_text(render_replay_report_markdown(replay_report), encoding="utf-8")
    return {
        "report": replay_report,
        "outputs": replay_report["outputs"],
    }
