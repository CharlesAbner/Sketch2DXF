"""
本文件的作用：
- 负责生成元件候选框，是后续分类、pin 定位和导线擦除的基础输入。
- 当前支持两种后端：传统规则法和 YOLO 检测法。

说明：
- `extract_component_proposals()` 是统一入口。
"""

from __future__ import annotations

import cv2
import numpy as np

from src.perception.yolo_component_detector import extract_component_proposals_yolo


def extract_component_proposals_traditional(preprocess_result: dict, config: dict) -> dict:
    """Generate component proposals with a lightweight corner-clustering heuristic."""
    clean_img = preprocess_result["clean"]
    perception_cfg = config["perception"]

    corners = cv2.goodFeaturesToTrack(
        clean_img,
        maxCorners=int(perception_cfg["proposal_corner_max_corners"]),
        qualityLevel=float(perception_cfg["proposal_corner_quality_level"]),
        minDistance=int(perception_cfg["proposal_corner_min_distance"]),
    )

    corner_mask = np.zeros_like(clean_img)
    if corners is not None:
        for corner in corners:
            x, y = corner.ravel()
            cv2.circle(corner_mask, (int(x), int(y)), 2, 255, -1)

    kernel_size = int(perception_cfg["proposal_dilate_kernel_size"])
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    dilated_corners = cv2.dilate(corner_mask, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated_corners, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    proposals = []
    min_area = int(perception_cfg["proposal_min_area"])
    padding = int(perception_cfg["proposal_bbox_padding"])

    for index, contour in enumerate(contours):
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < min_area:
            continue

        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(clean_img.shape[1], x + w + padding)
        y2 = min(clean_img.shape[0], y + h + padding)
        proposals.append(
            {
                "id": f"cp_{index + 1}",
                "bbox": [x1, y1, x2, y2],
                "area": area,
                "source": "traditional",
            }
        )

    return {
        "proposals": proposals,
        "stats": {
            "proposal_count": len(proposals),
            "backend": "traditional",
        },
    }


def extract_component_proposals(preprocess_result: dict, config: dict) -> dict:
    """Dispatch component proposal extraction based on the configured backend."""
    detector_cfg = config.get("detector", {})
    backend = detector_cfg.get("proposal_backend", "traditional")
    if backend == "yolo":
        return extract_component_proposals_yolo(preprocess_result, config)
    if backend == "traditional":
        return extract_component_proposals_traditional(preprocess_result, config)
    raise ValueError(f"Unsupported component proposal backend: {backend}")
