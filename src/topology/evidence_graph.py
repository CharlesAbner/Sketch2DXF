"""Build an audit-only graph view of wire evidence.

This module does not decide electrical topology. It exposes the geometry that
later topology stages are already consuming: wire segments, local contacts,
raw connected components, and bridge candidates between nearby components.
"""

from __future__ import annotations

from math import hypot


Point = tuple[int, int]


def _unique(items: list) -> list:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _distance(point_a: Point, point_b: Point) -> float:
    return hypot(float(point_a[0]) - float(point_b[0]), float(point_a[1]) - float(point_b[1]))


def _segment_endpoints(segment: dict) -> list[tuple[str, Point]]:
    return [
        ("start", (int(segment["x1"]), int(segment["y1"]))),
        ("end", (int(segment["x2"]), int(segment["y2"]))),
    ]


def _point_from_candidate(candidate: dict) -> Point:
    return int(candidate["x"]), int(candidate["y"])


def _closest_point_on_segment(point: Point, segment: dict) -> tuple[float, float, float, float]:
    px = float(point[0])
    py = float(point[1])
    x1 = float(segment["x1"])
    y1 = float(segment["y1"])
    x2 = float(segment["x2"])
    y2 = float(segment["y2"])
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return x1, y1, _distance(point, (int(x1), int(y1))), 0.0
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return proj_x, proj_y, hypot(px - proj_x, py - proj_y), t


def _point_projects_onto_segment(point: Point, segment: dict, tol: float) -> bool:
    x = float(point[0])
    y = float(point[1])
    x1 = min(float(segment["x1"]), float(segment["x2"])) - tol
    x2 = max(float(segment["x1"]), float(segment["x2"])) + tol
    y1 = min(float(segment["y1"]), float(segment["y2"])) - tol
    y2 = max(float(segment["y1"]), float(segment["y2"])) + tol
    return x1 <= x <= x2 and y1 <= y <= y2


def _point_touches_segment(point: Point, segment: dict, tol: float) -> bool:
    _, _, distance, _ = _closest_point_on_segment(point, segment)
    return distance <= tol and _point_projects_onto_segment(point, segment, tol)


def _orthogonal_intersection(seg_a: dict, seg_b: dict, tol: float) -> Point | None:
    if seg_a.get("orientation") == seg_b.get("orientation"):
        return None
    h_seg = seg_a if seg_a.get("orientation") == "h" else seg_b
    v_seg = seg_a if seg_a.get("orientation") == "v" else seg_b
    if h_seg.get("orientation") != "h" or v_seg.get("orientation") != "v":
        return None
    corner = (int(v_seg["x1"]), int(h_seg["y1"]))
    if _point_touches_segment(corner, h_seg, tol) and _point_touches_segment(corner, v_seg, tol):
        return corner
    return None


def _endpoint_touch_edges(
    seg_a: dict,
    seg_b: dict,
    endpoint_vertex_by_segment: dict[tuple[str, str], str],
    tol: float,
) -> list[dict]:
    edges = []
    for role_a, point_a in _segment_endpoints(seg_a):
        for role_b, point_b in _segment_endpoints(seg_b):
            distance = _distance(point_a, point_b)
            if distance > tol:
                continue
            edges.append(
                {
                    "edge_type": "endpoint_touch",
                    "from_vertex_id": endpoint_vertex_by_segment[(seg_a["id"], role_a)],
                    "to_vertex_id": endpoint_vertex_by_segment[(seg_b["id"], role_b)],
                    "segment_ids": [seg_a["id"], seg_b["id"]],
                    "distance": round(float(distance), 3),
                    "points": [[point_a[0], point_a[1]], [point_b[0], point_b[1]]],
                }
            )
    return edges


def _segments_touch(seg_a: dict, seg_b: dict, tol: float) -> bool:
    for _, point_a in _segment_endpoints(seg_a):
        for _, point_b in _segment_endpoints(seg_b):
            if _distance(point_a, point_b) <= tol:
                return True
    return _orthogonal_intersection(seg_a, seg_b, tol) is not None


def _segment_geometry(segment: dict) -> dict:
    return {
        "segment_id": segment["id"],
        "orientation": segment.get("orientation"),
        "x1": int(segment["x1"]),
        "y1": int(segment["y1"]),
        "x2": int(segment["x2"]),
        "y2": int(segment["y2"]),
        "length": int(segment.get("length", 0)),
        "evidence_type": segment.get("evidence_type"),
        "evidence_score": segment.get("evidence_score"),
        "keep_reasons": segment.get("keep_reasons", []),
        "noise_flags": segment.get("noise_flags", []),
        "source_segment_ids": segment.get("source_segment_ids", [segment["id"]]),
    }


