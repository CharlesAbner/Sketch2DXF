"""
本文件的作用：
- 将导线线段按连通关系聚合成 electrical nodes。
- 它不再直接把 junction/endpoint 当最终 node，而是把连续导线网络视作电气节点。

建议说明：
- 当前实现以 wire segments 的连通分量为主，适合串联和基础并联场景。
- 后续可继续增强支路接母线、T 形连接和更复杂交点的连通规则。
"""

from __future__ import annotations

from math import hypot


def _unique(items: list) -> list:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _point_distance(point_a: tuple[int, int], point_b: tuple[int, int]) -> float:
    return hypot(float(point_a[0]) - float(point_b[0]), float(point_a[1]) - float(point_b[1]))


def _segment_endpoints(segment: dict) -> list[tuple[int, int]]:
    return [
        (int(segment["x1"]), int(segment["y1"])),
        (int(segment["x2"]), int(segment["y2"])),
    ]


def _closest_point_on_segment(point: tuple[int, int], segment: dict) -> tuple[float, float, float, float]:
    px = float(point[0])
    py = float(point[1])
    x1 = float(segment["x1"])
    y1 = float(segment["y1"])
    x2 = float(segment["x2"])
    y2 = float(segment["y2"])
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return x1, y1, _point_distance(point, (int(x1), int(y1))), 0.0
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return proj_x, proj_y, hypot(px - proj_x, py - proj_y), t


def _point_to_segment_distance(point: tuple[int, int], segment: dict) -> float:
    return _closest_point_on_segment(point, segment)[2]


def _point_projects_onto_segment(point: tuple[int, int], segment: dict, tol: float) -> bool:
    x = float(point[0])
    y = float(point[1])
    x1 = min(float(segment["x1"]), float(segment["x2"])) - tol
    x2 = max(float(segment["x1"]), float(segment["x2"])) + tol
    y1 = min(float(segment["y1"]), float(segment["y2"])) - tol
    y2 = max(float(segment["y1"]), float(segment["y2"])) + tol
    return x1 <= x <= x2 and y1 <= y <= y2


def _orthogonal_segments_intersect(seg_a: dict, seg_b: dict, tol: float) -> bool:
    if seg_a.get("orientation") == seg_b.get("orientation"):
        return False
    h_seg = seg_a if seg_a.get("orientation") == "h" else seg_b
    v_seg = seg_a if seg_a.get("orientation") == "v" else seg_b
    corner = (int(v_seg["x1"]), int(h_seg["y1"]))
    return (
        _point_projects_onto_segment(corner, h_seg, tol)
        and _point_projects_onto_segment(corner, v_seg, tol)
        and _point_to_segment_distance(corner, h_seg) <= tol
        and _point_to_segment_distance(corner, v_seg) <= tol
    )


def _segment_touches_candidate(segment: dict, candidate: dict, tol: float) -> bool:
    point = (int(candidate["x"]), int(candidate["y"]))
    return _point_projects_onto_segment(point, segment, tol) and _point_to_segment_distance(point, segment) <= tol


def _node_points(node: dict) -> list[tuple[int, int]]:
    return [(int(point[0]), int(point[1])) for point in node.get("points", [])]


def _segment_geometry(segment: dict) -> dict:
    return {
        "segment_id": segment["id"],
        "orientation": segment.get("orientation"),
        "x1": int(segment["x1"]),
        "y1": int(segment["y1"]),
        "x2": int(segment["x2"]),
        "y2": int(segment["y2"]),
    }


def _node_segments(node: dict) -> list[dict]:
    segments = []
    for member in node.get("members", []):
        if "segment_id" not in member or not all(key in member for key in ("x1", "y1", "x2", "y2")):
            continue
        segments.append(
            {
                "id": member["segment_id"],
                "orientation": member.get("orientation"),
                "x1": int(member["x1"]),
                "y1": int(member["y1"]),
                "x2": int(member["x2"]),
                "y2": int(member["y2"]),
            }
        )
    return segments


