"""Dry-run graph-derived electrical nodes for audit and comparison.

The output from this module is not used by topology_builder. It lets us compare
supported-graph nodes against the current production nodes before switching any
main-chain behavior.
"""

from __future__ import annotations


GRAPH_BRIDGE_STATUSES = {
    "between_best_supported_components",
    "between_supported_components",
    "between_path_supported_components",
}


def _unique(items: list) -> list:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _id_sort_key(item_id: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(item_id) if ch.isdigit())
    return (int(digits) if digits else 10**9, str(item_id))


def _bbox_union(bboxes: list[list[int] | None]) -> list[int] | None:
    valid_bboxes = [bbox for bbox in bboxes if bbox and len(bbox) == 4]
    if not valid_bboxes:
        return None
    return [
        min(int(bbox[0]) for bbox in valid_bboxes),
        min(int(bbox[1]) for bbox in valid_bboxes),
        max(int(bbox[2]) for bbox in valid_bboxes),
        max(int(bbox[3]) for bbox in valid_bboxes),
    ]


def _bbox_center(bbox: list[int] | None) -> tuple[int, int]:
    if not bbox or len(bbox) != 4:
        return 0, 0
    return int(round((int(bbox[0]) + int(bbox[2])) / 2)), int(round((int(bbox[1]) + int(bbox[3])) / 2))


def _support_status_for_graph_node(component_statuses: list[str]) -> str:
    has_relay = "relay_supported" in component_statuses
    terminal_supported_count = sum(
        1
        for status in component_statuses
        if status in {"best_terminal_supported", "candidate_terminal_supported"}
    )
    if has_relay and terminal_supported_count > 0:
        return "terminal_supported_with_relay"
    if terminal_supported_count > 1:
        return "terminal_supported_merged"
    if terminal_supported_count == 1:
        return "terminal_supported"
    if has_relay:
        return "relay_only"
    return "unsupported"


def _build_graph_adjacency(supported_graph: dict) -> tuple[dict[str, set[str]], list[dict]]:
    supported_component_ids = {
        component["raw_component_id"]
        for component in supported_graph.get("raw_components", [])
        if component.get("support_status") != "unsupported"
    }
    adjacency = {component_id: set() for component_id in supported_component_ids}
    used_bridges = []
    for bridge in supported_graph.get("bridge_candidates", []):
        if bridge.get("support_status") not in GRAPH_BRIDGE_STATUSES:
            continue
        from_component_id = bridge.get("from_component_id")
        to_component_id = bridge.get("to_component_id")
        if from_component_id not in supported_component_ids or to_component_id not in supported_component_ids:
            continue
        adjacency[from_component_id].add(to_component_id)
        adjacency[to_component_id].add(from_component_id)
        used_bridges.append(bridge)
    return adjacency, used_bridges


def _connected_components(adjacency: dict[str, set[str]]) -> list[list[str]]:
    visited = set()
    components = []
    for component_id in sorted(adjacency, key=_id_sort_key):
        if component_id in visited:
            continue
        stack = [component_id]
        visited.add(component_id)
        current_component = []
        while stack:
            current_id = stack.pop()
            current_component.append(current_id)
            for neighbor_id in sorted(adjacency.get(current_id, set()), key=_id_sort_key):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                stack.append(neighbor_id)
        components.append(sorted(current_component, key=_id_sort_key))
    return components


def _bridges_for_component_ids(component_ids: list[str], bridges: list[dict]) -> list[dict]:
    component_id_set = set(component_ids)
    return [
        bridge
        for bridge in bridges
        if bridge.get("from_component_id") in component_id_set and bridge.get("to_component_id") in component_id_set
    ]


def _component_lookup(supported_graph: dict) -> dict[str, dict]:
    return {
        component["raw_component_id"]: component
        for component in supported_graph.get("raw_components", [])
    }