def _build_vertices_and_segment_edges(segments: list[dict]) -> tuple[list[dict], list[dict], dict, dict]:
    vertices: list[dict] = []
    edges: list[dict] = []
    endpoint_vertex_by_segment: dict[tuple[str, str], str] = {}
    segment_edge_by_segment_id: dict[str, str] = {}

    next_vertex_index = 1
    next_edge_index = 1
    for segment in segments:
        endpoint_vertex_ids = []
        for role, point in _segment_endpoints(segment):
            vertex_id = f"V{next_vertex_index}"
            next_vertex_index += 1
            endpoint_vertex_by_segment[(segment["id"], role)] = vertex_id
            endpoint_vertex_ids.append(vertex_id)
            vertices.append(
                {
                    "vertex_id": vertex_id,
                    "vertex_type": "segment_endpoint",
                    "x": point[0],
                    "y": point[1],
                    "segment_id": segment["id"],
                    "endpoint_role": role,
                }
            )
        edge_id = f"GE{next_edge_index}"
        next_edge_index += 1
        segment_edge_by_segment_id[segment["id"]] = edge_id
        edges.append(
            {
                "edge_id": edge_id,
                "edge_type": "segment",
                "from_vertex_id": endpoint_vertex_ids[0],
                "to_vertex_id": endpoint_vertex_ids[1],
                **_segment_geometry(segment),
            }
        )

    return vertices, edges, endpoint_vertex_by_segment, segment_edge_by_segment_id


def _append_detected_point_vertices(
    vertices: list[dict],
    endpoints: list[dict],
    junctions: list[dict],
) -> dict[str, str]:
    vertex_by_candidate_id: dict[str, str] = {}
    next_vertex_index = len(vertices) + 1
    for point_type, candidates in (("detected_endpoint", endpoints), ("detected_junction", junctions)):
        for candidate in candidates:
            vertex_id = f"V{next_vertex_index}"
            next_vertex_index += 1
            vertex_by_candidate_id[candidate["id"]] = vertex_id
            vertices.append(
                {
                    "vertex_id": vertex_id,
                    "vertex_type": point_type,
                    "x": int(candidate["x"]),
                    "y": int(candidate["y"]),
                    "source_id": candidate["id"],
                    "pixel_count": int(candidate.get("pixel_count", 0)),
                }
            )
    return vertex_by_candidate_id


def _append_relation_edges(
    edges: list[dict],
    vertices: list[dict],
    segments: list[dict],
    endpoints: list[dict],
    junctions: list[dict],
    endpoint_vertex_by_segment: dict[tuple[str, str], str],
    vertex_by_candidate_id: dict[str, str],
    tol: float,
) -> list[dict]:
    relation_edge_ids = []
    next_edge_index = len(edges) + 1
    next_vertex_index = len(vertices) + 1
    seen_intersections: set[tuple[str, str, int, int]] = set()

    for index, seg_a in enumerate(segments):
        for seg_b in segments[index + 1:]:
            for edge in _endpoint_touch_edges(seg_a, seg_b, endpoint_vertex_by_segment, tol):
                edge_id = f"GE{next_edge_index}"
                next_edge_index += 1
                edge["edge_id"] = edge_id
                edges.append(edge)
                relation_edge_ids.append(edge_id)

            intersection = _orthogonal_intersection(seg_a, seg_b, tol)
            if intersection is None:
                continue
            key = tuple(sorted([seg_a["id"], seg_b["id"]]) + [intersection[0], intersection[1]])
            if key in seen_intersections:
                continue
            seen_intersections.add(key)
            vertex_id = f"V{next_vertex_index}"
            next_vertex_index += 1
            vertices.append(
                {
                    "vertex_id": vertex_id,
                    "vertex_type": "inferred_intersection",
                    "x": intersection[0],
                    "y": intersection[1],
                    "segment_ids": [seg_a["id"], seg_b["id"]],
                }
            )
            edge_id = f"GE{next_edge_index}"
            next_edge_index += 1
            edges.append(
                {
                    "edge_id": edge_id,
                    "edge_type": "orthogonal_intersection",
                    "intersection_vertex_id": vertex_id,
                    "from_segment_id": seg_a["id"],
                    "to_segment_id": seg_b["id"],
                    "point": [intersection[0], intersection[1]],
                    "distance": 0.0,
                }
            )
            relation_edge_ids.append(edge_id)

    detected_points = [*endpoints, *junctions]
    for candidate in detected_points:
        point = _point_from_candidate(candidate)
        point_vertex_id = vertex_by_candidate_id[candidate["id"]]
        for segment in segments:
            proj_x, proj_y, distance, t = _closest_point_on_segment(point, segment)
            if distance > tol or not _point_projects_onto_segment(point, segment, tol):
                continue
            edge_id = f"GE{next_edge_index}"
            next_edge_index += 1
            if t <= 0.05:
                target_role = "start"
            elif t >= 0.95:
                target_role = "end"
            else:
                target_role = None
            edge = {
                "edge_id": edge_id,
                "edge_type": "detected_point_on_segment",
                "from_vertex_id": point_vertex_id,
                "candidate_id": candidate["id"],
                "candidate_type": candidate.get("type"),
                "segment_id": segment["id"],
                "distance": round(float(distance), 3),
                "projected_point": [round(float(proj_x), 3), round(float(proj_y), 3)],
            }
            if target_role is not None:
                edge["to_vertex_id"] = endpoint_vertex_by_segment[(segment["id"], target_role)]
                edge["edge_type"] = "detected_point_touch"
            edges.append(edge)
            relation_edge_ids.append(edge_id)

    return relation_edge_ids


