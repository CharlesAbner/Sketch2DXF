"""Lightweight DXF layout cleanup for recovered topology.

This module does not re-synthesize a schematic from the netlist. It preserves
the recovered image-space layout, then makes the exported geometry easier to
read by snapping wire endpoints, snapping to a small grid, filtering unsupported
wire evidence, and merging collinear wire fragments.
"""

from __future__ import annotations

from copy import deepcopy
from math import hypot
from typing import Iterable


Point = tuple[float, float]


def _export_cfg(config: dict) -> dict:
    return config.get("export", {})


def _is_clean_mode(config: dict) -> bool:
    return str(_export_cfg(config).get("dxf_mode", "clean")).lower() == "clean"


def _grid_size(config: dict) -> float:
    return max(0.0, float(_export_cfg(config).get("dxf_grid_size", 5.0)))


def _snap_radius(config: dict) -> float:
    return max(0.0, float(_export_cfg(config).get("dxf_snap_radius", 18.0)))


def _merge_gap(config: dict) -> float:
    return max(0.0, float(_export_cfg(config).get("dxf_merge_gap", 8.0)))


def _min_wire_length(config: dict) -> float:
    return max(0.0, float(_export_cfg(config).get("dxf_min_wire_length", 4.0)))


def _snap_value(value: float, grid: float) -> float:
    if grid <= 0.0:
        return float(value)
    return round(float(value) / grid) * grid


def _snap_point_to_grid(point: Point, grid: float) -> Point:
    return _snap_value(point[0], grid), _snap_value(point[1], grid)


def _distance(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _nearest_anchor(point: Point, anchors: Iterable[Point], radius: float) -> Point | None:
    best_point = None
    best_distance = radius + 1.0
    for anchor in anchors:
        distance = _distance(point, anchor)
        if distance <= radius and distance < best_distance:
            best_point = anchor
            best_distance = distance
    return best_point


def _active_wire_ids(topology: dict) -> set[str]:
    active_ids: set[str] = set()
    for node in topology.get("nodes", []):
        active_ids.update(str(item) for item in node.get("segment_ids", []))
        active_ids.update(str(item) for item in node.get("source_segment_ids", []))
        for member in node.get("members", []):
            segment_id = member.get("segment_id")
            if segment_id:
                active_ids.add(str(segment_id))
    return active_ids


def _filter_active_wires(topology: dict, config: dict) -> list[dict]:
    wires = list(topology.get("wires", []))
    if not _export_cfg(config).get("dxf_active_wires_only", True):
        return wires

    active_ids = _active_wire_ids(topology)
    if not active_ids:
        return wires

    kept = [wire for wire in wires if str(wire.get("segment_id", wire.get("id", ""))) in active_ids]
    min_ratio = float(_export_cfg(config).get("dxf_active_wire_min_keep_ratio", 0.25))
    if wires and len(kept) < max(1, int(len(wires) * min_ratio)):
        return wires
    return kept


def _snap_bbox(bbox: list, grid: float) -> list:
    if len(bbox) != 4:
        return bbox
    return [_snap_value(float(value), grid) for value in bbox]


def _snap_pin_and_node_coordinates(topology: dict, grid: float) -> None:
    for group in topology.get("pins", []):
        for pin in group.get("pins", []):
            pin["x"] = _snap_value(float(pin.get("x", 0.0)), grid)
            pin["y"] = _snap_value(float(pin.get("y", 0.0)), grid)

    for node in topology.get("nodes", []):
        node["x"] = _snap_value(float(node.get("x", 0.0)), grid)
        node["y"] = _snap_value(float(node.get("y", 0.0)), grid)
        if isinstance(node.get("bbox"), list):
            node["bbox"] = _snap_bbox(node["bbox"], grid)
        if isinstance(node.get("points"), list):
            node["points"] = [
                list(_snap_point_to_grid((float(point[0]), float(point[1])), grid))
                for point in node["points"]
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]

    for component in topology.get("components", []):
        if isinstance(component.get("bbox"), list):
            component["bbox"] = _snap_bbox(component["bbox"], grid)


def _anchor_points(topology: dict) -> list[Point]:
    anchors: list[Point] = []
    for group in topology.get("pins", []):
        for pin in group.get("pins", []):
            anchors.append((float(pin.get("x", 0.0)), float(pin.get("y", 0.0))))
    for node in topology.get("nodes", []):
        anchors.append((float(node.get("x", 0.0)), float(node.get("y", 0.0))))
        for point in node.get("points", []):
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                anchors.append((float(point[0]), float(point[1])))
    return anchors


def _wire_orientation(wire: dict, p1: Point, p2: Point) -> str:
    orientation = str(wire.get("orientation", "")).lower()
    if orientation in {"h", "horizontal"}:
        return "h"
    if orientation in {"v", "vertical"}:
        return "v"
    return "h" if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]) else "v"


