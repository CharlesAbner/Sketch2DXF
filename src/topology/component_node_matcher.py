"""
本文件的作用：
- 将元件 pins 与恢复出的 electrical nodes 建立匹配关系。
- 当前策略以最近邻为主，但距离是基于 node 所属导线几何计算，而不是只看 node 中心点。

建议说明：
- 第一版先保证基础场景能稳定匹配。
- 后续可继续加入方向约束、side-aware 匹配和更复杂的置信度策略。
"""

from __future__ import annotations

from math import hypot


def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Compute Euclidean distance between two planar points."""
    return hypot(x1 - x2, y1 - y2)


def _closest_point_on_segment(px: float, py: float, segment: dict) -> tuple[float, float, float]:
    x1 = float(segment["x1"])
    y1 = float(segment["y1"])
    x2 = float(segment["x2"])
    y2 = float(segment["y2"])
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return x1, y1, _distance(px, py, x1, y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return proj_x, proj_y, _distance(px, py, proj_x, proj_y)


def _point_to_segment_distance(px: float, py: float, segment: dict) -> float:
    return _closest_point_on_segment(px, py, segment)[2]


def _side_alignment_score(pin: dict, target_x: float, target_y: float, axis_tolerance: float) -> float:
    dx = float(target_x) - float(pin["x"])
    dy = float(target_y) - float(pin["y"])
    side = pin.get("side")

    if side == "left":
        if dx > axis_tolerance:
            return 0.0
        return max(0.0, 1.0 - abs(dy) / max(axis_tolerance, 1.0))
    if side == "right":
        if dx < -axis_tolerance:
            return 0.0
        return max(0.0, 1.0 - abs(dy) / max(axis_tolerance, 1.0))
    if side == "top":
        if dy > axis_tolerance:
            return 0.0
        return max(0.0, 1.0 - abs(dx) / max(axis_tolerance, 1.0))
    if side == "bottom":
        if dy < -axis_tolerance:
            return 0.0
        return max(0.0, 1.0 - abs(dx) / max(axis_tolerance, 1.0))
    return 0.5


def _segment_alignment_score(pin: dict, segment: dict, axis_tolerance: float) -> float:
    orientation = segment.get("orientation")
    side = pin.get("side")
    preferred_orientation = "h" if side in {"left", "right"} else "v"
    orientation_score = 1.0 if orientation == preferred_orientation else 0.4
    proj_x, proj_y, _ = _closest_point_on_segment(float(pin["x"]), float(pin["y"]), segment)
    axis_score = _side_alignment_score(
        pin,
        proj_x,
        proj_y,
        axis_tolerance,
    )
    return round(float(orientation_score * axis_score), 3)


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


def _corridor_alignment_score(
    pin: dict,
    target_x: float,
    target_y: float,
    corridor_width: float,
    orientation: str | None = None,
) -> float:
    metrics = _ray_metrics(pin, target_x, target_y)
    if metrics is None:
        return 0.0
    _, lateral_distance = metrics
    lateral_score = max(0.0, 1.0 - lateral_distance / max(corridor_width, 1.0))
    preferred_orientation = _preferred_orientation(pin)
    if orientation is None or preferred_orientation is None:
        orientation_score = 0.8
    else:
        orientation_score = 1.0 if orientation == preferred_orientation else 0.45
    return round(float(lateral_score * orientation_score), 3)


def _is_in_pin_corridor(
    pin: dict,
    target_x: float,
    target_y: float,
    corridor_length: float,
    corridor_width: float,
    backtrack: float,
) -> bool:
    metrics = _ray_metrics(pin, target_x, target_y)
    if metrics is None:
        return False
    forward_distance, lateral_distance = metrics
    return -backtrack <= forward_distance <= corridor_length and lateral_distance <= corridor_width


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    best_by_evidence: dict[tuple[str, str, str], dict] = {}
    for candidate in candidates:
        key = (
            str(candidate.get("node_id")),
            str(candidate.get("evidence_type")),
            str(candidate.get("evidence_id")),
        )
        existing = best_by_evidence.get(key)
        if existing is None:
            best_by_evidence[key] = candidate
            continue
        candidate_rank = (float(candidate.get("confidence", 0.0)), -float(candidate.get("distance", 0.0)))
        existing_rank = (float(existing.get("confidence", 0.0)), -float(existing.get("distance", 0.0)))
        if candidate_rank > existing_rank:
            best_by_evidence[key] = candidate
    return list(best_by_evidence.values())


def _distance_to_node_geometry(pin: dict, node: dict, wire_segments: dict[str, dict]) -> float:
    best_distance = _distance(
        float(pin["x"]),
        float(pin["y"]),
        float(node["x"]),
        float(node["y"]),
    )
    for segment_id in node.get("segment_ids", []):
        segment = wire_segments.get(segment_id)
        if segment is None:
            continue
        current_distance = _point_to_segment_distance(float(pin["x"]), float(pin["y"]), segment)
        if current_distance < best_distance:
            best_distance = current_distance
    return best_distance


def _confidence(distance: float, radius: float, alignment_score: float) -> float:
    if radius <= 0:
        return round(float(alignment_score), 3)
    distance_score = max(0.0, 1.0 - (distance / radius))
    return round(float(0.65 * distance_score + 0.35 * alignment_score), 3)


def match_components_to_nodes(
    pin_result: dict,
    node_result: dict,
    wire_result: dict,
    junction_result: dict,
    config: dict,
) -> dict:
    """Match component terminals to the best nearby evidence, then map them onto electrical nodes."""
    matches: list[dict] = []
    candidates_by_pin: dict[str, list[dict]] = {}
    max_radius = float(config["topology"]["pin_match_radius"])
    endpoint_radius = float(config["topology"].get("pin_endpoint_match_radius", max_radius))
    segment_radius = float(config["topology"].get("pin_segment_match_radius", max_radius))
    node_radius = float(config["topology"].get("pin_node_match_radius", max_radius))
    axis_tolerance = float(config["topology"].get("pin_axis_alignment_tolerance", 10))
    candidate_limit = int(config["topology"].get("pin_match_candidate_limit", 5))
    min_match_confidence = float(config["topology"].get("pin_match_min_confidence", 0.0))
    corridor_enabled = bool(config["topology"].get("pin_corridor_enabled", True))
    corridor_length = float(config["topology"].get("pin_corridor_length", 48))
    corridor_width = float(config["topology"].get("pin_corridor_width", 18))
    corridor_backtrack = float(config["topology"].get("pin_corridor_backtrack", 4))
    nodes = node_result["nodes"]
    endpoints = junction_result.get("endpoints", [])
    wire_segments = {
        segment["id"]: segment
        for segment in wire_result.get("segments", [])
    }
    endpoint_to_node = {}
    segment_to_node = {}
    for node in nodes:
        for endpoint_id in node.get("endpoint_ids", []):
            endpoint_to_node[endpoint_id] = node["node_id"]
        for segment_id in node.get("segment_ids", []):
            segment_to_node[segment_id] = node["node_id"]

    for component_pins in pin_result["pins"]:
        component_id = component_pins["component_id"]
        for pin in component_pins["pins"]:
            candidates = []

            for endpoint in endpoints:
                current_distance = _distance(
                    float(pin["x"]),
                    float(pin["y"]),
                    float(endpoint["x"]),
                    float(endpoint["y"]),
                )
                if current_distance > endpoint_radius:
                    continue
                node_id = endpoint_to_node.get(endpoint["id"])
                if node_id is None:
                    continue
                alignment_score = _side_alignment_score(pin, float(endpoint["x"]), float(endpoint["y"]), axis_tolerance)
                confidence = _confidence(current_distance, endpoint_radius, alignment_score)
                candidates.append(
                    {
                        "pin_id": pin["pin_id"],
                        "component_id": component_id,
                        "node_id": node_id,
                        "distance": round(float(current_distance), 3),
                        "match_type": "best_evidence",
                        "evidence_type": "endpoint",
                        "evidence_id": endpoint["id"],
                        "alignment_score": alignment_score,
                        "confidence": confidence,
                        "match_radius": endpoint_radius,
                        "projected_point": [int(endpoint["x"]), int(endpoint["y"])],
                    }
                )

            for segment in wire_result.get("segments", []):
                proj_x, proj_y, current_distance = _closest_point_on_segment(float(pin["x"]), float(pin["y"]), segment)
                if current_distance > segment_radius:
                    continue
                node_id = segment_to_node.get(segment["id"])
                if node_id is None:
                    continue
                alignment_score = _segment_alignment_score(pin, segment, axis_tolerance)
                confidence = _confidence(current_distance, segment_radius, alignment_score)
                candidates.append(
                    {
                        "pin_id": pin["pin_id"],
                        "component_id": component_id,
                        "node_id": node_id,
                        "distance": round(float(current_distance), 3),
                        "match_type": "best_evidence",
                        "evidence_type": "segment",
                        "evidence_id": segment["id"],
                        "alignment_score": alignment_score,
                        "confidence": confidence,
                        "match_radius": segment_radius,
                        "projected_point": [round(float(proj_x), 3), round(float(proj_y), 3)],
                        "evidence_score": segment.get("evidence_score"),
                        "keep_reasons": segment.get("keep_reasons", []),
                    }
                )

            if corridor_enabled:
                for endpoint in endpoints:
                    node_id = endpoint_to_node.get(endpoint["id"])
                    if node_id is None:
                        continue
                    target_x = float(endpoint["x"])
                    target_y = float(endpoint["y"])
                    if not _is_in_pin_corridor(
                        pin,
                        target_x,
                        target_y,
                        corridor_length,
                        corridor_width,
                        corridor_backtrack,
                    ):
                        continue
                    current_distance = _distance(float(pin["x"]), float(pin["y"]), target_x, target_y)
                    alignment_score = _corridor_alignment_score(pin, target_x, target_y, corridor_width)
                    confidence = _confidence(current_distance, corridor_length, alignment_score)
                    candidates.append(
                        {
                            "pin_id": pin["pin_id"],
                            "component_id": component_id,
                            "node_id": node_id,
                            "distance": round(float(current_distance), 3),
                            "match_type": "terminal_corridor",
                            "evidence_type": "endpoint",
                            "evidence_id": endpoint["id"],
                            "alignment_score": alignment_score,
                            "confidence": confidence,
                            "match_radius": corridor_length,
                            "projected_point": [int(endpoint["x"]), int(endpoint["y"])],
                        }
                    )

                for segment in wire_result.get("segments", []):
                    node_id = segment_to_node.get(segment["id"])
                    if node_id is None:
                        continue
                    proj_x, proj_y, current_distance = _closest_point_on_segment(
                        float(pin["x"]),
                        float(pin["y"]),
                        segment,
                    )
                    if not _is_in_pin_corridor(
                        pin,
                        proj_x,
                        proj_y,
                        corridor_length,
                        corridor_width,
                        corridor_backtrack,
                    ):
                        continue
                    alignment_score = _corridor_alignment_score(
                        pin,
                        proj_x,
                        proj_y,
                        corridor_width,
                        segment.get("orientation"),
                    )
                    confidence = _confidence(current_distance, corridor_length, alignment_score)
                    candidates.append(
                        {
                            "pin_id": pin["pin_id"],
                            "component_id": component_id,
                            "node_id": node_id,
                            "distance": round(float(current_distance), 3),
                            "match_type": "terminal_corridor",
                            "evidence_type": "segment",
                            "evidence_id": segment["id"],
                            "alignment_score": alignment_score,
                            "confidence": confidence,
                            "match_radius": corridor_length,
                            "projected_point": [round(float(proj_x), 3), round(float(proj_y), 3)],
                            "evidence_score": segment.get("evidence_score"),
                            "keep_reasons": segment.get("keep_reasons", []),
                        }
                    )

            for node in nodes:
                current_distance = _distance_to_node_geometry(pin, node, wire_segments)
                if current_distance > node_radius:
                    continue
                alignment_score = _side_alignment_score(pin, float(node["x"]), float(node["y"]), axis_tolerance)
                confidence = _confidence(current_distance, node_radius, alignment_score * 0.85)
                candidates.append(
                    {
                        "pin_id": pin["pin_id"],
                        "component_id": component_id,
                        "node_id": node["node_id"],
                        "distance": round(float(current_distance), 3),
                        "match_type": "best_evidence",
                        "evidence_type": "node",
                        "evidence_id": node["node_id"],
                        "alignment_score": round(float(alignment_score * 0.85), 3),
                        "confidence": confidence,
                        "match_radius": node_radius,
                        "projected_point": [int(node["x"]), int(node["y"])],
                        "node_confidence": node.get("node_confidence"),
                    }
                )

            candidates = sorted(
                _dedupe_candidates(candidates),
                key=lambda item: (-float(item["confidence"]), float(item["distance"])),
            )
            candidates_by_pin[pin["pin_id"]] = candidates[:candidate_limit]
            best_match = candidates[0] if candidates else None
            if best_match is not None and float(best_match.get("confidence", 0.0)) >= min_match_confidence:
                matches.append(best_match)

    return {
        "matches": matches,
        "candidates_by_pin": candidates_by_pin,
        "stats": {
            "match_count": len(matches),
            "candidate_count": sum(len(candidates) for candidates in candidates_by_pin.values()),
        },
    }