def _build_segment_components(segments: list[dict], tol: float) -> list[list[dict]]:
    visited = [False] * len(segments)
    components: list[list[dict]] = []
    for index in range(len(segments)):
        if visited[index]:
            continue
        stack = [index]
        visited[index] = True
        component = []
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
    return components


def _component_bbox(points: list[Point]) -> list[int] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs) + 1, max(ys) + 1]


def _build_raw_components(
    segments: list[dict],
    endpoints: list[dict],
    junctions: list[dict],
    endpoint_vertex_by_segment: dict[tuple[str, str], str],
    vertex_by_candidate_id: dict[str, str],
    segment_edge_by_segment_id: dict[str, str],
    relation_edges: list[dict],
    tol: float,
) -> tuple[list[dict], dict[str, str]]:
    raw_components = []
    component_by_segment_id = {}
    relation_edges_by_segment: dict[str, list[str]] = {}
    for edge in relation_edges:
        segment_ids = []
        if "segment_ids" in edge:
            segment_ids.extend(edge["segment_ids"])
        if "segment_id" in edge:
            segment_ids.append(edge["segment_id"])
        if "from_segment_id" in edge:
            segment_ids.append(edge["from_segment_id"])
        if "to_segment_id" in edge:
            segment_ids.append(edge["to_segment_id"])
        for segment_id in segment_ids:
            relation_edges_by_segment.setdefault(segment_id, []).append(edge["edge_id"])

    for index, component_segments in enumerate(_build_segment_components(segments, tol), start=1):
        component_id = f"RC{index}"
        component_segment_ids = [segment["id"] for segment in component_segments]
        for segment_id in component_segment_ids:
            component_by_segment_id[segment_id] = component_id

        component_endpoints = [
            candidate
            for candidate in endpoints
            if any(_point_touches_segment(_point_from_candidate(candidate), segment, tol) for segment in component_segments)
        ]
        component_junctions = [
            candidate
            for candidate in junctions
            if any(_point_touches_segment(_point_from_candidate(candidate), segment, tol) for segment in component_segments)
        ]

        points = []
        for segment in component_segments:
            points.extend(point for _, point in _segment_endpoints(segment))
        points.extend(_point_from_candidate(candidate) for candidate in [*component_endpoints, *component_junctions])

        vertex_ids = []
        for segment in component_segments:
            vertex_ids.extend(
                [
                    endpoint_vertex_by_segment[(segment["id"], "start")],
                    endpoint_vertex_by_segment[(segment["id"], "end")],
                ]
            )
        vertex_ids.extend(vertex_by_candidate_id[candidate["id"]] for candidate in [*component_endpoints, *component_junctions])

        edge_ids = []
        for segment_id in component_segment_ids:
            edge_ids.append(segment_edge_by_segment_id[segment_id])
            edge_ids.extend(relation_edges_by_segment.get(segment_id, []))

        raw_components.append(
            {
                "raw_component_id": component_id,
                "segment_ids": component_segment_ids,
                "endpoint_ids": [candidate["id"] for candidate in component_endpoints],
                "junction_ids": [candidate["id"] for candidate in component_junctions],
                "vertex_ids": _unique(vertex_ids),
                "edge_ids": _unique(edge_ids),
                "bbox": _component_bbox(points),
                "segment_count": len(component_segment_ids),
                "endpoint_count": len(component_endpoints),
                "junction_count": len(component_junctions),
            }
        )

    return raw_components, component_by_segment_id