def _point_incident_orientations(node: dict, point: tuple[int, int], tol: float = 2.0) -> set[str]:
    orientations = set()
    for segment in _node_segments(node):
        for endpoint in _segment_endpoints(segment):
            if _point_distance(point, endpoint) <= tol and segment.get("orientation"):
                orientations.add(segment["orientation"])
    return orientations


def _segments_touch(seg_a: dict, seg_b: dict, tol: float) -> bool:
    for point_a in _segment_endpoints(seg_a):
        for point_b in _segment_endpoints(seg_b):
            if _point_distance(point_a, point_b) <= tol:
                return True
    if _orthogonal_segments_intersect(seg_a, seg_b, tol):
        return True
    return False


def _point_to_node_segment_bridge(
    point_node: dict,
    segment_node: dict,
    bridge_gap: float,
    axis_tolerance: float,
) -> dict | None:
    best_bridge: dict | None = None
    for point in _node_points(point_node):
        point_orientations = _point_incident_orientations(point_node, point)
        for segment in _node_segments(segment_node):
            orientation = segment.get("orientation")
            if orientation not in {"h", "v"}:
                continue
            if point_orientations and orientation in point_orientations:
                continue
            proj_x, proj_y, distance, _ = _closest_point_on_segment(point, segment)
            if distance > bridge_gap:
                continue
            if orientation == "h" and not (
                min(float(segment["x1"]), float(segment["x2"])) - axis_tolerance
                <= float(point[0])
                <= max(float(segment["x1"]), float(segment["x2"])) + axis_tolerance
            ):
                continue
            if orientation == "v" and not (
                min(float(segment["y1"]), float(segment["y2"])) - axis_tolerance
                <= float(point[1])
                <= max(float(segment["y1"]), float(segment["y2"])) + axis_tolerance
            ):
                continue

            bridge = {
                "from_node_id": point_node["node_id"],
                "to_node_id": segment_node["node_id"],
                "reason": "point_to_segment_bridge",
                "distance": round(float(distance), 3),
                "point": [point[0], point[1]],
                "projected_point": [round(float(proj_x), 3), round(float(proj_y), 3)],
                "target_segment_id": segment["id"],
            }
            if best_bridge is None or bridge["distance"] < best_bridge["distance"]:
                best_bridge = bridge
    return best_bridge


def _raw_nodes_bridge(
    node_a: dict,
    node_b: dict,
    bridge_gap: float,
    axis_tolerance: float,
    point_to_segment_gap: float,
    point_to_segment_axis_tolerance: float,
) -> dict | None:
    """Return bridge metadata when two raw nodes look like parts of one broken wire."""
    best_bridge: dict | None = None
    for point_a in _node_points(node_a):
        for point_b in _node_points(node_b):
            dx = abs(point_a[0] - point_b[0])
            dy = abs(point_a[1] - point_b[1])
            if dx <= axis_tolerance and dy <= bridge_gap:
                distance = _point_distance(point_a, point_b)
                bridge = {
                    "from_node_id": node_a["node_id"],
                    "to_node_id": node_b["node_id"],
                    "reason": "vertical_gap_bridge",
                    "distance": round(float(distance), 3),
                    "points": [[point_a[0], point_a[1]], [point_b[0], point_b[1]]],
                }
                if best_bridge is None or bridge["distance"] < best_bridge["distance"]:
                    best_bridge = bridge
            if dy <= axis_tolerance and dx <= bridge_gap:
                distance = _point_distance(point_a, point_b)
                bridge = {
                    "from_node_id": node_a["node_id"],
                    "to_node_id": node_b["node_id"],
                    "reason": "horizontal_gap_bridge",
                    "distance": round(float(distance), 3),
                    "points": [[point_a[0], point_a[1]], [point_b[0], point_b[1]]],
                }
                if best_bridge is None or bridge["distance"] < best_bridge["distance"]:
                    best_bridge = bridge
    for bridge in (
        _point_to_node_segment_bridge(
            node_a,
            node_b,
            point_to_segment_gap,
            point_to_segment_axis_tolerance,
        ),
        _point_to_node_segment_bridge(
            node_b,
            node_a,
            point_to_segment_gap,
            point_to_segment_axis_tolerance,
        ),
    ):
        if bridge is not None and (best_bridge is None or bridge["distance"] < best_bridge["distance"]):
            best_bridge = bridge
    return best_bridge


