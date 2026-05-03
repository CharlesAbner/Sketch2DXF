"""
本文件的作用：
- 根据元件类别、bbox 和附近导线关系，生成 pin 的几何位置。
- 这是从“检测到元件”走向“恢复连接关系”的关键一步。

说明：
- 当前版本先保证 pin 朝向和落点相对稳定。
- 精细 pin 几何仍然可以在后续继续优化。
"""

from __future__ import annotations

from math import hypot

from src.topology.symbol_library import get_symbol_definition


def _segment_endpoints(segment: dict) -> list[tuple[int, int]]:
    return [
        (int(segment["x1"]), int(segment["y1"])),
        (int(segment["x2"]), int(segment["y2"])),
    ]


def _infer_component_axis(component: dict, wire_segments: list[dict], margin: int = 18) -> tuple[str, str, float]:
    """Infer whether the component pins are arranged horizontally or vertically."""
    bbox = component.get("bbox")
    if not bbox or len(bbox) != 4:
        return "horizontal", "fallback", 0.5
    x1, y1, x2, y2 = [int(v) for v in bbox]
    cx = int(round((x1 + x2) / 2))
    cy = int(round((y1 + y2) / 2))

    side_scores = {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
    for segment in wire_segments:
        for px, py in _segment_endpoints(segment):
            if not (x1 - margin <= px <= x2 + margin and y1 - margin <= py <= y2 + margin):
                continue
            distances = {
                "left": hypot(px - x1, py - cy),
                "right": hypot(px - x2, py - cy),
                "top": hypot(px - cx, py - y1),
                "bottom": hypot(px - cx, py - y2),
            }
            nearest_side = min(distances, key=distances.get)
            if distances[nearest_side] <= margin:
                side_scores[nearest_side] += max(0.0, float(margin - distances[nearest_side]))

    horizontal_score = side_scores["left"] + side_scores["right"]
    vertical_score = side_scores["top"] + side_scores["bottom"]
    if vertical_score > horizontal_score:
        total = vertical_score + horizontal_score
        confidence = 0.5 if total <= 0.0 else vertical_score / total
        return "vertical", "wire_evidence", round(float(confidence), 3)
    total = vertical_score + horizontal_score
    confidence = 0.5 if total <= 0.0 else horizontal_score / total
    source = "wire_evidence" if total > 0.0 else "fallback"
    return "horizontal", source, round(float(confidence), 3)


def _pins_from_bbox(
    component: dict,
    pin_count: int,
    pin_layout: str,
    axis: str,
    axis_source: str,
    confidence: float,
) -> list[dict]:
    """Create coarse pin coordinates from a component bounding box."""
    bbox = component.get("bbox")
    component_id = component.get("id", "unknown_component")
    if not bbox or len(bbox) != 4:
        return []
    x1, y1, x2, y2 = bbox
    cx = int(round((x1 + x2) / 2))
    cy = int(round((y1 + y2) / 2))

    if pin_count == 1 or pin_layout == "top_single":
        return [
            {
                "pin_id": f"{component_id}_p1",
                "component_id": component_id,
                "x": cx,
                "y": int(y1),
                "side": "top",
                "terminal_role": "top",
                "axis": axis,
                "axis_source": axis_source,
                "confidence": confidence,
            }
        ]

    if axis == "vertical":
        return [
            {
                "pin_id": f"{component_id}_p1",
                "component_id": component_id,
                "x": cx,
                "y": int(y1),
                "side": "top",
                "terminal_role": "top",
                "axis": axis,
                "axis_source": axis_source,
                "confidence": confidence,
            },
            {
                "pin_id": f"{component_id}_p2",
                "component_id": component_id,
                "x": cx,
                "y": int(y2),
                "side": "bottom",
                "terminal_role": "bottom",
                "axis": axis,
                "axis_source": axis_source,
                "confidence": confidence,
            },
        ]

    return [
        {
            "pin_id": f"{component_id}_p1",
            "component_id": component_id,
            "x": int(x1),
            "y": cy,
            "side": "left",
            "terminal_role": "left",
            "axis": axis,
            "axis_source": axis_source,
            "confidence": confidence,
        },
        {
            "pin_id": f"{component_id}_p2",
            "component_id": component_id,
            "x": int(x2),
            "y": cy,
            "side": "right",
            "terminal_role": "right",
            "axis": axis,
            "axis_source": axis_source,
            "confidence": confidence,
        },
    ]


def locate_component_pins(perception_result: dict, config: dict) -> dict:
    """Instantiate pin locations for each detected component."""
    pins = []
    wire_segments = perception_result.get("wire_segments", [])
    margin = int(config["topology"].get("pin_axis_probe_margin", 18))

    for component in perception_result["components"]:
        symbol = get_symbol_definition(component.get("class_name", "unknown"))
        pin_count = symbol["pin_count"]
        pin_layout = symbol["pin_layout"]

        # Fallback: if the classifier is still weak, treat unknown proposals as generic two-terminal parts.
        if pin_count == 0 and component.get("bbox"):
            pin_count = 2
            pin_layout = "horizontal_pair"

        axis, axis_source, confidence = _infer_component_axis(component, wire_segments, margin=margin)
        pins.append(
            {
                "component_id": component.get("id", "unknown_component"),
                "pin_count": pin_count,
                "axis": axis,
                "axis_source": axis_source,
                "confidence": confidence,
                "pins": _pins_from_bbox(component, pin_count, pin_layout, axis, axis_source, confidence),
            }
        )
    return {"pins": pins}