def _graph_component_to_node(
    graph_component_ids: list[str],
    graph_node_id: str,
    components_by_id: dict[str, dict],
    used_bridges: list[dict],
) -> dict:
    components = [components_by_id[component_id] for component_id in graph_component_ids]
    node_bridges = _bridges_for_component_ids(graph_component_ids, used_bridges)
    bbox = _bbox_union([component.get("bbox") for component in components])
    x, y = _bbox_center(bbox)
    support_pin_ids = _unique(
        [pin_id for component in components for pin_id in component.get("support_pin_ids", [])]
    )
    support_component_ids = _unique(
        [component_id for component in components for component_id in component.get("support_component_ids", [])]
    )
    relay_component_ids = [
        component["raw_component_id"]
        for component in components
        if component.get("support_status") == "relay_supported"
    ]
    component_statuses = [component.get("support_status", "unsupported") for component in components]
    segment_ids = _unique([segment_id for component in components for segment_id in component.get("segment_ids", [])])
    edge_ids = _unique([edge_id for component in components for edge_id in component.get("edge_ids", [])])
    vertex_ids = _unique([vertex_id for component in components for vertex_id in component.get("vertex_ids", [])])
    endpoint_ids = _unique([endpoint_id for component in components for endpoint_id in component.get("endpoint_ids", [])])
    junction_ids = _unique([junction_id for component in components for junction_id in component.get("junction_ids", [])])
    return {
        "graph_node_id": graph_node_id,
        "x": x,
        "y": y,
        "type": "electrical_dry_run",
        "support_status": _support_status_for_graph_node(component_statuses),
        "terminal_support_count": len(support_pin_ids),
        "pin_ids": support_pin_ids,
        "component_ids": support_component_ids,
        "raw_component_ids": graph_component_ids,
        "relay_raw_component_ids": relay_component_ids,
        "bridge_candidate_ids": [bridge["bridge_candidate_id"] for bridge in node_bridges],
        "bridge_connections": node_bridges,
        "segment_ids": segment_ids,
        "edge_ids": edge_ids,
        "vertex_ids": vertex_ids,
        "endpoint_ids": endpoint_ids,
        "junction_ids": junction_ids,
        "bbox": bbox,
    }


def _current_node_summaries(current_node_result: dict, supported_graph: dict) -> list[dict]:
    component_by_segment_id = {}
    for component in supported_graph.get("raw_components", []):
        for segment_id in component.get("segment_ids", []):
            component_by_segment_id[segment_id] = component["raw_component_id"]

    summaries = []
    for node in current_node_result.get("nodes", []):
        segment_ids = node.get("segment_ids", [])
        raw_component_ids = _unique(
            [
                component_by_segment_id[segment_id]
                for segment_id in segment_ids
                if segment_id in component_by_segment_id
            ]
        )
        summaries.append(
            {
                "node_id": node["node_id"],
                "pin_ids": node.get("pin_ids", []),
                "component_ids": node.get("component_ids", []),
                "raw_component_ids": raw_component_ids,
                "merged_raw_node_ids": node.get("merged_raw_node_ids", []),
                "segment_ids": segment_ids,
                "terminal_support_count": node.get("terminal_support_count", 0),
                "support_status": node.get("support_status"),
                "bbox": node.get("bbox"),
            }
        )
    return summaries