def _point_incident_orientations(segment: dict, point: Point, tol: float = 2.0) -> set[str]:
    orientations = set()
    for _, endpoint in _segment_endpoints(segment):
        if _distance(point, endpoint) <= tol and segment.get("orientation"):
            orientations.add(segment["orientation"])
    return orientations


def _bridge_point_to_segment(
    point_segment: dict,
    point: Point,
    target_segment: dict,
    from_component_id: str,
    to_component_id: str,
    max_gap: float,
    axis_tolerance: float,
) -> dict | None:
    orientation = target_segment.get("orientation")
    if orientation not in {"h", "v"}:
        return None
    if orientation in _point_incident_orientations(point_segment, point):
        return None

    proj_x, proj_y, distance, _ = _closest_point_on_segment(point, target_segment)
    if distance > max_gap:
        return None
    if orientation == "h" and not (
        min(float(target_segment["x1"]), float(target_segment["x2"])) - axis_tolerance
        <= float(point[0])
        <= max(float(target_segment["x1"]), float(target_segment["x2"])) + axis_tolerance
    ):
        return None
    if orientation == "v" and not (
        min(float(target_segment["y1"]), float(target_segment["y2"])) - axis_tolerance
        <= float(point[1])
        <= max(float(target_segment["y1"]), float(target_segment["y2"])) + axis_tolerance
    ):
        return None

    return {
        "candidate_type": "point_to_segment_bridge",
        "from_component_id": from_component_id,
        "to_component_id": to_component_id,
        "from_segment_id": point_segment["id"],
        "target_segment_id": target_segment["id"],
        "distance": round(float(distance), 3),
        "point": [point[0], point[1]],
        "projected_point": [round(float(proj_x), 3), round(float(proj_y), 3)],
    }


def _build_bridge_candidates(
    raw_components: list[dict],
    component_by_segment_id: dict[str, str],
    segments: list[dict],
    bridge_gap: float,
    axis_tolerance: float,
    point_to_segment_gap: float,
    point_to_segment_axis_tolerance: float,
) -> list[dict]:
    segments_by_id = {segment["id"]: segment for segment in segments}
    candidates = []
    candidate_index = 1

    for comp_index, component_a in enumerate(raw_components):
        for component_b in raw_components[comp_index + 1:]:
            best_by_type: dict[str, dict] = {}
            segments_a = [segments_by_id[segment_id] for segment_id in component_a["segment_ids"]]
            segments_b = [segments_by_id[segment_id] for segment_id in component_b["segment_ids"]]

            for seg_a in segments_a:
                for _, point_a in _segment_endpoints(seg_a):
                    for seg_b in segments_b:
                        for _, point_b in _segment_endpoints(seg_b):
                            dx = abs(point_a[0] - point_b[0])
                            dy = abs(point_a[1] - point_b[1])
                            if dx <= axis_tolerance and dy <= bridge_gap:
                                candidate = {
                                    "candidate_type": "vertical_gap_bridge",
                                    "from_component_id": component_a["raw_component_id"],
                                    "to_component_id": component_b["raw_component_id"],
                                    "from_segment_id": seg_a["id"],
                                    "to_segment_id": seg_b["id"],
                                    "distance": round(float(_distance(point_a, point_b)), 3),
                                    "points": [[point_a[0], point_a[1]], [point_b[0], point_b[1]]],
                                }
                                best_by_type["vertical_gap_bridge"] = _choose_better_bridge(
                                    best_by_type.get("vertical_gap_bridge"),
                                    candidate,
                                )
                            if dy <= axis_tolerance and dx <= bridge_gap:
                                candidate = {
                                    "candidate_type": "horizontal_gap_bridge",
                                    "from_component_id": component_a["raw_component_id"],
                                    "to_component_id": component_b["raw_component_id"],
                                    "from_segment_id": seg_a["id"],
                                    "to_segment_id": seg_b["id"],
                                    "distance": round(float(_distance(point_a, point_b)), 3),
                                    "points": [[point_a[0], point_a[1]], [point_b[0], point_b[1]]],
                                }
                                best_by_type["horizontal_gap_bridge"] = _choose_better_bridge(
                                    best_by_type.get("horizontal_gap_bridge"),
                                    candidate,
                                )

            for source_segments, target_segments in ((segments_a, segments_b), (segments_b, segments_a)):
                for source_segment in source_segments:
                    from_component_id = component_by_segment_id[source_segment["id"]]
                    for _, point in _segment_endpoints(source_segment):
                        for target_segment in target_segments:
                            to_component_id = component_by_segment_id[target_segment["id"]]
                            candidate = _bridge_point_to_segment(
                                source_segment,
                                point,
                                target_segment,
                                from_component_id,
                                to_component_id,
                                point_to_segment_gap,
                                point_to_segment_axis_tolerance,
                            )
                            if candidate is None:
                                continue
                            best_by_type["point_to_segment_bridge"] = _choose_better_bridge(
                                best_by_type.get("point_to_segment_bridge"),
                                candidate,
                            )

            for candidate in best_by_type.values():
                candidate["bridge_candidate_id"] = f"B{candidate_index}"
                candidate_index += 1
                candidates.append(candidate)

    return sorted(candidates, key=lambda item: (float(item["distance"]), item["candidate_type"]))


