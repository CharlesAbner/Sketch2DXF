"""Build a compact audit-ready semantic package for humans and agents."""

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


def _net_by_pin(topology_result: dict) -> dict[str, str]:
    net_by_pin = {}
    for net in topology_result.get("nets", []):
        for pin in net.get("pins", []):
            net_by_pin[pin["pin_id"]] = net["net_id"]
    return net_by_pin


def _component_lookup(topology_result: dict) -> dict[str, dict]:
    return {component["id"]: component for component in topology_result.get("components", [])}


def _summarize_components(topology_result: dict, pin_result: dict, proposal_result: dict) -> list[dict]:
    proposal_by_id = {proposal["id"]: proposal for proposal in proposal_result.get("proposals", [])}
    net_by_pin = _net_by_pin(topology_result)
    pins_by_component: dict[str, list[dict]] = {}
    for pin in _all_pins(pin_result):
        pins_by_component.setdefault(pin["component_id"], []).append(
            {
                "pin_id": pin["pin_id"],
                "side": pin.get("side"),
                "x": int(pin["x"]),
                "y": int(pin["y"]),
                "net_id": net_by_pin.get(pin["pin_id"]),
            }
        )

    components = []
    for component in topology_result.get("components", []):
        proposal = proposal_by_id.get(component["id"], {})
        components.append(
            {
                "component_id": component["id"],
                "refdes": component.get("refdes"),
                "class_name": component.get("class_name"),
                "class_candidates": component.get("class_candidates", []),
                "class_alternatives": component.get("class_alternatives", []),
                "bbox": component.get("bbox"),
                "score": proposal.get("score", component.get("score")),
                "source": proposal.get("source", component.get("source")),
                "pins": pins_by_component.get(component["id"], []),
            }
        )
    return components


def _summarize_pins(pin_result: dict, match_result: dict, terminal_attachments: dict, topology_result: dict) -> list[dict]:
    matches = _match_by_pin(match_result)
    attachments = _attachment_by_pin(terminal_attachments)
    net_by_pin = _net_by_pin(topology_result)
    pin_records = []
    for pin in _all_pins(pin_result):
        match = matches.get(pin["pin_id"])
        attachment = attachments.get(pin["pin_id"], {})
        pin_records.append(
            {
                "pin_id": pin["pin_id"],
                "component_id": pin["component_id"],
                "side": pin.get("side"),
                "x": int(pin["x"]),
                "y": int(pin["y"]),
                "net_id": net_by_pin.get(pin["pin_id"]),
                "node_id": match.get("node_id") if match else None,
                "match_confidence": match.get("confidence") if match else None,
                "match_type": match.get("match_type") if match else None,
                "match_evidence_type": match.get("evidence_type") if match else None,
                "match_evidence_id": match.get("evidence_id") if match else None,
                "best_raw_component_id": attachment.get("best_raw_component_id"),
                "best_attachment_score": attachment.get("best_attachment_score"),
                "best_evidence_kind": attachment.get("best_evidence_kind"),
                "best_evidence_id": attachment.get("best_evidence_id"),
                "attachment_candidate_count": attachment.get("candidate_count", 0),
            }
        )
    return pin_records


def _node_explanation(node: dict) -> str:
    status = node.get("support_status")
    if status == "terminal_supported_with_relay":
        return "Terminal-supported evidence connected through relay raw components."
    if status == "terminal_supported_merged":
        return "Multiple terminal-supported evidence components were merged into one electrical node."
    if status == "terminal_supported":
        return "Electrical node directly supported by terminal attachments."
    if status == "relay_only":
        return "Relay evidence without direct terminal support."
    return f"Node selected with support status: {status}."


def _summarize_nodes(node_result: dict) -> list[dict]:
    nodes = []
    for node in node_result.get("nodes", []):
        nodes.append(
            {
                "node_id": node["node_id"],
                "source": node.get("source"),
                "support_status": node.get("support_status"),
                "terminal_support_count": node.get("terminal_support_count", 0),
                "pin_ids": node.get("pin_ids", []),
                "component_ids": node.get("component_ids", []),
                "raw_component_ids": node.get("raw_component_ids", node.get("merged_raw_node_ids", [])),
                "relay_raw_component_ids": node.get("relay_raw_component_ids", []),
                "bridge_candidate_ids": node.get("bridge_candidate_ids", []),
                "segment_ids": node.get("segment_ids", []),
                "bbox": node.get("bbox"),
                "explanation": _node_explanation(node),
            }
        )
    return nodes


def _summarize_nets(topology_result: dict) -> list[dict]:
    return [
        {
            "net_id": net["net_id"],
            "node_id": net.get("node_id"),
            "pin_count": net.get("pin_count", 0),
            "pin_refs": net.get("pin_refs", []),
            "component_refs": net.get("component_refs", []),
            "component_ids": net.get("component_ids", []),
            "bbox": net.get("bbox"),
        }
        for net in topology_result.get("nets", [])
    ]


