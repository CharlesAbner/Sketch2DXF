"""Select graph-derived nodes for the main chain with legacy fallback."""

from __future__ import annotations

from src.topology.component_node_matcher import match_components_to_nodes
from src.topology.consistency_rules import check_topology_consistency
from src.topology.topology_builder import build_topology


ACCEPTED_DIFF_STATUSES = {"exact_match", "near_match"}


def _id_sort_key(item_id: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(item_id) if ch.isdigit())
    return (int(digits) if digits else 10**9, str(item_id))


def _next_node_id(used_node_ids: set[str], index: int) -> str:
    candidate_index = index
    while True:
        node_id = f"N{candidate_index}"
        if node_id not in used_node_ids:
            used_node_ids.add(node_id)
            return node_id
        candidate_index += 1


def _node_ids_from_diff(graph_nodes_dry_run: dict) -> dict[str, str]:
    graph_to_current: dict[str, str] = {}
    used_node_ids: set[str] = set()
    for comparison in graph_nodes_dry_run.get("node_diff", {}).get("comparisons", []):
        current_node_id = comparison.get("current_node_id")
        if comparison.get("status") in ACCEPTED_DIFF_STATUSES and current_node_id:
            graph_to_current[comparison["graph_node_id"]] = current_node_id
            used_node_ids.add(current_node_id)

    next_index = 1
    for graph_node in graph_nodes_dry_run.get("graph_nodes", []):
        graph_node_id = graph_node["graph_node_id"]
        if graph_node_id in graph_to_current:
            continue
        graph_to_current[graph_node_id] = _next_node_id(used_node_ids, next_index)
        next_index += 1
    return graph_to_current


def _graph_node_confidence(graph_node: dict) -> float:
    if graph_node.get("support_status") == "terminal_supported_with_relay":
        return 0.82
    if graph_node.get("support_status") in {"terminal_supported", "terminal_supported_merged"}:
        return 0.86
    if graph_node.get("support_status") == "relay_only":
        return 0.55
    return 0.4


def _graph_node_to_node(graph_node: dict, node_id: str) -> dict:
    return {
        "node_id": node_id,
        "graph_node_id": graph_node["graph_node_id"],
        "x": int(graph_node["x"]),
        "y": int(graph_node["y"]),
        "type": "electrical",
        "source": "graph_derived",
        "support_status": graph_node.get("support_status", "terminal_supported"),
        "terminal_support_count": int(graph_node.get("terminal_support_count", 0)),
        "pin_ids": graph_node.get("pin_ids", []),
        "component_ids": graph_node.get("component_ids", []),
        "node_confidence": _graph_node_confidence(graph_node),
        "best_match_confidence": 0.0,
        "discard_reason": None,
        "keep_reasons": ["graph_supported"],
        "noise_flags": [],
        "raw_component_ids": graph_node.get("raw_component_ids", []),
        "relay_raw_component_ids": graph_node.get("relay_raw_component_ids", []),
        "merged_raw_node_ids": graph_node.get("raw_component_ids", []),
        "bridge_candidate_ids": graph_node.get("bridge_candidate_ids", []),
        "bridge_connections": graph_node.get("bridge_connections", []),
        "members": [],
        "points": [],
        "segment_ids": graph_node.get("segment_ids", []),
        "edge_ids": graph_node.get("edge_ids", []),
        "vertex_ids": graph_node.get("vertex_ids", []),
        "endpoint_ids": graph_node.get("endpoint_ids", []),
        "junction_ids": graph_node.get("junction_ids", []),
        "bbox": graph_node.get("bbox"),
    }