def _build_wire_components(
    segments: list[dict],
    endpoints: list[dict],
    junctions: list[dict],
    tol: float,
) -> list[dict]:
    components: list[list[dict]] = []
    visited = [False] * len(segments)
    for index in range(len(segments)):
        if visited[index]:
            continue
        stack = [index]
        visited[index] = True
        component: list[dict] = []
        while stack:
            current_index = stack.pop()
            current_segment = segments[current_index]
            component.append(current_segment)
            for neighbor_index in range(len(segments)):
                if visited[neighbor_index]:
                    continue
                if _segments_touch(current_segment, segments[neighbor_index], tol):
                    visited[neighbor_index] = True
                    stack.append(neighbor_index)
        components.append(component)
    component_records = []
    for component in components:
        component_records.append(
            {
                "segments": component,
                "endpoints": [
                    point
                    for point in endpoints
                    if any(_segment_touches_candidate(segment, point, tol) for segment in component)
                ],
                "junctions": [
                    point
                    for point in junctions
                    if any(_segment_touches_candidate(segment, point, tol) for segment in component)
                ],
            }
        )
    return component_records


def _component_to_node(component_record: dict, node_id: str) -> dict:
    component_segments = component_record["segments"]
    endpoints = component_record.get("endpoints", [])
    junctions = component_record.get("junctions", [])
    unique_points: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for segment in component_segments:
        for point in _segment_endpoints(segment):
            if point not in seen:
                seen.add(point)
                unique_points.append(point)
    for candidate in endpoints + junctions:
        point = (int(candidate["x"]), int(candidate["y"]))
        if point not in seen:
            seen.add(point)
            unique_points.append(point)

    avg_x = int(round(sum(point[0] for point in unique_points) / len(unique_points)))
    avg_y = int(round(sum(point[1] for point in unique_points) / len(unique_points)))
    xs = [point[0] for point in unique_points]
    ys = [point[1] for point in unique_points]
    keep_reasons = _unique(
        [
            reason
            for segment in component_segments
            for reason in segment.get("keep_reasons", [])
        ]
    )
    noise_flags = _unique(
        [
            flag
            for segment in component_segments
            for flag in segment.get("noise_flags", [])
        ]
    )
    source_segment_ids = _unique(
        [
            source_id
            for segment in component_segments
            for source_id in segment.get("source_segment_ids", [segment["id"]])
        ]
    )
    evidence_scores = [float(segment.get("evidence_score", 0.5)) for segment in component_segments]
    node_confidence = round(sum(evidence_scores) / max(len(evidence_scores), 1), 3)
    return {
        "node_id": node_id,
        "x": avg_x,
        "y": avg_y,
        "type": "electrical",
        "support_status": "raw_unresolved",
        "terminal_support_count": 0,
        "pin_ids": [],
        "component_ids": [],
        "node_confidence": node_confidence,
        "discard_reason": None,
        "keep_reasons": keep_reasons,
        "noise_flags": noise_flags,
        "source_segment_ids": source_segment_ids,
        "members": [_segment_geometry(segment) for segment in component_segments]
        + [{"endpoint_id": candidate["id"], "type": "endpoint"} for candidate in endpoints]
        + [{"junction_id": candidate["id"], "type": "junction"} for candidate in junctions],
        "points": [[x, y] for x, y in unique_points],
        "segment_ids": [segment["id"] for segment in component_segments],
        "endpoint_ids": [candidate["id"] for candidate in endpoints],
        "junction_ids": [candidate["id"] for candidate in junctions],
        "bbox": [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
    }


def build_nodes(junction_result: dict, wire_result: dict, config: dict) -> dict:
    """Build electrical nodes from connected wire-segment components."""
    segments = wire_result.get("segments", [])
    endpoints = junction_result.get("endpoints", [])
    junctions = junction_result.get("junctions", [])
    connect_radius = float(config["topology"].get("wire_connect_radius", 8))
    components = _build_wire_components(segments, endpoints, junctions, connect_radius)
    nodes = [
        _component_to_node(component, f"N{index + 1}")
        for index, component in enumerate(components)
    ]
    return {
        "nodes": nodes,
        "raw_nodes": nodes,
        "discarded_nodes": [],
        "stats": {
            "candidate_count": len(segments) + len(endpoints) + len(junctions),
            "node_count": len(nodes),
            "raw_node_count": len(nodes),
            "active_node_count": len(nodes),
            "discarded_node_count": 0,
        },
    }


def _node_sort_key(node_id: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(node_id) if ch.isdigit())
    return (int(digits) if digits else 10**9, str(node_id))


def _build_raw_node_components(
    raw_nodes: list[dict],
    bridge_gap: float,
    axis_tolerance: float,
    point_to_segment_gap: float,
    point_to_segment_axis_tolerance: float,
) -> tuple[list[list[dict]], list[dict]]:
    adjacency: dict[str, set[str]] = {node["node_id"]: set() for node in raw_nodes}
    bridge_connections: list[dict] = []
    for index, node_a in enumerate(raw_nodes):
        for node_b in raw_nodes[index + 1:]:
            bridge = _raw_nodes_bridge(
                node_a,
                node_b,
                bridge_gap,
                axis_tolerance,
                point_to_segment_gap,
                point_to_segment_axis_tolerance,
            )
            if bridge is None:
                continue
            adjacency[node_a["node_id"]].add(node_b["node_id"])
            adjacency[node_b["node_id"]].add(node_a["node_id"])
            bridge_connections.append(bridge)

    node_lookup = {node["node_id"]: node for node in raw_nodes}
    visited: set[str] = set()
    components: list[list[dict]] = []
    for node in raw_nodes:
        node_id = node["node_id"]
        if node_id in visited:
            continue
        stack = [node_id]
        visited.add(node_id)
        component_ids = []
        while stack:
            current_id = stack.pop()
            component_ids.append(current_id)
            for neighbor_id in adjacency.get(current_id, set()):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                stack.append(neighbor_id)
        components.append([node_lookup[current_id] for current_id in sorted(component_ids, key=_node_sort_key)])
    return components, bridge_connections


def _merge_raw_nodes(
    raw_component: list[dict],
    support_matches: list[dict],
    bridge_connections: list[dict],
) -> dict:
    raw_node_ids = [node["node_id"] for node in raw_component]
    supported_node_ids = _unique([match["node_id"] for match in support_matches])
    output_node_id = (
        sorted(supported_node_ids, key=_node_sort_key)[0]
        if supported_node_ids
        else sorted(raw_node_ids, key=_node_sort_key)[0]
    )

    unique_points: list[tuple[int, int]] = []
    seen_points: set[tuple[int, int]] = set()
    for node in raw_component:
        for point in _node_points(node):
            if point in seen_points:
                continue
            seen_points.add(point)
            unique_points.append(point)

    pin_ids = _unique([match["pin_id"] for match in support_matches])
    component_ids = _unique([match["component_id"] for match in support_matches])
    keep_reasons = _unique([reason for node in raw_component for reason in node.get("keep_reasons", [])])
    noise_flags = _unique([flag for node in raw_component for flag in node.get("noise_flags", [])])
    source_segment_ids = _unique(
        [
            source_id
            for node in raw_component
            for source_id in node.get("source_segment_ids", [])
        ]
    )
    members = []
    for node in raw_component:
        members.extend(node.get("members", []))
    segment_ids = _unique([segment_id for node in raw_component for segment_id in node.get("segment_ids", [])])
    endpoint_ids = _unique([endpoint_id for node in raw_component for endpoint_id in node.get("endpoint_ids", [])])
    junction_ids = _unique([junction_id for node in raw_component for junction_id in node.get("junction_ids", [])])
    node_confidences = [float(node.get("node_confidence", 0.5)) for node in raw_component]
    node_confidence = round(sum(node_confidences) / max(len(node_confidences), 1), 3)
    best_match_confidence = max((float(match.get("confidence", 0.0)) for match in support_matches), default=0.0)

    xs = [point[0] for point in unique_points]
    ys = [point[1] for point in unique_points]
    relevant_bridge_connections = [
        bridge
        for bridge in bridge_connections
        if bridge["from_node_id"] in raw_node_ids and bridge["to_node_id"] in raw_node_ids
    ]
    return {
        "node_id": output_node_id,
        "x": int(round(sum(xs) / max(len(xs), 1))),
        "y": int(round(sum(ys) / max(len(ys), 1))),
        "type": "electrical",
        "support_status": "terminal_supported_merged" if len(raw_component) > 1 else "terminal_supported",
        "terminal_support_count": len(pin_ids),
        "pin_ids": pin_ids,
        "component_ids": component_ids,
        "node_confidence": node_confidence,
        "best_match_confidence": round(float(best_match_confidence), 3),
        "discard_reason": None,
        "keep_reasons": keep_reasons,
        "noise_flags": noise_flags,
        "source_segment_ids": source_segment_ids,
        "merged_raw_node_ids": raw_node_ids,
        "bridge_connections": relevant_bridge_connections,
        "members": members,
        "points": [[x, y] for x, y in unique_points],
        "segment_ids": segment_ids,
        "endpoint_ids": endpoint_ids,
        "junction_ids": junction_ids,
        "bbox": [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
    }


def filter_nodes_by_terminal_support(node_result: dict, match_result: dict, config: dict) -> dict:
    """Promote terminal-supported raw-node components into final electrical nodes."""
    min_confidence = float(config["topology"].get("node_terminal_support_min_confidence", 0.0))
    bridge_gap = float(config["topology"].get("node_bridge_gap", 32))
    axis_tolerance = float(config["topology"].get("node_bridge_axis_tolerance", 10))
    point_to_segment_gap = float(config["topology"].get("node_bridge_point_to_segment_gap", 14))
    point_to_segment_axis_tolerance = float(config["topology"].get("node_bridge_point_to_segment_axis_tolerance", 12))
    raw_nodes = node_result.get("raw_nodes", node_result.get("nodes", []))
    matches_by_node: dict[str, list[dict]] = {}
    for match in match_result.get("matches", []):
        if float(match.get("confidence", 0.0)) < min_confidence:
            continue
        matches_by_node.setdefault(match["node_id"], []).append(match)

    active_nodes = []
    discarded_nodes = []
    node_id_map: dict[str, str] = {}
    raw_components, bridge_connections = _build_raw_node_components(
        raw_nodes,
        bridge_gap,
        axis_tolerance,
        point_to_segment_gap,
        point_to_segment_axis_tolerance,
    )
    for raw_component in raw_components:
        support_matches = [
            match
            for raw_node in raw_component
            for match in matches_by_node.get(raw_node["node_id"], [])
        ]
        if support_matches:
            merged_node = _merge_raw_nodes(raw_component, support_matches, bridge_connections)
            active_nodes.append(merged_node)
            for raw_node_id in merged_node.get("merged_raw_node_ids", []):
                node_id_map[raw_node_id] = merged_node["node_id"]
        else:
            for raw_node in raw_component:
                discarded_nodes.append(
                    {
                        **raw_node,
                        "terminal_support_count": 0,
                        "pin_ids": [],
                        "component_ids": [],
                        "best_match_confidence": 0.0,
                        "support_status": "discarded",
                        "discard_reason": "unanchored_evidence",
                    }
                )

    stats = {
        **node_result.get("stats", {}),
        "raw_node_count": len(raw_nodes),
        "node_count": len(active_nodes),
        "active_node_count": len(active_nodes),
        "discarded_node_count": len(discarded_nodes),
        "terminal_supported_node_count": len(active_nodes),
        "bridge_connection_count": len(bridge_connections),
        "merged_node_count": sum(1 for node in active_nodes if len(node.get("merged_raw_node_ids", [])) > 1),
    }
    return {
        **node_result,
        "nodes": active_nodes,
        "raw_nodes": raw_nodes,
        "discarded_nodes": discarded_nodes,
        "node_id_map": node_id_map,
        "bridge_connections": bridge_connections,
        "stats": stats,
    }
