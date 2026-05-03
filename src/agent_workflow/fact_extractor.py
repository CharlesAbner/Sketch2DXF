"""Extract compact deterministic facts from audit artifacts."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.agent_workflow.schemas import EvidenceItem


POWER_CLASSES = {
    "power_source",
    "voltage_source",
    "voltage.ac",
    "voltage.dc",
    "voltage.battery",
    "battery",
    "source",
}
GROUND_CLASSES = {"gnd", "ground", "vss"}


def _class_name(component: dict) -> str:
    return str(component.get("class_name", "")).lower()


def _is_power(component: dict) -> bool:
    class_name = _class_name(component)
    return class_name in POWER_CLASSES or "voltage" in class_name or "battery" in class_name


def _is_ground(component: dict) -> bool:
    class_name = _class_name(component)
    return class_name in GROUND_CLASSES or class_name.endswith(".gnd")


def _severity_counts(items: list[dict]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for item in items:
        severity = item.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def _component_net_lookup(topology: dict) -> dict[str, dict]:
    return {
        component_net.get("component_id"): component_net
        for component_net in topology.get("component_nets", [])
    }


def _pin_records(audit_inputs: dict, topology: dict) -> list[dict]:
    audit_pins = audit_inputs.get("pins", [])
    if audit_pins:
        return audit_pins

    records = []
    net_by_pin = {}
    for net in topology.get("nets", []):
        for pin in net.get("pins", []):
            net_by_pin[pin.get("pin_id")] = net.get("net_id")
    for group in topology.get("pins", []):
        for pin in group.get("pins", []):
            records.append(
                {
                    **pin,
                    "component_id": group.get("component_id"),
                    "net_id": net_by_pin.get(pin.get("pin_id")),
                }
            )
    return records


def _component_ref(component: dict) -> str:
    return str(component.get("refdes") or component.get("id") or component.get("component_id"))


def _component_graph(component_nets: list[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    component_to_nets: dict[str, set[str]] = {}
    net_to_components: dict[str, set[str]] = defaultdict(set)
    for component in component_nets:
        component_id = component.get("component_id")
        net_ids = [net_id for net_id in component.get("net_ids", []) if net_id]
        component_to_nets[component_id] = set(net_ids)
        for net_id in net_ids:
            net_to_components[net_id].add(component_id)
    return component_to_nets, net_to_components


def _connected_groups(component_nets: list[dict]) -> list[list[str]]:
    component_to_nets, net_to_components = _component_graph(component_nets)
    visited_components: set[str] = set()
    groups: list[list[str]] = []
    for component_id in sorted(component_to_nets):
        if component_id in visited_components:
            continue
        stack = [component_id]
        visited_components.add(component_id)
        group = []
        while stack:
            current_component = stack.pop()
            group.append(current_component)
            for net_id in component_to_nets.get(current_component, set()):
                for neighbor_component in net_to_components.get(net_id, set()):
                    if neighbor_component in visited_components:
                        continue
                    visited_components.add(neighbor_component)
                    stack.append(neighbor_component)
        groups.append(sorted(group))
    return groups


def extract_case_facts(loaded: dict[str, Any]) -> dict[str, Any]:
    """Turn full artifacts into a compact fact package for diagnosis."""
    artifacts = loaded.get("artifacts", {})
    case_summary = artifacts.get("case_summary", {})
    audit_inputs = artifacts.get("audit_inputs", {})
    repair_candidates = artifacts.get("repair_candidates", {})
    topology = artifacts.get("topology", {})
    terminal_attachments = artifacts.get("terminal_attachments", {})
    supported_graph = artifacts.get("supported_graph", {})
    graph_nodes = artifacts.get("graph_nodes_dry_run", {})
    node_selection = artifacts.get("node_selection", {})

    summary = case_summary.get("summary", audit_inputs.get("summary", {}))
    components = topology.get("components", audit_inputs.get("components", []))
    nets = topology.get("nets", audit_inputs.get("nets", []))
    component_nets = topology.get("component_nets", [])
    pins = _pin_records(audit_inputs, topology)

    unmatched_pins = [pin.get("pin_id") for pin in pins if not pin.get("net_id") and not pin.get("node_id")]
    single_pin_nets = [net.get("net_id") for net in nets if int(net.get("pin_count", 0)) == 1]
    zero_pin_nets = [net.get("net_id") for net in nets if int(net.get("pin_count", 0)) == 0]
    connected_groups = _connected_groups(component_nets)
    component_lookup = {component.get("id"): component for component in components}
    component_net_lookup = _component_net_lookup(topology)

    self_shorts = []
    source_shorts = []
    for component_id, component_net in component_net_lookup.items():
        net_ids = [net_id for net_id in component_net.get("net_ids", []) if net_id]
        duplicate_nets = sorted({net_id for net_id in net_ids if net_ids.count(net_id) > 1})
        if not duplicate_nets:
            continue
        component = component_lookup.get(component_id, {})
        record = {
            "component_id": component_id,
            "refdes": component.get("refdes", component_net.get("refdes")),
            "class_name": component.get("class_name", component_net.get("class_name")),
            "net_ids": duplicate_nets,
        }
        self_shorts.append(record)
        if _is_power(component):
            source_shorts.append(record)

    risk_flags = audit_inputs.get("risk_flags", [])
    repair_items = repair_candidates.get("candidates", [])
    strict_low_confidence_matches = [
        flag for flag in risk_flags if flag.get("code") == "low_confidence_match"
    ]
    weak_confidence_matches = [
        flag for flag in risk_flags if flag.get("code") == "weak_confidence_match"
    ]
    isolated_net_risks = [flag for flag in risk_flags if flag.get("code") == "isolated_net"]

    facts = {
        "case_id": loaded.get("case_id"),
        "image_path": loaded.get("image_path"),
        "known_stressors": loaded.get("known_stressors", []),
        "core_artifacts_ok": bool(loaded.get("core_artifacts_ok", False)),
        "missing_required_artifacts": loaded.get("missing_required_artifacts", []),
        "missing_artifacts": loaded.get("missing_artifacts", []),
        "summary": summary,
        "component_count": int(summary.get("component_count", len(components))),
        "pin_count": int(summary.get("pin_count", len(pins))),
        "node_count": int(summary.get("node_count", len(topology.get("nodes", [])))),
        "net_count": int(summary.get("net_count", len(nets))),
        "connection_count": int(summary.get("connection_count", len(topology.get("connections", [])))),
        "has_power_source": any(_is_power(component) for component in components),
        "has_ground": any(_is_ground(component) for component in components),
        "unmatched_pin_ids": [pin_id for pin_id in unmatched_pins if pin_id],
        "single_pin_nets": [net_id for net_id in single_pin_nets if net_id],
        "zero_pin_nets": [net_id for net_id in zero_pin_nets if net_id],
        "connected_component_groups": connected_groups,
        "component_self_shorts": self_shorts,
        "source_terminal_shorts": source_shorts,
        "risk_counts": _severity_counts(risk_flags),
        "repair_counts": _severity_counts(repair_items),
        "risk_flags": risk_flags,
        "repair_candidates": repair_items,
        "low_confidence_matches": strict_low_confidence_matches,
        "weak_confidence_matches": weak_confidence_matches,
        "isolated_net_risks": isolated_net_risks,
        "wire_stats": audit_inputs.get("evidence_summary", {}).get("wire_stats", {}),
        "evidence_graph_stats": audit_inputs.get("evidence_summary", {}).get("evidence_graph_stats", {}),
        "supported_graph_stats": audit_inputs.get("evidence_summary", {}).get("supported_graph_stats", {}),
        "terminal_attachment_stats": terminal_attachments.get("stats", {}),
        "supported_graph_stats_raw": supported_graph.get("stats", {}),
        "graph_nodes_stats": graph_nodes.get("stats", {}),
        "node_selection": node_selection,
        "artifact_paths": loaded.get("artifact_paths", {}),
    }
    facts["evidence_items"] = _build_evidence_items(facts)
    return facts


def _build_evidence_items(facts: dict[str, Any]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    if not facts.get("core_artifacts_ok"):
        items.append(
            EvidenceItem(
                code="insufficient_artifacts",
                message=(
                    "Required debug artifacts are missing: "
                    f"{', '.join(facts.get('missing_required_artifacts', []))}."
                ),
                refs={"missing_required_artifacts": facts.get("missing_required_artifacts", [])},
            )
        )
    summary = facts.get("summary", {})
    if summary:
        items.append(
            EvidenceItem(
                code="case_summary",
                message=(
                    f"quality={summary.get('quality_label')}, "
                    f"nodes={facts['node_count']}, nets={facts['net_count']}, "
                    f"connections={facts['connection_count']}"
                ),
            )
        )
    if facts.get("single_pin_nets"):
        items.append(
            EvidenceItem(
                code="single_pin_nets",
                message=f"Single-pin nets: {', '.join(facts['single_pin_nets'])}.",
                refs={"net_ids": facts["single_pin_nets"]},
            )
        )
    if facts.get("unmatched_pin_ids"):
        items.append(
            EvidenceItem(
                code="unmatched_pins",
                message=f"Unmatched pins: {', '.join(facts['unmatched_pin_ids'])}.",
                refs={"pin_ids": facts["unmatched_pin_ids"]},
            )
        )
    if facts.get("low_confidence_matches"):
        items.append(
            EvidenceItem(
                code="low_confidence_matches",
                message=f"{len(facts['low_confidence_matches'])} low-confidence match risk(s).",
                refs={
                    "pin_ids": [
                        flag.get("refs", {}).get("pin_id")
                        for flag in facts["low_confidence_matches"]
                    ]
                },
            )
        )
    if facts.get("weak_confidence_matches"):
        items.append(
            EvidenceItem(
                code="weak_confidence_matches",
                message=f"{len(facts['weak_confidence_matches'])} weak-confidence match info item(s).",
                refs={
                    "pin_ids": [
                        flag.get("refs", {}).get("pin_id")
                        for flag in facts["weak_confidence_matches"]
                    ]
                },
            )
        )
    unsupported_count = int(facts.get("supported_graph_stats", {}).get("unsupported_raw_component_count", 0))
    if unsupported_count:
        items.append(
            EvidenceItem(
                code="unsupported_evidence",
                message=f"{unsupported_count} unsupported raw evidence component(s) were discarded.",
            )
        )
    if facts.get("node_selection", {}).get("fallback_used"):
        items.append(
            EvidenceItem(
                code="fallback_used",
                message="Graph-derived node selection fell back to legacy nodes.",
                refs={"fallback_reasons": facts.get("node_selection", {}).get("fallback_reasons", [])},
            )
        )
    if "slanted" in " ".join(facts.get("known_stressors", [])).lower():
        items.append(
            EvidenceItem(
                code="known_slanted_stressor",
                message="The case is tagged as a slanted-wire stress case.",
            )
        )
    if not facts.get("has_power_source"):
        items.append(
            EvidenceItem(
                code="missing_power_source",
                message="No power-source component was found in the recovered topology.",
            )
        )
    return items