def _regularize_wire(wire: dict, anchors: list[Point], config: dict) -> dict | None:
    grid = _grid_size(config)
    radius = _snap_radius(config)
    p1 = (float(wire.get("x1", 0.0)), float(wire.get("y1", 0.0)))
    p2 = (float(wire.get("x2", 0.0)), float(wire.get("y2", 0.0)))

    p1 = _nearest_anchor(p1, anchors, radius) or _snap_point_to_grid(p1, grid)
    p2 = _nearest_anchor(p2, anchors, radius) or _snap_point_to_grid(p2, grid)

    orientation = _wire_orientation(wire, p1, p2)
    if orientation == "h":
        y = _snap_value((p1[1] + p2[1]) / 2.0, grid)
        x1, x2 = sorted([p1[0], p2[0]])
        p1, p2 = (_snap_value(x1, grid), y), (_snap_value(x2, grid), y)
    else:
        x = _snap_value((p1[0] + p2[0]) / 2.0, grid)
        y1, y2 = sorted([p1[1], p2[1]])
        p1, p2 = (x, _snap_value(y1, grid)), (x, _snap_value(y2, grid))

    if _distance(p1, p2) < _min_wire_length(config):
        return None

    return {
        **wire,
        "x1": p1[0],
        "y1": p1[1],
        "x2": p2[0],
        "y2": p2[1],
        "orientation": orientation,
        "export_regularized": True,
    }


def _merge_grouped_wires(wires: list[dict], config: dict) -> list[dict]:
    if not _export_cfg(config).get("dxf_merge_collinear_wires", True):
        return wires

    merge_gap = _merge_gap(config)
    grouped: dict[tuple[str, float], list[dict]] = {}
    for wire in wires:
        orientation = str(wire.get("orientation", "")).lower()
        if orientation == "h":
            key = ("h", round(float(wire["y1"]), 3))
        elif orientation == "v":
            key = ("v", round(float(wire["x1"]), 3))
        else:
            key = (_wire_orientation(wire, (wire["x1"], wire["y1"]), (wire["x2"], wire["y2"])), 0.0)
        grouped.setdefault(key, []).append(wire)

    merged: list[dict] = []
    counter = 1
    for (orientation, axis), group in grouped.items():
        intervals = []
        for wire in group:
            if orientation == "h":
                start, end = sorted([float(wire["x1"]), float(wire["x2"])])
            else:
                start, end = sorted([float(wire["y1"]), float(wire["y2"])])
            intervals.append((start, end, wire))
        intervals.sort(key=lambda item: item[0])

        current_start = current_end = None
        current_sources: list[str] = []
        current_template: dict | None = None
        for start, end, wire in intervals:
            if current_start is None:
                current_start, current_end = start, end
                current_sources = [str(wire.get("segment_id", wire.get("id", f"wire_{counter}")))]
                current_template = wire
                continue
            if start <= float(current_end) + merge_gap:
                current_end = max(float(current_end), end)
                current_sources.append(str(wire.get("segment_id", wire.get("id", f"wire_{counter}"))))
                continue

            merged.append(
                _merged_wire(counter, orientation, axis, float(current_start), float(current_end), current_template, current_sources)
            )
            counter += 1
            current_start, current_end = start, end
            current_sources = [str(wire.get("segment_id", wire.get("id", f"wire_{counter}")))]
            current_template = wire

        if current_start is not None and current_template is not None:
            merged.append(
                _merged_wire(counter, orientation, axis, float(current_start), float(current_end), current_template, current_sources)
            )
            counter += 1

    return merged


def _merged_wire(
    counter: int,
    orientation: str,
    axis: float,
    start: float,
    end: float,
    template: dict | None,
    sources: list[str],
) -> dict:
    base = dict(template or {})
    if orientation == "h":
        base.update({"x1": start, "y1": axis, "x2": end, "y2": axis})
    else:
        base.update({"x1": axis, "y1": start, "x2": axis, "y2": end})
    base.update(
        {
            "segment_id": f"dxw{counter}",
            "orientation": orientation,
            "source_segment_ids": sources,
            "export_merged": len(sources) > 1,
            "export_regularized": True,
        }
    )
    return base


def _regularize_wires(topology: dict, config: dict) -> None:
    topology["wires"] = _filter_active_wires(topology, config)
    anchors = _anchor_points(topology)
    regularized = []
    for wire in topology.get("wires", []):
        normalized = _regularize_wire(wire, anchors, config)
        if normalized is not None:
            regularized.append(normalized)
    topology["wires"] = _merge_grouped_wires(regularized, config)


def normalize_layout(topology: dict, config: dict) -> dict:
    """Return a DXF-oriented topology copy with optional clean geometry cleanup."""
    normalized = deepcopy(topology)
    export_cfg = _export_cfg(config)
    if not _is_clean_mode(config) or not export_cfg.get("enable_layout_normalization", True):
        normalized["export_layout"] = {
            "mode": export_cfg.get("dxf_mode", "debug"),
            "normalized": False,
        }
        return normalized

    grid = _grid_size(config)
    original_wire_count = len(normalized.get("wires", []))
    _snap_pin_and_node_coordinates(normalized, grid)
    _regularize_wires(normalized, config)
    normalized["export_layout"] = {
        "mode": "clean",
        "normalized": True,
        "grid_size": grid,
        "snap_radius": _snap_radius(config),
        "original_wire_count": original_wire_count,
        "export_wire_count": len(normalized.get("wires", [])),
        "active_wires_only": bool(export_cfg.get("dxf_active_wires_only", True)),
    }
    return normalized