def _build_graph_node_result(graph_nodes_dry_run: dict, legacy_node_result: dict) -> dict:
    graph_to_node_id = _node_ids_from_diff(graph_nodes_dry_run)
    nodes = [
        _graph_node_to_node(graph_node, graph_to_node_id[graph_node["graph_node_id"]])
        for graph_node in graph_nodes_dry_run.get("graph_nodes", [])
    ]
    raw_component_to_node_id = {
        raw_component_id: node["node_id"]
        for node in nodes
        for raw_component_id in node.get("raw_component_ids", [])
    }
    discarded_nodes = [
        {
            "node_id": f"DISCARDED_{component['raw_component_id']}",
            "raw_component_id": component["raw_component_id"],
            "x": int((component.get("bbox", [0, 0, 0, 0])[0] + component.get("bbox", [0, 0, 0, 0])[2]) / 2),
            "y": int((component.get("bbox", [0, 0, 0, 0])[1] + component.get("bbox", [0, 0, 0, 0])[3]) / 2),
            "type": "electrical",
            "source": "graph_derived",
            "support_status": "discarded",
            "terminal_support_count": 0,
            "pin_ids": [],
            "component_ids": [],
            "discard_reason": "unsupported_graph_component",
            "segment_ids": component.get("segment_ids", []),
            "endpoint_ids": component.get("endpoint_ids", []),
            "junction_ids": component.get("junction_ids", []),
            "bbox": component.get("bbox"),
        }
        for component in graph_nodes_dry_run.get("discarded_raw_components", [])
    ]
    return {
        "nodes": nodes,
        "raw_nodes": legacy_node_result.get("raw_nodes", []),
        "discarded_nodes": discarded_nodes,
        "node_id_map": raw_component_to_node_id,
        "bridge_connections": graph_nodes_dry_run.get("used_bridge_candidates", []),
        "graph_nodes": graph_nodes_dry_run.get("graph_nodes", []),
        "stats": {
            **legacy_node_result.get("stats", {}),
            "node_source": "graph_derived",
            "node_count": len(nodes),
            "active_node_count": len(nodes),
            "discarded_node_count": len(discarded_nodes),
            "graph_node_count": len(nodes),
            "legacy_node_count": len(legacy_node_result.get("nodes", [])),
            "used_bridge_candidate_count": len(graph_nodes_dry_run.get("used_bridge_candidates", [])),
            "fallback_used": False,
        },
    }


def _diff_rejection_reasons(graph_nodes_dry_run: dict, min_match_ratio: float) -> list[str]:
    node_diff = graph_nodes_dry_run.get("node_diff", {})
    comparisons = node_diff.get("comparisons", [])
    stats = node_diff.get("stats", {})
    if not graph_nodes_dry_run.get("graph_nodes"):
        return ["graph_nodes_empty"]
    accepted_count = sum(1 for comparison in comparisons if comparison.get("status") in ACCEPTED_DIFF_STATUSES)
    graph_node_count = int(stats.get("graph_node_count", len(comparisons)))
    if graph_node_count > 0 and accepted_count / graph_node_count < min_match_ratio:
        return ["dry_run_diff_below_threshold"]
    if int(stats.get("graph_only_count", 0)) > 0:
        return ["dry_run_has_graph_only_nodes"]
    if int(stats.get("current_only_count", 0)) > 0:
        return ["dry_run_has_current_only_nodes"]
    if int(stats.get("partial_match_count", 0)) > 0:
        return ["dry_run_has_partial_matches"]
    return []


def _candidate_rejection_reasons(
    graph_nodes_dry_run: dict,
    legacy_match_result: dict,
    graph_match_result: dict,
    graph_consistency_result: dict,
    legacy_consistency_result: dict,
    config: dict,
) -> list[str]:
    topology_cfg = config["topology"]
    min_diff_match_ratio = float(topology_cfg.get("graph_nodes_min_diff_match_ratio", 1.0))
    min_match_count_ratio = float(topology_cfg.get("graph_nodes_min_match_count_ratio", 1.0))
    fallback_on_repair = bool(topology_cfg.get("graph_nodes_fallback_on_repair", True))
    reasons = _diff_rejection_reasons(graph_nodes_dry_run, min_diff_match_ratio)

    legacy_match_count = int(legacy_match_result.get("stats", {}).get("match_count", len(legacy_match_result.get("matches", []))))
    graph_match_count = int(graph_match_result.get("stats", {}).get("match_count", len(graph_match_result.get("matches", []))))
    if legacy_match_count > 0 and graph_match_count < legacy_match_count * min_match_count_ratio:
        reasons.append("graph_match_count_dropped")
    if graph_consistency_result.get("errors"):
        reasons.append("graph_topology_has_errors")
    if (
        fallback_on_repair
        and graph_consistency_result.get("needs_repair")
        and not legacy_consistency_result.get("needs_repair")
    ):
        reasons.append("graph_topology_needs_repair")
    return reasons


