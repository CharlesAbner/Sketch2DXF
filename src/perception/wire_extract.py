"""
本文件的作用：
- 从骨架图中恢复导线向量线段，并重绘为干净的 wire mask。
- 当前实现采用 HoughLinesP + 共线合并 + 角点规整，适合手绘电路图场景。
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def _unique(items: list) -> list:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _with_evidence_metadata(
    segment: dict,
    keep_reason: str,
    evidence_score: float,
    noise_flags: list[str] | None = None,
) -> dict:
    updated = {**segment}
    updated["evidence_type"] = updated.get("evidence_type", "hough_segment")
    updated["source_segment_ids"] = updated.get("source_segment_ids", [updated["id"]])
    updated["keep_reasons"] = _unique([*updated.get("keep_reasons", []), keep_reason])
    updated["noise_flags"] = _unique([*updated.get("noise_flags", []), *(noise_flags or [])])
    updated["evidence_score"] = round(max(float(updated.get("evidence_score", 0.0)), evidence_score), 3)
    return updated


def _merge_metadata(seg_a: dict, seg_b: dict) -> dict:
    source_ids = _unique(
        [
            *seg_a.get("source_segment_ids", [seg_a["id"]]),
            *seg_b.get("source_segment_ids", [seg_b["id"]]),
        ]
    )
    keep_reasons = _unique([*seg_a.get("keep_reasons", []), *seg_b.get("keep_reasons", []), "merged_collinear"])
    noise_flags = _unique([*seg_a.get("noise_flags", []), *seg_b.get("noise_flags", [])])
    evidence_score = min(
        1.0,
        max(
            float(seg_a.get("evidence_score", 0.5)),
            float(seg_b.get("evidence_score", 0.5)),
        )
        + 0.03,
    )
    return {
        "evidence_type": "wire_evidence",
        "source_segment_ids": source_ids,
        "keep_reasons": keep_reasons,
        "noise_flags": noise_flags,
        "evidence_score": round(float(evidence_score), 3),
    }


def mask_out_components(skeleton: np.ndarray, proposals: list[dict]) -> np.ndarray:
    """Erase component regions from the skeleton so only connection wires remain."""
    connections_only = skeleton.copy()
    for proposal in proposals:
        x1, y1, x2, y2 = proposal["bbox"]
        cv2.rectangle(
            connections_only,
            (int(x1), int(y1)),
            (int(x2), int(y2)),
            0,
            -1,
        )
    return connections_only


def _segment_bbox(segment: dict) -> tuple[int, int, int, int]:
    return (
        int(min(segment["x1"], segment["x2"])),
        int(min(segment["y1"], segment["y2"])),
        int(max(segment["x1"], segment["x2"])),
        int(max(segment["y1"], segment["y2"])),
    )


def _segment_near_proposal(segment: dict, proposal: dict, margin: int) -> bool:
    sx1, sy1, sx2, sy2 = _segment_bbox(segment)
    px1, py1, px2, py2 = [int(v) for v in proposal["bbox"]]
    return not (
        sx2 < px1 - margin
        or sx1 > px2 + margin
        or sy2 < py1 - margin
        or sy1 > py2 + margin
    )


def _bridges_two_components(segment: dict, proposals: list[dict], margin: int) -> bool:
    if segment.get("orientation") == "h":
        left_candidates: list[str] = []
        right_candidates: list[str] = []
        x1 = int(segment["x1"])
        x2 = int(segment["x2"])
        y = int(segment["y1"])
        for proposal in proposals:
            proposal_id = str(proposal["id"])
            px1, py1, px2, py2 = [int(v) for v in proposal["bbox"]]
            if py1 - margin <= y <= py2 + margin:
                if abs(x1 - px2) <= margin or px2 < x1 <= px2 + margin:
                    left_candidates.append(proposal_id)
                if abs(x2 - px1) <= margin or px1 - margin <= x2 < px1:
                    right_candidates.append(proposal_id)
        return any(left_id != right_id for left_id in left_candidates for right_id in right_candidates)

    if segment.get("orientation") == "v":
        top_candidates: list[str] = []
        bottom_candidates: list[str] = []
        x = int(segment["x1"])
        y1 = int(segment["y1"])
        y2 = int(segment["y2"])
        for proposal in proposals:
            proposal_id = str(proposal["id"])
            px1, py1, px2, py2 = [int(v) for v in proposal["bbox"]]
            if px1 - margin <= x <= px2 + margin:
                if abs(y1 - py2) <= margin or py2 < y1 <= py2 + margin:
                    top_candidates.append(proposal_id)
                if abs(y2 - py1) <= margin or py1 - margin <= y2 < py1:
                    bottom_candidates.append(proposal_id)
        return any(top_id != bottom_id for top_id in top_candidates for bottom_id in bottom_candidates)

    return False


def _has_collinear_support(segment: dict, segments: list[dict], axis_gap: int, support_gap: int) -> bool:
    for other in segments:
        if other is segment:
            continue
        if other.get("orientation") != segment.get("orientation"):
            continue

        if segment.get("orientation") == "h":
            if abs(int(segment["y1"]) - int(other["y1"])) > axis_gap:
                continue
            if (
                abs(int(segment["x1"]) - int(other["x2"])) <= support_gap
                or abs(int(segment["x2"]) - int(other["x1"])) <= support_gap
            ):
                return True
        else:
            if abs(int(segment["x1"]) - int(other["x1"])) > axis_gap:
                continue
            if (
                abs(int(segment["y1"]) - int(other["y2"])) <= support_gap
                or abs(int(segment["y2"]) - int(other["y1"])) <= support_gap
            ):
                return True
    return False


def score_and_filter_wire_segments(segments: list[dict], proposals: list[dict], config: dict) -> list[dict]:
    """Remove near-component noise and attach evidence metadata to kept segments."""
    perception_cfg = config["perception"]
    min_length = int(perception_cfg["wire_segment_min_length"])
    bridge_margin = int(perception_cfg["wire_bridge_margin"])
    axis_gap = int(perception_cfg["wire_support_axis_gap"])
    support_gap = int(perception_cfg["wire_support_gap"])
    near_component_margin = int(perception_cfg["wire_noise_near_component_margin"])

    kept: list[dict] = []
    kept_bridge_count = 0
    kept_supported_count = 0
    removed_noise_count = 0
    for segment in segments:
        if int(segment["length"]) >= min_length:
            kept.append(_with_evidence_metadata(segment, "long", 0.75))
            continue

        if _bridges_two_components(segment, proposals, bridge_margin):
            kept.append(_with_evidence_metadata(segment, "bridge_candidate", 0.85))
            kept_bridge_count += 1
            continue

        if _has_collinear_support(segment, segments, axis_gap, support_gap):
            kept.append(_with_evidence_metadata(segment, "collinear_support", 0.7))
            kept_supported_count += 1
            continue

        if any(_segment_near_proposal(segment, proposal, near_component_margin) for proposal in proposals):
            removed_noise_count += 1
            continue

        kept.append(_with_evidence_metadata(segment, "retained_unclassified", 0.45, ["low_structural_support"]))

    return kept, {
        "kept_bridge_segment_count": kept_bridge_count,
        "kept_supported_segment_count": kept_supported_count,
        "removed_near_component_noise_count": removed_noise_count,
    }


def _get_orientation(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    angle_thresh: float,
) -> str | None:
    """Normalize a nearly-horizontal or nearly-vertical line segment to h/v."""
    if x1 == x2:
        return "v"
    if y1 == y2:
        return "h"

    angle = math.degrees(math.atan2(abs(y2 - y1), abs(x2 - x1)))
    if angle <= angle_thresh:
        return "h"
    if angle >= 90 - angle_thresh:
        return "v"
    return None


def extract_hough_segments(
    skeleton: np.ndarray,
    min_length: int,
    max_gap: int,
    threshold: int,
    angle_thresh: float,
) -> list[dict]:
    """Extract initial wire segments with a probabilistic Hough transform."""
    lines = cv2.HoughLinesP(
        skeleton,
        rho=1,
        theta=np.pi / 180,
        threshold=threshold,
        minLineLength=min_length,
        maxLineGap=max_gap,
    )

    segments: list[dict] = []
    if lines is None:
        return segments

    for index, line in enumerate(lines):
        x1, y1, x2, y2 = line[0]
        orientation = _get_orientation(x1, y1, x2, y2, angle_thresh)
        if not orientation:
            continue

        if orientation == "h" and x1 > x2:
            x1, x2, y1, y2 = x2, x1, y2, y1
        elif orientation == "v" and y1 > y2:
            x1, x2, y1, y2 = x2, x1, y2, y1

        if orientation == "h":
            y = int(round((y1 + y2) / 2))
            y1 = y2 = y
        else:
            x = int(round((x1 + x2) / 2))
            x1 = x2 = x

        length = int(math.hypot(x2 - x1, y2 - y1))
        segments.append(
            {
                "id": f"h{index}",
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "orientation": orientation,
                "bbox": [
                    int(min(x1, x2)),
                    int(min(y1, y2)),
                    int(max(x1, x2) + 1),
                    int(max(y1, y2) + 1),
                ],
                "length": length,
                "area": length,
                "evidence_type": "hough_segment",
                "source_segment_ids": [f"h{index}"],
                "keep_reasons": ["hough_detected"],
                "noise_flags": [],
                "evidence_score": 0.5,
            }
        )
    return segments


def _can_merge(seg_a: dict, seg_b: dict, axis_gap: int, endpoint_gap: int) -> bool:
    """Check whether two collinear segments can be merged."""
    if seg_a["orientation"] != seg_b["orientation"]:
        return False

    if seg_a["orientation"] == "h":
        same_axis = abs(seg_a["y1"] - seg_b["y1"]) <= axis_gap
        close = min(
            abs(seg_a["x2"] - seg_b["x1"]),
            abs(seg_b["x2"] - seg_a["x1"]),
        ) <= endpoint_gap
        overlap = not (
            seg_a["x2"] < seg_b["x1"] - endpoint_gap
            or seg_b["x2"] < seg_a["x1"] - endpoint_gap
        )
        return same_axis and (close or overlap)

    same_axis = abs(seg_a["x1"] - seg_b["x1"]) <= axis_gap
    close = min(
        abs(seg_a["y2"] - seg_b["y1"]),
        abs(seg_b["y2"] - seg_a["y1"]),
    ) <= endpoint_gap
    overlap = not (
        seg_a["y2"] < seg_b["y1"] - endpoint_gap
        or seg_b["y2"] < seg_a["y1"] - endpoint_gap
    )
    return same_axis and (close or overlap)


def _merge_pair(seg_a: dict, seg_b: dict, new_id: str) -> dict:
    if seg_a["orientation"] == "h":
        x1 = min(seg_a["x1"], seg_b["x1"])
        x2 = max(seg_a["x2"], seg_b["x2"])
        y = int(round((seg_a["y1"] + seg_b["y1"]) / 2))
        return {
            **seg_a,
            **_merge_metadata(seg_a, seg_b),
            "id": new_id,
            "x1": x1,
            "x2": x2,
            "y1": y,
            "y2": y,
            "length": x2 - x1 + 1,
            "bbox": [x1, y, x2 + 1, y + 1],
            "area": seg_a.get("area", 0) + seg_b.get("area", 0),
        }

    y1 = min(seg_a["y1"], seg_b["y1"])
    y2 = max(seg_a["y2"], seg_b["y2"])
    x = int(round((seg_a["x1"] + seg_b["x1"]) / 2))
    return {
        **seg_a,
        **_merge_metadata(seg_a, seg_b),
        "id": new_id,
        "x1": x,
        "x2": x,
        "y1": y1,
        "y2": y2,
        "length": y2 - y1 + 1,
        "bbox": [x, y1, x + 1, y2 + 1],
        "area": seg_a.get("area", 0) + seg_b.get("area", 0),
    }


def merge_collinear_segments(
    segments: list[dict],
    axis_gap: int = 5,
    endpoint_gap: int = 50,
) -> list[dict]:
    """Repeatedly merge all collinear segments that satisfy the merge rule."""
    merged = segments[:]
    changed = True
    next_index = len(segments) + 1

    while changed:
        changed = False
        result: list[dict] = []
        used = [False] * len(merged)
        for i, seg_a in enumerate(merged):
            if used[i]:
                continue
            current = seg_a
            for j in range(i + 1, len(merged)):
                if used[j]:
                    continue
                seg_b = merged[j]
                if _can_merge(current, seg_b, axis_gap, endpoint_gap):
                    current = _merge_pair(current, seg_b, f"w{next_index}")
                    used[j] = True
                    changed = True
                    next_index += 1
            used[i] = True
            result.append(current)
        merged = result
    return merged


def _segment_with_updated_geometry(segment: dict) -> dict:
    updated = {**segment}
    if updated["orientation"] == "h":
        x1 = min(int(updated["x1"]), int(updated["x2"]))
        x2 = max(int(updated["x1"]), int(updated["x2"]))
        y = int(round((int(updated["y1"]) + int(updated["y2"])) / 2))
        updated["x1"] = x1
        updated["x2"] = x2
        updated["y1"] = y
        updated["y2"] = y
        updated["length"] = x2 - x1 + 1
        updated["bbox"] = [x1, y, x2 + 1, y + 1]
        updated["area"] = updated["length"]
        return updated

    y1 = min(int(updated["y1"]), int(updated["y2"]))
    y2 = max(int(updated["y1"]), int(updated["y2"]))
    x = int(round((int(updated["x1"]) + int(updated["x2"])) / 2))
    updated["x1"] = x
    updated["x2"] = x
    updated["y1"] = y1
    updated["y2"] = y2
    updated["length"] = y2 - y1 + 1
    updated["bbox"] = [x, y1, x + 1, y2 + 1]
    updated["area"] = updated["length"]
    return updated


def _nearest_endpoint_name(segment: dict, x: int, y: int) -> tuple[str, int]:
    endpoints = {
        "start": abs(int(segment["x1"]) - x) + abs(int(segment["y1"]) - y),
        "end": abs(int(segment["x2"]) - x) + abs(int(segment["y2"]) - y),
    }
    endpoint_name = min(endpoints, key=endpoints.get)
    return endpoint_name, endpoints[endpoint_name]


def _set_endpoint(segment: dict, endpoint_name: str, x: int, y: int) -> None:
    if endpoint_name == "start":
        segment["x1"] = int(x)
        segment["y1"] = int(y)
    else:
        segment["x2"] = int(x)
        segment["y2"] = int(y)


def regularize_orthogonal_corners(
    segments: list[dict],
    corner_gap: int = 8,
    axis_slack: int = 6,
    extension_gap: int = 12,
) -> list[dict]:
    """Snap near-orthogonal segment endpoints onto shared corner intersections."""
    regularized = [{**segment} for segment in segments]
    horizontals = [segment for segment in regularized if segment.get("orientation") == "h"]
    verticals = [segment for segment in regularized if segment.get("orientation") == "v"]

    for h_seg in horizontals:
        for v_seg in verticals:
            corner_x = int(v_seg["x1"])
            corner_y = int(h_seg["y1"])

            h_endpoint, h_distance = _nearest_endpoint_name(h_seg, corner_x, corner_y)
            v_endpoint, v_distance = _nearest_endpoint_name(v_seg, corner_x, corner_y)

            horizontal_near = h_distance <= corner_gap
            vertical_near = v_distance <= corner_gap
            horizontal_extendable = h_distance <= extension_gap
            vertical_extendable = v_distance <= extension_gap
            axis_aligned = (
                min(int(v_seg["y1"]), int(v_seg["y2"])) - axis_slack
                <= corner_y
                <= max(int(v_seg["y1"]), int(v_seg["y2"])) + axis_slack
                and min(int(h_seg["x1"]), int(h_seg["x2"])) - axis_slack
                <= corner_x
                <= max(int(h_seg["x1"]), int(h_seg["x2"])) + axis_slack
            )

            if axis_aligned and (
                (horizontal_near and vertical_near)
                or (horizontal_near and vertical_extendable)
                or (vertical_near and horizontal_extendable)
            ):
                _set_endpoint(h_seg, h_endpoint, corner_x, corner_y)
                _set_endpoint(v_seg, v_endpoint, corner_x, corner_y)

    return [_segment_with_updated_geometry(segment) for segment in regularized]


def extract_wires(preprocess_result: dict, proposal_result: dict, config: dict) -> dict:
    """Erase components, recover clean wire segments, then redraw a wire mask."""
    perception_cfg = config["perception"]
    skeleton = preprocess_result["skeleton"]
    proposals = proposal_result["proposals"]
    connections_only = mask_out_components(skeleton, proposals)

    raw_segments = extract_hough_segments(
        connections_only,
        min_length=int(perception_cfg["hough_min_line_length"]),
        max_gap=int(perception_cfg["hough_max_gap"]),
        threshold=int(perception_cfg["hough_threshold"]),
        angle_thresh=float(perception_cfg["wire_orientation_angle_thresh"]),
    )
    filtered_segments, filter_stats = score_and_filter_wire_segments(raw_segments, proposals, config)
    merged_segments = merge_collinear_segments(
        filtered_segments,
        axis_gap=int(perception_cfg["wire_merge_axis_gap"]),
        endpoint_gap=int(perception_cfg["wire_merge_endpoint_gap"]),
    )
    regularized_segments = regularize_orthogonal_corners(
        merged_segments,
        corner_gap=int(perception_cfg["wire_corner_gap"]),
        axis_slack=int(perception_cfg["wire_corner_axis_slack"]),
        extension_gap=int(perception_cfg["wire_corner_extension_gap"]),
    )

    wire_mask = np.zeros_like(skeleton)
    for segment in regularized_segments:
        cv2.line(
            wire_mask,
            (segment["x1"], segment["y1"]),
            (segment["x2"], segment["y2"]),
            255,
            1,
        )

    return {
        "connections_only": connections_only,
        "wire_mask": wire_mask,
        "raw_segments": raw_segments,
        "filtered_segments": filtered_segments,
        "segments": regularized_segments,
        "stats": {
            "raw_segment_count": len(raw_segments),
            "filtered_segment_count": len(filtered_segments),
            "segment_count": len(regularized_segments),
            "horizontal_count": sum(1 for seg in regularized_segments if seg.get("orientation") == "h"),
            "vertical_count": sum(1 for seg in regularized_segments if seg.get("orientation") == "v"),
            **filter_stats,
        },
    }
