"""Lightweight component classification wrapper.

The classifier does not overwrite YOLO's selected class.  When YOLO/NMS
suppresses overlapping alternatives, those alternatives are preserved as
audit evidence for the agent layer.
"""

from __future__ import annotations


def _class_candidate(proposal: dict, status: str, overlap_iou: float | None = None) -> dict:
    candidate = {
        "class_name": proposal.get("class_name"),
        "score": proposal.get("score"),
        "bbox": proposal.get("bbox"),
        "class_id": proposal.get("class_id"),
        "candidate_status": status,
        "source": proposal.get("source"),
    }
    if overlap_iou is not None:
        candidate["overlap_iou_with_kept"] = round(float(overlap_iou), 3)
    if proposal.get("suppression_reason"):
        candidate["suppression_reason"] = proposal.get("suppression_reason")
    return candidate


def _bbox_iou(bbox_a: list[int] | None, bbox_b: list[int] | None) -> float:
    if not bbox_a or not bbox_b or len(bbox_a) < 4 or len(bbox_b) < 4:
        return 0.0
    ax1, ay1, ax2, ay2 = [float(value) for value in bbox_a[:4]]
    bx1, by1, bx2, by2 = [float(value) for value in bbox_b[:4]]
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    if union <= 0.0:
        return 0.0
    return inter_area / union


def _class_candidates_for(proposal: dict, suppressed: list[dict]) -> list[dict]:
    if proposal.get("class_candidates"):
        return proposal["class_candidates"]
    candidates = [_class_candidate(proposal, "kept")]
    alternatives = []
    for candidate in suppressed:
        iou = _bbox_iou(proposal.get("bbox"), candidate.get("bbox"))
        if iou <= 0.0:
            continue
        alternatives.append(_class_candidate(candidate, "suppressed_duplicate", iou))
    alternatives.sort(
        key=lambda item: (
            -float(item.get("overlap_iou_with_kept", 0.0) or 0.0),
            -float(item.get("score", 0.0) or 0.0),
        )
    )
    return [candidates[0], *alternatives]


def classify_component_proposals(proposal_result: dict, config: dict) -> dict:
    """Classify proposals without mutating selected class labels."""
    _ = config
    suppressed = proposal_result.get("stats", {}).get("suppressed_duplicates", [])
    classified = []
    for proposal in proposal_result["proposals"]:
        class_candidates = _class_candidates_for(proposal, suppressed)
        alternatives = class_candidates[1:]
        component = {
            **proposal,
            "class_name": proposal.get("class_name", "unknown"),
            "score": proposal.get("score", 0.0),
            "orientation": proposal.get("orientation", "unknown"),
            "class_candidates": class_candidates,
        }
        if alternatives:
            component["class_alternatives"] = alternatives
        classified.append(component)
    return {"components": classified}