def _jaccard(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _diff_status(graph_node: dict, current_node: dict | None, segment_jaccard: float, pin_jaccard: float) -> str:
    if current_node is None:
        return "graph_only"
    if segment_jaccard == 1.0 and pin_jaccard == 1.0:
        return "exact_match"
    if segment_jaccard >= 0.75 and pin_jaccard >= 0.75:
        return "near_match"
    if segment_jaccard > 0.0 or pin_jaccard > 0.0:
        return "partial_match"
    return "mismatch"


def _best_current_match(graph_node: dict, current_nodes: list[dict], used_current_ids: set[str]) -> tuple[dict | None, float, float]:
    best_node = None
    best_rank = (-1.0, -1.0)
    for current_node in current_nodes:
        if current_node["node_id"] in used_current_ids:
            continue
        segment_score = _jaccard(graph_node.get("segment_ids", []), current_node.get("segment_ids", []))
        pin_score = _jaccard(graph_node.get("pin_ids", []), current_node.get("pin_ids", []))
        rank = (segment_score, pin_score)
        if rank > best_rank:
            best_rank = rank
            best_node = current_node
    if best_node is None:
        return None, 0.0, 0.0
    return best_node, best_rank[0], best_rank[1]


def _build_node_diff(graph_nodes: list[dict], current_node_result: dict, supported_graph: dict) -> dict:
    current_nodes = _current_node_summaries(current_node_result, supported_graph)
    used_current_ids: set[str] = set()
    comparisons = []
    for graph_node in graph_nodes:
        current_node, segment_jaccard, pin_jaccard = _best_current_match(graph_node, current_nodes, used_current_ids)
        if current_node is not None:
            used_current_ids.add(current_node["node_id"])
        comparisons.append(
            {
                "graph_node_id": graph_node["graph_node_id"],
                "current_node_id": current_node["node_id"] if current_node else None,
                "status": _diff_status(graph_node, current_node, segment_jaccard, pin_jaccard),
                "segment_jaccard": round(float(segment_jaccard), 3),
                "pin_jaccard": round(float(pin_jaccard), 3),
                "graph_raw_component_ids": graph_node.get("raw_component_ids", []),
                "current_raw_component_ids": current_node.get("raw_component_ids", []) if current_node else [],
                "graph_pin_ids": graph_node.get("pin_ids", []),
                "current_pin_ids": current_node.get("pin_ids", []) if current_node else [],
                "graph_segment_ids": graph_node.get("segment_ids", []),
                "current_segment_ids": current_node.get("segment_ids", []) if current_node else [],
            }
        )

    current_only_nodes = [
        current_node
        for current_node in current_nodes
        if current_node["node_id"] not in used_current_ids
    ]
    return {
        "comparisons": comparisons,
        "current_only_nodes": current_only_nodes,
        "stats": {
            "graph_node_count": len(graph_nodes),
            "current_node_count": len(current_nodes),
            "exact_match_count": sum(1 for comparison in comparisons if comparison["status"] == "exact_match"),
            "near_match_count": sum(1 for comparison in comparisons if comparison["status"] == "near_match"),
            "partial_match_count": sum(1 for comparison in comparisons if comparison["status"] == "partial_match"),
            "graph_only_count": sum(1 for comparison in comparisons if comparison["status"] == "graph_only"),
            "current_only_count": len(current_only_nodes),
        },
    }


def build_graph_nodes_dry_run(supported_graph: dict, current_node_result: dict, config: dict) -> dict:
    """Generate graph-derived nodes and compare them to current production nodes."""
    _ = config
    components_by_id = _component_lookup(supported_graph)
    adjacency, used_bridges = _build_graph_adjacency(supported_graph)
    graph_components = _connected_components(adjacency)
    graph_nodes = [
        _graph_component_to_node(
            graph_component_ids,
            f"GN{index + 1}",
            components_by_id,
            used_bridges,
        )
        for index, graph_component_ids in enumerate(graph_components)
    ]
    discarded_raw_components = [
        component
        for component in supported_graph.get("raw_components", [])
        if component.get("support_status") == "unsupported"
    ]
    node_diff = _build_node_diff(graph_nodes, current_node_result, supported_graph)
    return {
        "graph_nodes": graph_nodes,
        "discarded_raw_components": discarded_raw_components,
        "used_bridge_candidates": used_bridges,
        "node_diff": node_diff,
        "stats": {
            "graph_node_count": len(graph_nodes),
            "discarded_raw_component_count": len(discarded_raw_components),
            "relay_graph_node_count": sum(
                1 for node in graph_nodes if node.get("relay_raw_component_ids")
            ),
            "used_bridge_candidate_count": len(used_bridges),
            **node_diff["stats"],
        },
    }