def _selection_metadata(
    selected_source: str,
    fallback_used: bool,
    fallback_reasons: list[str],
    graph_nodes_dry_run: dict,
    legacy_match_result: dict,
    graph_match_result: dict,
    legacy_consistency_result: dict,
    graph_consistency_result: dict,
) -> dict:
    return {
        "selected_node_source": selected_source,
        "fallback_used": fallback_used,
        "fallback_reasons": fallback_reasons,
        "graph_node_count": len(graph_nodes_dry_run.get("graph_nodes", [])),
        "legacy_match_count": int(
            legacy_match_result.get("stats", {}).get("match_count", len(legacy_match_result.get("matches", [])))
        ),
        "graph_match_count": int(
            graph_match_result.get("stats", {}).get("match_count", len(graph_match_result.get("matches", [])))
        ),
        "legacy_consistency_score": legacy_consistency_result.get("consistency_score"),
        "graph_consistency_score": graph_consistency_result.get("consistency_score"),
        "node_diff_stats": graph_nodes_dry_run.get("node_diff", {}).get("stats", {}),
    }


def select_node_result_with_graph_fallback(
    graph_nodes_dry_run: dict,
    legacy_node_result: dict,
    legacy_match_result: dict,
    perception_result: dict,
    pin_result: dict,
    wire_result: dict,
    junction_result: dict,
    config: dict,
) -> dict:
    """Prefer graph-derived nodes, falling back to legacy nodes when checks fail."""
    topology_cfg = config["topology"]
    use_graph_nodes = bool(topology_cfg.get("use_graph_derived_nodes", True))
    enable_fallback = bool(topology_cfg.get("graph_nodes_enable_fallback", True))

    legacy_topology_result = build_topology(perception_result, pin_result, legacy_node_result, legacy_match_result)
    legacy_consistency_result = check_topology_consistency(legacy_topology_result, config)
    graph_node_result = _build_graph_node_result(graph_nodes_dry_run, legacy_node_result)
    graph_match_result = match_components_to_nodes(
        pin_result,
        graph_node_result,
        wire_result,
        junction_result,
        config,
    )
    graph_topology_result = build_topology(perception_result, pin_result, graph_node_result, graph_match_result)
    graph_consistency_result = check_topology_consistency(graph_topology_result, config)

    fallback_reasons = []
    if not use_graph_nodes:
        fallback_reasons.append("graph_derived_nodes_disabled")
    fallback_reasons.extend(
        _candidate_rejection_reasons(
            graph_nodes_dry_run,
            legacy_match_result,
            graph_match_result,
            graph_consistency_result,
            legacy_consistency_result,
            config,
        )
    )

    should_fallback = (not use_graph_nodes) or (bool(fallback_reasons) and enable_fallback)
    if should_fallback:
        selected_source = "legacy"
        selected_node_result = {
            **legacy_node_result,
            "stats": {
                **legacy_node_result.get("stats", {}),
                "node_source": "legacy",
                "fallback_used": True,
                "fallback_reasons": fallback_reasons,
            },
        }
        selected_match_result = legacy_match_result
        selected_topology_result = legacy_topology_result
        selected_consistency_result = legacy_consistency_result
    else:
        selected_source = "graph_derived"
        selected_node_result = {
            **graph_node_result,
            "stats": {
                **graph_node_result.get("stats", {}),
                "node_source": "graph_derived",
                "fallback_used": False,
                "fallback_reasons": fallback_reasons,
            },
        }
        selected_match_result = graph_match_result
        selected_topology_result = graph_topology_result
        selected_consistency_result = graph_consistency_result

    selection = _selection_metadata(
        selected_source,
        should_fallback,
        fallback_reasons,
        graph_nodes_dry_run,
        legacy_match_result,
        graph_match_result,
        legacy_consistency_result,
        graph_consistency_result,
    )
    return {
        "node_result": selected_node_result,
        "match_result": selected_match_result,
        "topology_result": selected_topology_result,
        "consistency_result": selected_consistency_result,
        "selection": selection,
        "legacy": {
            "node_result": legacy_node_result,
            "match_result": legacy_match_result,
            "topology_result": legacy_topology_result,
            "consistency_result": legacy_consistency_result,
        },
        "graph_candidate": {
            "node_result": graph_node_result,
            "match_result": graph_match_result,
            "topology_result": graph_topology_result,
            "consistency_result": graph_consistency_result,
        },
    }
