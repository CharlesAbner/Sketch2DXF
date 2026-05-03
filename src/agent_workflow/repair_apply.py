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


def _candidate_pool(advisor_report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    dossier = advisor_report.get("human_review_dossier", {})
    candidates.extend(dossier.get("selected_candidates", []))
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
    selected_ids = [str(item) for item in advisor_report.get("selected_candidate_ids", [])]
    wanted_id = candidate_id or (selected_ids[0] if selected_ids else None)
    if wanted_id:
        for candidate in candidates:
            if str(candidate.get("candidate_id")) == wanted_id:
                return candidate
    if candidates:
        return candidates[0]
    raise ValueError("No repair candidate found in advisor report.")


def _approval_decision(
    approval: str,
    approval_file: Path | None,
    candidate: dict[str, Any],
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
        "candidate_ids": [candidate.get("candidate_id")],
        "approved_by": approved_by,
        "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_approval_request(
    debug_dir: Path,
    advisor_report: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "3.5-human-approval-request",
        "case_id": advisor_report.get("case_id") or debug_dir.name,
        "debug_dir": str(debug_dir),
        "advisor_report": advisor_report.get("_source_path"),
        "request_status": "pending_human_decision",
        "candidate": {
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
        },
        "instructions": {
            "accept": "Run apply with --approval accept after checking overlay/debug images.",
            "reject": "Run apply with --approval reject, or edit approval_decision.json with decision=reject.",
            "approval_file_schema": {
                "decision": "accept | reject",
                "candidate_ids": [candidate.get("candidate_id")],
                "approved_by": "your_name",
                "notes": "human review notes",
            },
        },
    }


def render_approval_request_markdown(request: dict[str, Any]) -> str:
    candidate = request.get("candidate", {})
    lines = [
        f"# Approval Request: {request.get('case_id')}",
        "",
        f"- status: `{request.get('request_status')}`",
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
    for reason in candidate.get("reasons", []):
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Human Check",
            "",
            "- Open `13_overlay.png` and `12_active_nodes.png` for the same debug run.",
            "- Confirm the listed node merge matches the visible circuit intent.",
            "- Apply only if the candidate removes an artificial split without creating a short.",
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
            "repair_type": "merge_nodes",
            "target_nodes": target_nodes,
            "canonical_node_id": canonical_id,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return _rebuild_topology_views(repaired)


def _apply_candidate(topology: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    repair_type = candidate.get("repair_type")
    if repair_type == "merge_nodes":
        return _apply_merge_nodes(topology, candidate)
    raise ValueError(f"Unsupported apply repair_type: {repair_type}")


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
    approval: str = "pending",
    approval_file: str | Path | None = None,
    approved_by: str = "manual_cli",
    notes: str = "",
) -> dict[str, Any]:
    """Create an approval request and optionally apply an accepted repair."""
    base_dir = Path(debug_dir)
    out_dir = Path(output_dir) if output_dir else base_dir / "repair_apply"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = _load_advisor_report(
        base_dir,
        Path(advisor_dir) if advisor_dir else None,
        Path(advisor_report) if advisor_report else None,
    )
    candidate = _select_candidate(report, candidate_id)
    approval_request = _make_approval_request(base_dir, report, candidate)
    _write_json(out_dir / APPROVAL_REQUEST_JSON, approval_request)
    (out_dir / APPROVAL_REQUEST_MD).write_text(
        render_approval_request_markdown(approval_request),
        encoding="utf-8",
    )

    decision = _approval_decision(
        approval,
        Path(approval_file) if approval_file else None,
        candidate,
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

    topology = _read_json(base_dir / "topology.json")
    if not isinstance(topology, dict):
        raise FileNotFoundError(f"Missing topology.json under {base_dir}")
    before_metrics = _simple_metrics(topology)
    corrected_topology = _apply_candidate(topology, candidate)
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
        "candidate": {
            "candidate_id": candidate.get("candidate_id"),
            "repair_type": candidate.get("repair_type"),
            "target_nodes": candidate.get("target_nodes", []),
            "target_pins": candidate.get("target_pins", []),
            "recommendation": candidate.get("recommendation"),
            "validation_result": candidate.get("validation_result"),
            "ranking_score": candidate.get("ranking_score"),
        },
        "before_metrics": before_metrics,
        "after_metrics": after_metrics,
        "expected_after_metrics": candidate.get("after_metrics", {}),
        "topology_mutated_in_place": False,
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