def _summarize_evidence(wire_result: dict, evidence_graph: dict, supported_graph: dict) -> dict:
    unsupported_components = [
        {
            "raw_component_id": component["raw_component_id"],
            "segment_ids": component.get("segment_ids", []),
            "bbox": component.get("bbox"),
            "support_status": component.get("support_status"),
        }
        for component in supported_graph.get("raw_components", [])
        if component.get("support_status") == "unsupported"
    ]
    relay_components = [
        {
            "raw_component_id": component["raw_component_id"],
            "segment_ids": component.get("segment_ids", []),
            "relay_neighbor_component_ids": component.get("relay_neighbor_component_ids", []),
            "relay_bridge_candidate_ids": component.get("relay_bridge_candidate_ids", []),
        }
        for component in supported_graph.get("raw_components", [])
        if component.get("support_status") == "relay_supported"
    ]
    supported_bridges = [
        {
            "bridge_candidate_id": bridge["bridge_candidate_id"],
            "candidate_type": bridge.get("candidate_type"),
            "from_component_id": bridge.get("from_component_id"),
            "to_component_id": bridge.get("to_component_id"),
            "support_status": bridge.get("support_status"),
            "distance": bridge.get("distance"),
        }
        for bridge in supported_graph.get("bridge_candidates", [])
        if bridge.get("support_status") != "unsupported"
    ]
    return {
        "wire_stats": wire_result.get("stats", {}),
        "evidence_graph_stats": evidence_graph.get("stats", {}),
        "supported_graph_stats": supported_graph.get("stats", {}),
        "unsupported_raw_components": unsupported_components,
        "relay_raw_components": relay_components,
        "supported_bridge_candidates": supported_bridges,
    }


def _risk(code: str, severity: str, message: str, refs: dict | None = None) -> dict:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "refs": refs or {},
    }


def _risk_flags(
    proposal_result: dict,
    pin_result: dict,
    match_result: dict,
    node_selection: dict,
    supported_graph: dict,
    graph_nodes_dry_run: dict,
    topology_result: dict,
    consistency_result: dict,
    export_result: dict,
    config: dict,
) -> list[dict]:
    audit_cfg = config.get("audit", {})
    low_confidence_threshold = float(audit_cfg.get("low_confidence_match_threshold", 0.6))
    weak_confidence_threshold = float(audit_cfg.get("weak_confidence_match_threshold", 0.75))
    flags = []

    suppressed_duplicate_count = int(proposal_result.get("stats", {}).get("suppressed_duplicate_count", 0))
    if suppressed_duplicate_count > 0:
        flags.append(
            _risk(
                "duplicate_component_suppressed",
                "info",
                f"{suppressed_duplicate_count} overlapping component proposal(s) were suppressed.",
                {"suppressed_duplicates": proposal_result.get("stats", {}).get("suppressed_duplicates", [])},
            )
        )

    if node_selection.get("fallback_used"):
        flags.append(
            _risk(
                "fallback_used",
                "warning",
                "Graph-derived nodes were rejected and legacy nodes were used.",
                {"fallback_reasons": node_selection.get("fallback_reasons", [])},
            )
        )

    diff_stats = graph_nodes_dry_run.get("node_diff", {}).get("stats", {})
    if any(int(diff_stats.get(key, 0)) > 0 for key in ("partial_match_count", "graph_only_count", "current_only_count")):
        flags.append(
            _risk(
                "graph_legacy_diff",
                "warning",
                "Graph-derived node dry-run differs from the legacy node result.",
                {"node_diff_stats": diff_stats},
            )
        )

    matches = match_result.get("matches", [])
    for match in matches:
        confidence = float(match.get("confidence", 0.0))
        if confidence < low_confidence_threshold:
            flags.append(
                _risk(
                    "low_confidence_match",
                    "warning",
                    f"Pin {match['pin_id']} matched node {match['node_id']} with low confidence {confidence:.3f}.",
                    {
                        "pin_id": match["pin_id"],
                        "node_id": match["node_id"],
                        "evidence_type": match.get("evidence_type"),
                        "evidence_id": match.get("evidence_id"),
                    },
                )
            )
        elif confidence < weak_confidence_threshold:
            flags.append(
                _risk(
                    "weak_confidence_match",
                    "info",
                    f"Pin {match['pin_id']} matched node {match['node_id']} with moderate confidence {confidence:.3f}.",
                    {"pin_id": match["pin_id"], "node_id": match["node_id"]},
                )
            )

    matched_pin_ids = {match["pin_id"] for match in matches}
    for pin in _all_pins(pin_result):
        if pin["pin_id"] not in matched_pin_ids:
            flags.append(
                _risk(
                    "unmatched_pin",
                    "error",
                    f"Pin {pin['pin_id']} has no recovered node connection.",
                    {"pin_id": pin["pin_id"], "component_id": pin["component_id"]},
                )
            )

    unsupported_components = [
        component
        for component in supported_graph.get("raw_components", [])
        if component.get("support_status") == "unsupported"
    ]
    if unsupported_components:
        flags.append(
            _risk(
                "unsupported_evidence",
                "info",
                f"{len(unsupported_components)} raw evidence component(s) were discarded as unsupported.",
                {"raw_component_ids": [component["raw_component_id"] for component in unsupported_components]},
            )
        )

    relay_nodes = [
        node
        for node in topology_result.get("nodes", [])
        if node.get("support_status") == "terminal_supported_with_relay"
    ]
    for node in relay_nodes:
        flags.append(
            _risk(
                "relay_supported_node",
                "info",
                f"Node {node['node_id']} uses relay evidence between terminal-supported components.",
                {"node_id": node["node_id"], "relay_raw_component_ids": node.get("relay_raw_component_ids", [])},
            )
        )

    isolated_nets = [net for net in topology_result.get("nets", []) if int(net.get("pin_count", 0)) < 2]
    if isolated_nets:
        flags.append(
            _risk(
                "isolated_net",
                "warning",
                f"{len(isolated_nets)} net(s) have fewer than two pins.",
                {"net_ids": [net["net_id"] for net in isolated_nets]},
            )
        )

    for warning in consistency_result.get("warnings", []):
        flags.append(_risk("consistency_warning", "warning", warning))
    for error in consistency_result.get("errors", []):
        flags.append(_risk("consistency_error", "error", error))

    if not export_result.get("export_success", False):
        flags.append(
            _risk(
                "export_failed",
                "error",
                "DXF/netlist export failed.",
                {"export_errors": export_result.get("export_errors", [])},
            )
        )

    return flags


