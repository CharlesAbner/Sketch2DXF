"""
本文件的作用：
- 把关键中间结果叠加回原图，生成便于调试和展示的 overlay 图。
- 这是答辩最值钱的输出之一，因为它能直观展示系统理解过程。

建议说明：
- 第一版先把 bbox、线段、节点、pin 画出来即可。
- 只要可视化清晰，哪怕算法还没完全成熟，也能展示思路。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from src.config import OUTPUTS_DIR
from src.io_utils.image_io import save_image


def _ensure_bgr(image: np.ndarray) -> np.ndarray:
    """Convert grayscale images to BGR for colored overlay drawing."""
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image.copy()


def _draw_segments(canvas: np.ndarray, segments: list[dict]) -> None:
    """Draw wire segments on the overlay."""
    for segment in segments:
        color = (0, 255, 255) if segment.get("orientation") == "h" else (255, 255, 0)
        pt1 = (int(segment["x1"]), int(segment["y1"]))
        pt2 = (int(segment["x2"]), int(segment["y2"]))
        cv2.line(canvas, pt1, pt2, color, 2, lineType=cv2.LINE_AA)


def _draw_proposals(canvas: np.ndarray, proposals: list[dict]) -> None:
    """Draw component proposal bounding boxes."""
    for proposal in proposals:
        bbox = proposal.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = [int(v) for v in bbox]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 165, 255), 2)
        cv2.putText(
            canvas,
            proposal.get("id", "cp"),
            (x1, max(12, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 165, 255),
            1,
            lineType=cv2.LINE_AA,
        )


def _draw_points(canvas: np.ndarray, points: list[dict], color: tuple[int, int, int], radius: int, prefix: str) -> None:
    """Draw endpoint/junction/node style points."""
    for point in points:
        x = int(point.get("x", 0))
        y = int(point.get("y", 0))
        cv2.circle(canvas, (x, y), radius, color, -1, lineType=cv2.LINE_AA)
        label = point.get("id", prefix)
        cv2.putText(
            canvas,
            label,
            (x + 4, y - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            color,
            1,
            lineType=cv2.LINE_AA,
        )


def _draw_pin_groups(canvas: np.ndarray, pin_groups: list[dict]) -> None:
    """Draw pin locations for each component."""
    for group in pin_groups:
        for pin in group.get("pins", []):
            x = int(pin.get("x", 0))
            y = int(pin.get("y", 0))
            cv2.circle(canvas, (x, y), 4, (255, 0, 255), -1, lineType=cv2.LINE_AA)
            cv2.putText(
                canvas,
                pin.get("pin_id", "pin"),
                (x + 4, y + 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (255, 0, 255),
                1,
                lineType=cv2.LINE_AA,
            )


def _draw_legend(canvas: np.ndarray) -> None:
    """Draw a compact legend in the top-left corner."""
    legend_items = [
        ("h-wire", (0, 255, 255)),
        ("v-wire", (255, 255, 0)),
        ("proposal", (0, 165, 255)),
        ("endpoint", (0, 255, 0)),
        ("junction", (0, 0, 255)),
        ("node", (255, 0, 0)),
        ("pin", (255, 0, 255)),
    ]
    x0, y0 = 10, 20
    box_w = 145
    box_h = 20 + 18 * len(legend_items)
    cv2.rectangle(canvas, (x0 - 6, y0 - 16), (x0 + box_w, y0 - 16 + box_h), (30, 30, 30), -1)
    cv2.rectangle(canvas, (x0 - 6, y0 - 16), (x0 + box_w, y0 - 16 + box_h), (220, 220, 220), 1)
    for idx, (name, color) in enumerate(legend_items):
        y = y0 + idx * 18
        cv2.circle(canvas, (x0, y), 4, color, -1, lineType=cv2.LINE_AA)
        cv2.putText(
            canvas,
            name,
            (x0 + 12, y + 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (240, 240, 240),
            1,
            lineType=cv2.LINE_AA,
        )


def render_overlay(
    image,
    preprocess_result: dict,
    perception_result: dict,
    topology_result: dict,
    config: dict,
) -> dict:
    """Render a debug overlay with wires, proposals, endpoints, junctions, nodes, and pins."""
    _ = preprocess_result, config
    canvas = _ensure_bgr(image)

    _draw_segments(canvas, perception_result.get("wire_segments", []))
    _draw_proposals(canvas, perception_result.get("proposals", []))
    _draw_points(canvas, perception_result.get("endpoints", []), (0, 255, 0), 3, "E")
    _draw_points(canvas, perception_result.get("junctions", []), (0, 0, 255), 4, "J")
    _draw_points(canvas, topology_result.get("nodes", []), (255, 0, 0), 5, "N")
    _draw_pin_groups(canvas, topology_result.get("pins", []))
    _draw_legend(canvas)

    overlay_path = OUTPUTS_DIR / "overlay" / "overlay.png"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    save_image(overlay_path, canvas)
    return {"overlay_path": str(Path(overlay_path))}
