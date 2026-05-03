"""Build audit-only terminal-to-evidence attachment candidates.

The matcher still owns the final pin-to-node decision. This module exposes the
lower-level evidence each terminal can see along its expected direction, so the
next graph-pruning step has a clear, inspectable input.
"""

from __future__ import annotations

from math import hypot


def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return hypot(float(x1) - float(x2), float(y1) - float(y2))


def _closest_point_on_segment(px: float, py: float, edge: dict) -> tuple[float, float, float]:
    x1 = float(edge["x1"])
    y1 = float(edge["y1"])
    x2 = float(edge["x2"])
    y2 = float(edge["y2"])
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return x1, y1, _distance(px, py, x1, y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return proj_x, proj_y, _distance(px, py, proj_x, proj_y)


def _preferred_orientation(pin: dict) -> str | None:
    side = pin.get("side")
    if side in {"left", "right"}:
        return "h"
    if side in {"top", "bottom"}:
        return "v"
    return None


def _ray_metrics(pin: dict, target_x: float, target_y: float) -> tuple[float, float] | None:
    dx = float(target_x) - float(pin["x"])
    dy = float(target_y) - float(pin["y"])
    side = pin.get("side")
    if side == "left":
        return -dx, abs(dy)
    if side == "right":
        return dx, abs(dy)
    if side == "top":
        return -dy, abs(dx)
    if side == "bottom":
        return dy, abs(dx)
    return None


def _corridor_status(
    pin: dict,
    target_x: float,
    target_y: float,
    corridor_length: float,
    corridor_width: float,
    backtrack: float,
) -> tuple[bool, float, float]:
    metrics = _ray_metrics(pin, target_x, target_y)
    if metrics is None:
        return False, 0.0, _distance(float(pin["x"]), float(pin["y"]), target_x, target_y)
    forward_distance, lateral_distance = metrics
    in_corridor = -backtrack <= forward_distance <= corridor_length and lateral_distance <= corridor_width
    return in_corridor, forward_distance, lateral_distance


def _attachment_score(
    in_corridor: bool,
    forward_distance: float,
    lateral_distance: float,
    corridor_length: float,
    corridor_width: float,
    orientation: str | None,
    pin: dict,
) -> float:
    lateral_score = max(0.0, 1.0 - lateral_distance / max(corridor_width, 1.0))
    forward_score = max(0.0, 1.0 - max(forward_distance, 0.0) / max(corridor_length, 1.0))
    preferred_orientation = _preferred_orientation(pin)
    if orientation is None or preferred_orientation is None:
        orientation_score = 0.75
    else:
        orientation_score = 1.0 if orientation == preferred_orientation else 0.45
    corridor_bonus = 1.0 if in_corridor else 0.55
    return round(float(corridor_bonus * (0.45 * lateral_score + 0.35 * forward_score + 0.20 * orientation_score)), 3)


def _raw_component_lookup(evidence_graph: dict) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    segment_to_component = {}
    endpoint_to_component = {}
    junction_to_component = {}
    for component in evidence_graph.get("raw_components", []):
        component_id = component["raw_component_id"]
        for segment_id in component.get("segment_ids", []):
            segment_to_component[segment_id] = component_id
        for endpoint_id in component.get("endpoint_ids", []):
            endpoint_to_component[endpoint_id] = component_id
        for junction_id in component.get("junction_ids", []):
            junction_to_component[junction_id] = component_id
    return segment_to_component, endpoint_to_component, junction_to_component


def _candidate_sort_key(candidate: dict) -> tuple[float, float]:
    return -float(candidate.get("attachment_score", 0.0)), float(candidate.get("distance", 0.0))


def _segment_candidates(
    pin: dict,
    segment_edges: list[dict],
    segment_to_component: dict[str, str],
    config_values: dict,
) -> list[dict]:
    candidates = []
    for edge in segment_edges:
        proj_x, proj_y, distance = _closest_point_on_segment(float(pin["x"]), float(pin["y"]), edge)
        in_corridor, forward_distance, lateral_distance = _corridor_status(
            pin,
            proj_x,
            proj_y,
            config_values["corridor_length"],
            config_values["corridor_width"],
            config_values["corridor_backtrack"],
        )
        nearby = distance <= config_values["segment_radius"]
        if not in_corridor and not nearby:
            continue
        segment_id = edge["segment_id"]
        candidates.append(
            {
                "pin_id": pin["pin_id"],
                "component_id": pin["component_id"],
                "evidence_kind": "segment",
                "evidence_id": segment_id,
                "graph_edge_id": edge["edge_id"],
                "raw_component_id": segment_to_component.get(segment_id),
                "attachment_type": "terminal_corridor" if in_corridor else "nearby_projection",
                "in_corridor": in_corridor,
                "distance": round(float(distance), 3),
                "forward_distance": round(float(forward_distance), 3),
                "lateral_distance": round(float(lateral_distance), 3),
                "projected_point": [round(float(proj_x), 3), round(float(proj_y), 3)],
                "orientation": edge.get("orientation"),
                "attachment_score": _attachment_score(
                    in_corridor,
                    forward_distance,
                    lateral_distance,
                    config_values["corridor_length"],
                    config_values["corridor_width"],
                    edge.get("orientation"),
                    pin,
                ),
                "evidence_score": edge.get("evidence_score"),
                "keep_reasons": edge.get("keep_reasons", []),
            }
        )
    return candidates


def _vertex_raw_component_id(
    vertex: dict,
    segment_to_component: dict[str, str],
    endpoint_to_component: dict[str, str],
    junction_to_component: dict[str, str],
) -> str | None:
    vertex_type = vertex.get("vertex_type")
    if vertex_type == "segment_endpoint":
        return segment_to_component.get(vertex.get("segment_id"))
    if vertex_type == "detected_endpoint":
        return endpoint_to_component.get(vertex.get("source_id"))
    if vertex_type == "detected_junction":
        return junction_to_component.get(vertex.get("source_id"))
    if vertex_type == "inferred_intersection":
        segment_ids = vertex.get("segment_ids", [])
        component_ids = [segment_to_component.get(segment_id) for segment_id in segment_ids]
        component_ids = [component_id for component_id in component_ids if component_id is not None]
        if len(set(component_ids)) == 1:
            return component_ids[0]
    return None


def _vertex_evidence_id(vertex: dict) -> str:
    if vertex.get("source_id"):
        return str(vertex["source_id"])
    if vertex.get("segment_id"):
        return f"{vertex['segment_id']}:{vertex.get('endpoint_role', 'point')}"
    return str(vertex["vertex_id"])


def _vertex_candidates(
    pin: dict,
    vertices: list[dict],
    segment_to_component: dict[str, str],
    endpoint_to_component: dict[str, str],
    junction_to_component: dict[str, str],
    config_values: dict,
) -> list[dict]:
    candidates = []
    for vertex in vertices:
        vertex_type = vertex.get("vertex_type")
        if vertex_type not in {
            "segment_endpoint",
            "detected_endpoint",
            "detected_junction",
            "inferred_intersection",
        }:
            continue
        target_x = float(vertex["x"])
        target_y = float(vertex["y"])
        distance = _distance(float(pin["x"]), float(pin["y"]), target_x, target_y)
        in_corridor, forward_distance, lateral_distance = _corridor_status(
            pin,
            target_x,
            target_y,
            config_values["corridor_length"],
            config_values["corridor_width"],
            config_values["corridor_backtrack"],
        )
        nearby = distance <= config_values["point_radius"]
        if not in_corridor and not nearby:
            continue
        raw_component_id = _vertex_raw_component_id(
            vertex,
            segment_to_component,
            endpoint_to_component,
            junction_to_component,
        )
        candidates.append(
            {
                "pin_id": pin["pin_id"],
                "component_id": pin["component_id"],
                "evidence_kind": vertex_type,
                "evidence_id": _vertex_evidence_id(vertex),
                "graph_vertex_id": vertex["vertex_id"],
                "raw_component_id": raw_component_id,
                "attachment_type": "terminal_corridor" if in_corridor else "nearby_point",
                "in_corridor": in_corridor,
                "distance": round(float(distance), 3),
                "forward_distance": round(float(forward_distance), 3),
                "lateral_distance": round(float(lateral_distance), 3),
                "projected_point": [int(vertex["x"]), int(vertex["y"])],
                "orientation": None,
                "attachment_score": _attachment_score(
                    in_corridor,
                    forward_distance,
                    lateral_distance,
                    config_values["corridor_length"],
                    config_values["corridor_width"],
                    None,
                    pin,
                ),
            }
        )
    return candidates


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    best_by_key: dict[tuple[str, str, str | None], dict] = {}
    for candidate in candidates:
        key = (
            str(candidate.get("evidence_kind")),
            str(candidate.get("evidence_id")),
            candidate.get("raw_component_id"),
        )
        existing = best_by_key.get(key)
        if existing is None or _candidate_sort_key(candidate) < _candidate_sort_key(existing):
            best_by_key[key] = candidate
    return list(best_by_key.values())


def _all_pins(pin_result: dict) -> list[dict]:
    pins = []
    for component_pins in pin_result.get("pins", []):
        for pin in component_pins.get("pins", []):
            pins.append(pin)
    return pins


def _config_values(config: dict) -> dict:
    topology_cfg = config["topology"]
    return {
        "candidate_limit": int(topology_cfg.get("terminal_attachment_candidate_limit", 8)),
        "corridor_length": float(topology_cfg.get("pin_corridor_length", 48)),
        "corridor_width": float(topology_cfg.get("pin_corridor_width", 18)),
        "corridor_backtrack": float(topology_cfg.get("pin_corridor_backtrack", 4)),
        "segment_radius": float(topology_cfg.get("pin_segment_match_radius", topology_cfg.get("pin_match_radius", 18))),
        "point_radius": float(topology_cfg.get("pin_endpoint_match_radius", topology_cfg.get("pin_match_radius", 18))),
    }


def build_terminal_attachments(pin_result: dict, evidence_graph: dict, config: dict) -> dict:
    """Build ranked terminal-to-evidence attachment candidates without mutating topology."""
    config_values = _config_values(config)
    segment_to_component, endpoint_to_component, junction_to_component = _raw_component_lookup(evidence_graph)
    segment_edges = [edge for edge in evidence_graph.get("edges", []) if edge.get("edge_type") == "segment"]
    vertices = evidence_graph.get("vertices", [])

    attachments = []
    candidates_by_pin = {}
    candidate_index = 1
    for pin in _all_pins(pin_result):
        candidates = [
            *_segment_candidates(pin, segment_edges, segment_to_component, config_values),
            *_vertex_candidates(
                pin,
                vertices,
                segment_to_component,
                endpoint_to_component,
                junction_to_component,
                config_values,
            ),
        ]
        candidates = sorted(_dedupe_candidates(candidates), key=_candidate_sort_key)
        candidates = candidates[: config_values["candidate_limit"]]
        for candidate in candidates:
            candidate["attachment_id"] = f"TA{candidate_index}"
            candidate_index += 1
        best_candidate = candidates[0] if candidates else None
        attachment = {
            "pin_id": pin["pin_id"],
            "component_id": pin["component_id"],
            "x": int(pin["x"]),
            "y": int(pin["y"]),
            "side": pin.get("side"),
            "axis": pin.get("axis"),
            "pin_confidence": pin.get("confidence"),
            "candidate_count": len(candidates),
            "best_attachment_id": best_candidate.get("attachment_id") if best_candidate else None,
            "best_raw_component_id": best_candidate.get("raw_component_id") if best_candidate else None,
            "best_evidence_kind": best_candidate.get("evidence_kind") if best_candidate else None,
            "best_evidence_id": best_candidate.get("evidence_id") if best_candidate else None,
            "best_attachment_score": best_candidate.get("attachment_score") if best_candidate else 0.0,
            "candidates": candidates,
        }
        attachments.append(attachment)
        candidates_by_pin[pin["pin_id"]] = candidates

    attached_count = sum(1 for attachment in attachments if attachment["candidate_count"] > 0)
    corridor_candidate_count = sum(
        1
        for attachment in attachments
        for candidate in attachment["candidates"]
        if candidate.get("in_corridor")
    )
    return {
        "attachments": attachments,
        "candidates_by_pin": candidates_by_pin,
        "stats": {
            "pin_count": len(attachments),
            "attached_pin_count": attached_count,
            "unattached_pin_count": len(attachments) - attached_count,
            "candidate_count": sum(attachment["candidate_count"] for attachment in attachments),
            "corridor_candidate_count": corridor_candidate_count,
            "raw_component_count": len(evidence_graph.get("raw_components", [])),
        },
        "config": config_values,
    }
