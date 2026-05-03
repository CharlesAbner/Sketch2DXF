"""
本文件的作用：
- 封装 YOLO proposal backend，把检测结果转换成项目内部的 proposal 结构。
- 这是一个薄适配层，尽量不把 Ultralytics 细节扩散到主流程其他文件里。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


_MODEL_CACHE: dict[str, Any] = {}


def _resolve_weights_path(weights_value: str | None) -> Path:
    if not weights_value:
        raise ValueError("YOLO proposal backend requires detector.yolo_weights to be configured.")
    weights_path = Path(weights_value)
    if not weights_path.is_absolute():
        weights_path = Path(__file__).resolve().parents[2] / weights_path
    if not weights_path.exists():
        raise FileNotFoundError(f"YOLO weights not found: {weights_path}")
    return weights_path


def _get_model(weights_path: Path):
    cache_key = str(weights_path.resolve())
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise ImportError(
            "ultralytics is required for the YOLO proposal backend. "
            "Install it in the active environment first."
        ) from exc

    model = YOLO(str(weights_path))
    _MODEL_CACHE[cache_key] = model
    return model


def _bbox_iou(bbox_a: list[int], bbox_b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = bbox_a
    bx1, by1, bx2, by2 = bbox_b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union


def _dedupe_overlapping_proposals(
    proposals: list[dict[str, Any]],
    duplicate_iou: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sorted_proposals = sorted(
        proposals,
        key=lambda item: float(item.get("score", 0.0)),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for proposal in sorted_proposals:
        duplicate_of = None
        duplicate_iou_value = 0.0
        for kept_proposal in kept:
            current_iou = _bbox_iou(proposal["bbox"], kept_proposal["bbox"])
            if current_iou >= duplicate_iou:
                duplicate_of = kept_proposal
                duplicate_iou_value = current_iou
                break
        if duplicate_of is None:
            kept.append(proposal)
            continue
        suppressed.append(
            {
                **proposal,
                "suppressed_by": duplicate_of["id"],
                "suppression_reason": "duplicate_overlapping_component",
                "suppression_iou": round(float(duplicate_iou_value), 3),
            }
        )

    kept_in_original_order = sorted(kept, key=lambda item: proposals.index(item))
    for index, proposal in enumerate(kept_in_original_order):
        proposal["id"] = f"cp_{index + 1}"
    return kept_in_original_order, suppressed


def extract_component_proposals_yolo(preprocess_result: dict, config: dict) -> dict:
    """Run YOLO detection and convert boxes into internal proposal objects."""
    detector_cfg = config.get("detector", {})
    weights_path = _resolve_weights_path(detector_cfg.get("yolo_weights"))
    image = preprocess_result.get("image")
    if image is None:
        raise ValueError("YOLO proposal backend requires preprocess_result['image'].")

    model = _get_model(weights_path)
    results = model.predict(
        source=image,
        imgsz=int(detector_cfg.get("yolo_imgsz", 1024)),
        conf=float(detector_cfg.get("yolo_conf", 0.25)),
        iou=float(detector_cfg.get("yolo_iou", 0.45)),
        device=str(detector_cfg.get("yolo_device", "0")),
        verbose=False,
    )

    result = results[0]
    boxes = getattr(result, "boxes", None)
    names = result.names if hasattr(result, "names") else {}
    proposals: list[dict[str, Any]] = []

    if boxes is not None:
        for index, box in enumerate(boxes):
            cls_id = int(box.cls.item())
            score = float(box.conf.item())
            x1, y1, x2, y2 = [int(round(value)) for value in box.xyxy[0].tolist()]
            area = max(0, x2 - x1) * max(0, y2 - y1)
            proposals.append(
                {
                    "id": f"cp_{index + 1}",
                    "bbox": [x1, y1, x2, y2],
                    "area": area,
                    "class_id": cls_id,
                    "class_name": str(names.get(cls_id, cls_id)),
                    "score": score,
                    "source": "yolo",
                }
            )

    duplicate_iou = float(detector_cfg.get("yolo_duplicate_iou", 0.92))
    proposals, suppressed_proposals = _dedupe_overlapping_proposals(proposals, duplicate_iou)
    return {
        "proposals": proposals,
        "stats": {
            "proposal_count": len(proposals),
            "backend": "yolo",
            "weights_path": str(weights_path),
            "suppressed_duplicate_count": len(suppressed_proposals),
            "suppressed_duplicates": suppressed_proposals,
        },
    }