def _severity_counts(risk_flags: list[dict]) -> dict:
    counts = Counter(flag["severity"] for flag in risk_flags)
    return {
        "error": counts.get("error", 0),
        "warning": counts.get("warning", 0),
        "info": counts.get("info", 0),
    }


def _quality_label(consistency_result: dict, risk_flags: list[dict], node_selection: dict) -> str:
    counts = _severity_counts(risk_flags)
    if counts["error"] > 0 or float(consistency_result.get("consistency_score", 0.0)) < 0.75:
        return "fail"
    if node_selection.get("fallback_used") or counts["warning"] > 0:
        return "pass_with_warnings"
    return "pass"


def build_audit_inputs(
    proposal_result: dict,
    pin_result: dict,
    wire_result: dict,
    evidence_graph: dict,
    terminal_attachments: dict,
    supported_graph: dict,
    graph_nodes_dry_run: dict,
    node_selection: dict,
    node_result: dict,
    match_result: dict,
    topology_result: dict,
    consistency_result: dict,
    export_result: dict,
    config: dict,
) -> dict:
    """Build a compact semantic package for audit agents and human review."""
    risk_flags = _risk_flags(
        proposal_result,
        pin_result,
        match_result,
        node_selection,
        supported_graph,
        graph_nodes_dry_run,
        topology_result,
        consistency_result,
        export_result,
        config,
    )
    severity_counts = _severity_counts(risk_flags)
    return {
        "schema_version": "2.0-audit",
        "summary": {
            "quality_label": _quality_label(consistency_result, risk_flags, node_selection),
            "component_count": len(topology_result.get("components", [])),
            "pin_count": sum(len(group.get("pins", [])) for group in pin_result.get("pins", [])),
            "connection_count": len(topology_result.get("connections", [])),
            "node_count": len(topology_result.get("nodes", [])),
            "net_count": len(topology_result.get("nets", [])),
            "wire_segment_count": len(wire_result.get("segments", [])),
            "raw_component_count": supported_graph.get("stats", {}).get("raw_component_count", 0),
            "supported_raw_component_count": supported_graph.get("stats", {}).get(
                "supported_raw_component_count",
                0,
            ),
            "relay_supported_raw_component_count": supported_graph.get("stats", {}).get(
                "relay_supported_raw_component_count",
                0,
            ),
            "unsupported_raw_component_count": supported_graph.get("stats", {}).get(
                "unsupported_raw_component_count",
                0,
            ),
            "selected_node_source": node_selection.get("selected_node_source"),
            "fallback_used": node_selection.get("fallback_used"),
            "consistency_score": consistency_result.get("consistency_score"),
            "needs_repair": consistency_result.get("needs_repair"),
            "export_success": export_result.get("export_success"),
            "risk_counts": severity_counts,
        },
        "components": _summarize_components(topology_result, pin_result, proposal_result),
        "pins": _summarize_pins(pin_result, match_result, terminal_attachments, topology_result),
        "nodes": _summarize_nodes(node_result),
        "nets": _summarize_nets(topology_result),
        "evidence_summary": _summarize_evidence(wire_result, evidence_graph, supported_graph),
        "node_selection": node_selection,
        "node_diff": graph_nodes_dry_run.get("node_diff", {}),
        "validation": consistency_result,
        "export": {
            "export_success": export_result.get("export_success"),
            "dxf_path": export_result.get("dxf_path"),
            "json_path": export_result.get("json_path"),
            "export_errors": export_result.get("export_errors", []),
        },
        "risk_flags": risk_flags,
    }