def _choose_better_bridge(existing: dict | None, candidate: dict) -> dict:
    if existing is None:
        return candidate
    if float(candidate["distance"]) < float(existing["distance"]):
        return candidate
    return existing


def build_evidence_graph(wire_result: dict, junction_result: dict, config: dict) -> dict:
    """Build an audit-only graph representation from wire and junction evidence."""
    segments = wire_result.get("segments", [])
    endpoints = junction_result.get("endpoints", [])
    junctions = junction_result.get("junctions", [])
    topology_cfg = config["topology"]
    connect_radius = float(topology_cfg.get("wire_connect_radius", 8))
    bridge_gap = float(topology_cfg.get("node_bridge_gap", 32))
    axis_tolerance = float(topology_cfg.get("node_bridge_axis_tolerance", 10))
    point_to_segment_gap = float(topology_cfg.get("node_bridge_point_to_segment_gap", 14))
    point_to_segment_axis_tolerance = float(topology_cfg.get("node_bridge_point_to_segment_axis_tolerance", 12))
    bridge_candidates_enabled = bool(topology_cfg.get("evidence_graph_enable_bridge_candidates", True))

    vertices, edges, endpoint_vertex_by_segment, segment_edge_by_segment_id = _build_vertices_and_segment_edges(
        segments
    )
    vertex_by_candidate_id = _append_detected_point_vertices(vertices, endpoints, junctions)
    relation_edge_ids = _append_relation_edges(
        edges,
        vertices,
        segments,
        endpoints,
        junctions,
        endpoint_vertex_by_segment,
        vertex_by_candidate_id,
        connect_radius,
    )
    relation_edges = [edge for edge in edges if edge["edge_id"] in set(relation_edge_ids)]
    raw_components, component_by_segment_id = _build_raw_components(
        segments,
        endpoints,
        junctions,
        endpoint_vertex_by_segment,
        vertex_by_candidate_id,
        segment_edge_by_segment_id,
        relation_edges,
        connect_radius,
    )
    bridge_candidates = []
    if bridge_candidates_enabled:
        bridge_candidates = _build_bridge_candidates(
            raw_components,
            component_by_segment_id,
            segments,
            bridge_gap,
            axis_tolerance,
            point_to_segment_gap,
            point_to_segment_axis_tolerance,
        )

    return {
        "vertices": vertices,
        "edges": edges,
        "raw_components": raw_components,
        "bridge_candidates": bridge_candidates,
        "stats": {
            "segment_count": len(segments),
            "detected_endpoint_count": len(endpoints),
            "detected_junction_count": len(junctions),
            "vertex_count": len(vertices),
            "edge_count": len(edges),
            "segment_edge_count": sum(1 for edge in edges if edge.get("edge_type") == "segment"),
            "relation_edge_count": len(relation_edge_ids),
            "raw_component_count": len(raw_components),
            "bridge_candidate_count": len(bridge_candidates),
        },
        "config": {
            "connect_radius": connect_radius,
            "bridge_gap": bridge_gap,
            "axis_tolerance": axis_tolerance,
            "point_to_segment_gap": point_to_segment_gap,
            "point_to_segment_axis_tolerance": point_to_segment_axis_tolerance,
            "bridge_candidates_enabled": bridge_candidates_enabled,
        },
    }
