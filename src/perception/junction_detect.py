"""
本文件的作用：
- 从骨架图或线段集合中检测导线端点与交点。
- 这一步会直接影响 node 构建和拓扑恢复质量。

建议说明：
- 第一版可以先做简单规则，不必一开始就追求完美判点。
- 端点/交点结果一定要配合可视化检查。
"""

from __future__ import annotations

import cv2
import numpy as np


NEIGHBOR_OFFSETS = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
)


def _to_binary_mask(skeleton: np.ndarray) -> np.ndarray:
    """Convert a 0/255 skeleton image into a binary uint8 mask."""
    return (skeleton > 0).astype(np.uint8)


def _count_neighbors(mask: np.ndarray) -> np.ndarray:
    """Count 8-neighborhood foreground pixels for each pixel."""
    padded = np.pad(mask, 1, mode="constant", constant_values=0)
    counts = np.zeros_like(mask, dtype=np.uint8)
    for dy, dx in NEIGHBOR_OFFSETS:
        counts += padded[1 + dy : 1 + dy + mask.shape[0], 1 + dx : 1 + dx + mask.shape[1]]
    return counts


def _cluster_pixels(pixel_mask: np.ndarray, prefix: str, point_type: str) -> list[dict]:
    """Group nearby labeled pixels into a single point by connected components."""
    points: list[dict] = []
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(pixel_mask, connectivity=8)
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area <= 0:
            continue
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        cx, cy = centroids[label]
        local_mask = labels[top : top + height, left : left + width] == label
        ys, xs = np.where(local_mask)
        source_pixels = [(int(left + x), int(top + y)) for y, x in zip(ys, xs)]
        points.append(
            {
                "id": f"{prefix}{len(points) + 1}",
                "x": int(round(cx)),
                "y": int(round(cy)),
                "type": point_type,
                "pixel_count": area,
                "source_pixels": source_pixels,
                "bbox": [left, top, left + width, top + height],
            }
        )
    return points


def detect_endpoints(skeleton: np.ndarray) -> tuple[list[dict], list[tuple[int, int]]]:
    """Detect endpoints from a skeleton image using 8-neighborhood counts."""
    mask = _to_binary_mask(skeleton)
    neighbor_counts = _count_neighbors(mask)
    endpoint_mask = ((mask == 1) & (neighbor_counts == 1)).astype(np.uint8)
    raw_pixels = [(int(x), int(y)) for y, x in np.argwhere(endpoint_mask > 0)]
    return _cluster_pixels(endpoint_mask, "E", "endpoint"), raw_pixels


def detect_junctions(skeleton: np.ndarray) -> tuple[list[dict], list[tuple[int, int]]]:
    """Detect junctions from a skeleton image using 8-neighborhood counts."""
    mask = _to_binary_mask(skeleton)
    neighbor_counts = _count_neighbors(mask)
    junction_mask = ((mask == 1) & (neighbor_counts >= 3)).astype(np.uint8)
    raw_pixels = [(int(x), int(y)) for y, x in np.argwhere(junction_mask > 0)]
    return _cluster_pixels(junction_mask, "J", "junction"), raw_pixels


def detect_junctions_and_endpoints(preprocess_result: dict, wire_result: dict, config: dict) -> dict:
    """Run the endpoint/junction detection stage."""
    _ = wire_result, config
    skeleton = wire_result["wire_mask"]
    endpoints, raw_endpoint_pixels = detect_endpoints(skeleton)
    junctions, raw_junction_pixels = detect_junctions(skeleton)
    return {
        "endpoints": endpoints,
        "junctions": junctions,
        "raw_endpoint_pixels": raw_endpoint_pixels,
        "raw_junction_pixels": raw_junction_pixels,
        "stats": {
            "endpoint_count": len(endpoints),
            "junction_count": len(junctions),
        },
    }
