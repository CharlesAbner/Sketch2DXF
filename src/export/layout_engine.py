"""DXF layout normalization and schematic redraw for recovered topology.

Clean export mode first tries topology-aware schematic redraw templates
(ladder, two-rail ladder, and rail/branch layouts). If no template matches,
it falls back to geometry-preserving cleanup: snapping wire endpoints,
filtering unsupported wire evidence, and merging collinear fragments.
"""

from __future__ import annotations

from copy import deepcopy
from math import hypot
from typing import Iterable


Point = tuple[float, float]
RAIL_TOP_Y = 80.0
RAIL_BOTTOM_Y = 340.0
RAIL_LEFT_X = 80.0
BRANCH_SPACING = 135.0
BRANCH_MARGIN = 70.0
RAIL_END_MARGIN = 0.0


def _export_cfg(config: dict) -> dict:
    return config.get("export", {})


def _is_clean_mode(config: dict) -> bool:
    return str(_export_cfg(config).get("dxf_mode", "clean")).lower() == "clean"


def _grid_size(config: dict) -> float:
    return max(0.0, float(_export_cfg(config).get("dxf_grid_size", 5.0)))


def _snap_radius(config: dict) -> float:
    return max(0.0, float(_export_cfg(config).get("dxf_snap_radius", 18.0)))


def _merge_gap(config: dict) -> float:
    return max(0.0, float(_export_cfg(config).get("dxf_merge_gap", 12.0)))


def _min_wire_length(config: dict) -> float:
    return max(0.0, float(_export_cfg(config).get("dxf_min_wire_length", 8.0)))


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


def _connections_by_component(topology: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for connection in topology.get("connections", []):
        component_id = str(connection.get("component_id", ""))
        if component_id:
            grouped.setdefault(component_id, []).append(connection)
    return grouped


def _pin_group_by_component(topology: dict) -> dict[str, dict]:
    return {
        str(group.get("component_id")): group
        for group in topology.get("pins", [])
        if group.get("component_id")
    }


def _net_pin_count(topology: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for net in topology.get("nets", []):
        net_id = str(net.get("node_id") or net.get("net_id") or "")
        if net_id:
            counts[net_id] = int(net.get("pin_count", 0) or 0)
    for connection in topology.get("connections", []):
        node_id = str(connection.get("node_id", ""))
        if node_id and node_id not in counts:
            counts[node_id] = counts.get(node_id, 0) + 1
    return counts


def _node_y_lookup(topology: dict) -> dict[str, float]:
    return {
        str(node.get("node_id")): float(node.get("y", 0.0) or 0.0)
        for node in topology.get("nodes", [])
        if node.get("node_id")
    }


def _choose_rail_pair(topology: dict) -> tuple[str, str] | None:
    counts = _net_pin_count(topology)
    if len(counts) < 2:
        return None
    node_y = _node_y_lookup(topology)
    ranked = sorted(counts, key=lambda node_id: (-counts[node_id], node_y.get(node_id, 0.0)))
    first, second = ranked[0], ranked[1]
    if node_y.get(first, 0.0) <= node_y.get(second, 0.0):
        return first, second
    return second, first


def _component_center_x(component: dict) -> float:
    bbox = component.get("bbox", [0, 0, 0, 0])
    return (float(bbox[0]) + float(bbox[2])) / 2.0 if len(bbox) >= 4 else 0.0


def _component_edges(topology: dict) -> list[dict]:
    components = {str(component.get("id")): component for component in topology.get("components", [])}
    result = []
    for component_id, connections in _connections_by_component(topology).items():
        if len(connections) != 2 or component_id not in components:
            continue
        left, right = connections[0], connections[1]
        result.append(
            {
                "component_id": component_id,
                "component": components[component_id],
                "nets": [str(left.get("node_id")), str(right.get("node_id"))],
                "pin_by_net": {
                    str(left.get("node_id")): str(left.get("pin_id")),
                    str(right.get("node_id")): str(right.get("pin_id")),
                },
                "x_hint": _component_center_x(components[component_id]),
            }
        )
    return result


def _build_net_graph(edges: list[dict]) -> dict[str, list[tuple[str, str]]]:
    graph: dict[str, list[tuple[str, str]]] = {}
    for edge in edges:
        a, b = edge["nets"]
        component_id = edge["component_id"]
        graph.setdefault(a, []).append((b, component_id))
        graph.setdefault(b, []).append((a, component_id))
    return graph


def _find_rail_paths(topology: dict, top_net: str, bottom_net: str) -> list[list[str]]:
    edges = _component_edges(topology)
    edge_by_component = {edge["component_id"]: edge for edge in edges}
    graph = _build_net_graph(edges)
    paths: list[list[str]] = []

    def visit(net_id: str, used_components: set[str], path_components: list[str]) -> None:
        if len(path_components) > 5:
            return
        if net_id == bottom_net and path_components:
            paths.append(list(path_components))
            return
        for next_net, component_id in graph.get(net_id, []):
            if component_id in used_components:
                continue
            edge = edge_by_component[component_id]
            if net_id not in edge["nets"]:
                continue
            next_used = set(used_components)
            next_used.add(component_id)
            visit(next_net, next_used, path_components + [component_id])

    visit(top_net, set(), [])
    deduped = []
    seen = set()
    for path in paths:
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _path_x_hint(path: list[str], edge_lookup: dict[str, dict]) -> float:
    hints = [float(edge_lookup[item].get("x_hint", 0.0)) for item in path if item in edge_lookup]
    return sum(hints) / len(hints) if hints else 0.0


def _pin_ref_for_group(group: dict, pin_id: str) -> dict | None:
    for pin in group.get("pins", []):
        if str(pin.get("pin_id")) == pin_id:
            return pin
    return None


def _component_refdes(component: dict) -> str:
    return str(component.get("refdes") or component.get("id") or "")


def _is_power_component(component: dict) -> bool:
    class_name = str(component.get("class_name", "")).lower()
    return (
        class_name in {"power_source", "voltage_source", "voltage.ac", "voltage.dc", "voltage.battery", "battery", "source"}
        or "voltage" in class_name
        or "battery" in class_name
    )


def _node_x_lookup(topology: dict) -> dict[str, float]:
    return {
        str(node.get("node_id")): float(node.get("x", 0.0) or 0.0)
        for node in topology.get("nodes", [])
        if node.get("node_id")
    }


def _find_ladder_chain(series_edges: list[dict], topology: dict) -> list[str] | None:
    if not series_edges:
        return None
    graph: dict[str, list[tuple[str, str]]] = {}
    for edge in series_edges:
        a, b = edge["nets"]
        graph.setdefault(a, []).append((b, edge["component_id"]))
        graph.setdefault(b, []).append((a, edge["component_id"]))
    if any(len(items) > 2 for items in graph.values()):
        return None
    endpoints = [node_id for node_id, items in graph.items() if len(items) == 1]
    if len(endpoints) != 2:
        return None

    node_x = _node_x_lookup(topology)
    start = min(endpoints, key=lambda node_id: node_x.get(node_id, 0.0))
    chain = [start]
    used_components: set[str] = set()
    current = start
    previous = None
    while True:
        next_items = [
            (next_node, component_id)
            for next_node, component_id in graph.get(current, [])
            if component_id not in used_components and next_node != previous
        ]
        if not next_items:
            break
        next_node, component_id = next_items[0]
        used_components.add(component_id)
        chain.append(next_node)
        previous, current = current, next_node
    if len(used_components) != len(series_edges):
        return None
    return chain


def _topology_ladder_redraw_layout(topology: dict, config: dict) -> dict | None:
    edges = _component_edges(topology)
    if len(edges) < 3:
        return None
    counts = _net_pin_count(topology)
    if not counts:
        return None
    bottom_net = max(counts, key=lambda node_id: counts[node_id])
    series_edges = [edge for edge in edges if bottom_net not in edge["nets"]]
    shunt_edges = [edge for edge in edges if bottom_net in edge["nets"]]
    if len(series_edges) < 2 or len(shunt_edges) < 2:
        return None

    chain = _find_ladder_chain(series_edges, topology)
    if not chain or len(chain) < 3:
        return None
    series_by_pair = {
        frozenset(edge["nets"]): edge
        for edge in series_edges
    }
    if any(frozenset([chain[index], chain[index + 1]]) not in series_by_pair for index in range(len(chain) - 1)):
        return None

    shunts_by_net: dict[str, list[dict]] = {node_id: [] for node_id in chain}
    for edge in shunt_edges:
        top_net = edge["nets"][0] if edge["nets"][1] == bottom_net else edge["nets"][1]
        if top_net in shunts_by_net:
            shunts_by_net[top_net].append(edge)
    if sum(len(items) for items in shunts_by_net.values()) < 2:
        return None

    redrawn = deepcopy(topology)
    components = {str(component.get("id")): component for component in redrawn.get("components", [])}
    pin_groups = _pin_group_by_component(redrawn)
    edge_lookup = {edge["component_id"]: edge for edge in edges}
    node_x = {
        node_id: RAIL_LEFT_X + BRANCH_MARGIN + index * BRANCH_SPACING
        for index, node_id in enumerate(chain)
    }
    shunt_gap = float(_export_cfg(config).get("dxf_ladder_parallel_shunt_gap", 70.0))
    shunt_positions: dict[str, dict[str, float]] = {}
    all_x = list(node_x.values())
    for node_id in chain:
        shunts = sorted(shunts_by_net.get(node_id, []), key=lambda edge: edge.get("x_hint", 0.0))
        if not shunts:
            continue
        offsets = [0.0] if len(shunts) == 1 else [index * shunt_gap for index in range(len(shunts))]
        shunt_positions[node_id] = {}
        for edge, offset in zip(shunts, offsets):
            x = node_x[node_id] + offset
            shunt_positions[node_id][edge["component_id"]] = x
            all_x.append(x)

    rail_start = min(all_x)
    rail_end = max(all_x)
    wires = [
        {"segment_id": "ladder_bottom_rail", "orientation": "h", "x1": rail_start, "y1": RAIL_BOTTOM_Y, "x2": rail_end, "y2": RAIL_BOTTOM_Y},
    ]
    node_points: dict[str, list[Point]] = {node_id: [(x, RAIL_TOP_Y)] for node_id, x in node_x.items()}
    node_points[bottom_net] = [(rail_start, RAIL_BOTTOM_Y), (rail_end, RAIL_BOTTOM_Y)]

    counter = 1
    for node_id, positions in shunt_positions.items():
        xs = list(positions.values())
        if xs:
            left, right = min(node_x[node_id], *xs), max(node_x[node_id], *xs)
            if right > left:
                wires.append(
                    {
                        "segment_id": f"ladder_top_tie_{counter}",
                        "orientation": "h",
                        "x1": left,
                        "y1": RAIL_TOP_Y,
                        "x2": right,
                        "y2": RAIL_TOP_Y,
                    }
                )
                counter += 1

    def place_pin(group: dict, pin_id: str | None, x: float, y: float, axis: str, side: str) -> None:
        if not pin_id:
            return
        pin = _pin_ref_for_group(group, pin_id)
        if pin is not None:
            pin["x"], pin["y"], pin["axis"], pin["side"] = x, y, axis, side

    for index in range(len(chain) - 1):
        left_net, right_net = chain[index], chain[index + 1]
        edge = series_by_pair[frozenset([left_net, right_net])]
        component = components.get(edge["component_id"])
        group = pin_groups.get(edge["component_id"])
        if not component or not group:
            continue
        x1, x2 = node_x[left_net], node_x[right_net]
        place_pin(group, edge["pin_by_net"].get(left_net), x1, RAIL_TOP_Y, "horizontal", "left")
        place_pin(group, edge["pin_by_net"].get(right_net), x2, RAIL_TOP_Y, "horizontal", "right")
        group["axis"] = "horizontal"
        group["axis_source"] = "schematic_ladder_redraw"
        component["bbox"] = [min(x1, x2), RAIL_TOP_Y - 22.0, max(x1, x2), RAIL_TOP_Y + 22.0]
        node_points.setdefault(left_net, []).append((x1, RAIL_TOP_Y))
        node_points.setdefault(right_net, []).append((x2, RAIL_TOP_Y))

    for node_id, positions in shunt_positions.items():
        for component_id, x in positions.items():
            edge = edge_lookup[component_id]
            component = components.get(component_id)
            group = pin_groups.get(component_id)
            if not component or not group:
                continue
            place_pin(group, edge["pin_by_net"].get(node_id), x, RAIL_TOP_Y, "vertical", "top")
            place_pin(group, edge["pin_by_net"].get(bottom_net), x, RAIL_BOTTOM_Y, "vertical", "bottom")
            group["axis"] = "vertical"
            group["axis_source"] = "schematic_ladder_redraw"
            component["bbox"] = [x - 22.0, RAIL_TOP_Y, x + 22.0, RAIL_BOTTOM_Y]
            node_points.setdefault(node_id, []).append((x, RAIL_TOP_Y))
            node_points.setdefault(bottom_net, []).append((x, RAIL_BOTTOM_Y))

    for node in redrawn.get("nodes", []):
        node_id = str(node.get("node_id"))
        points = node_points.get(node_id, [])
        if not points:
            continue
        node["x"] = sum(point[0] for point in points) / len(points)
        node["y"] = sum(point[1] for point in points) / len(points)
        node["points"] = [[point[0], point[1]] for point in points]
        node["bbox"] = [
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        ]

    redrawn["wires"] = wires
    redrawn["export_layout"] = {
        "mode": "schematic_ladder_redraw",
        "normalized": True,
        "topology_redraw": True,
        "bottom_net": bottom_net,
        "chain_nets": chain,
        "rail_start_x": rail_start,
        "rail_end_x": rail_end,
        "series_components": [
            series_by_pair[frozenset([chain[index], chain[index + 1]])]["component_id"]
            for index in range(len(chain) - 1)
        ],
        "shunt_components": {
            node_id: list(positions.keys())
            for node_id, positions in shunt_positions.items()
        },
    }
    return redrawn


def _topology_two_rail_ladder_redraw_layout(topology: dict, config: dict) -> dict | None:
    edges = _component_edges(topology)
    if len(edges) < 5:
        return None
    node_by_id = {
        str(node.get("node_id")): node
        for node in topology.get("nodes", [])
        if node.get("node_id")
    }
    source_edges = [edge for edge in edges if _is_power_component(edge["component"])]
    if len(source_edges) != 1:
        return None
    source_edge = source_edges[0]
    source_a, source_b = source_edge["nets"]
    if source_a not in node_by_id or source_b not in node_by_id:
        return None
    if float(node_by_id[source_a].get("y", 0.0) or 0.0) <= float(node_by_id[source_b].get("y", 0.0) or 0.0):
        top_start, bottom_start = source_a, source_b
    else:
        top_start, bottom_start = source_b, source_a

    source_mid_y = (
        float(node_by_id[top_start].get("y", 0.0) or 0.0)
        + float(node_by_id[bottom_start].get("y", 0.0) or 0.0)
    ) / 2.0
    rail_edges = [edge for edge in edges if edge["component_id"] != source_edge["component_id"]]
    top_edges = [
        edge
        for edge in rail_edges
        if all(float(node_by_id.get(net_id, {}).get("y", 0.0) or 0.0) <= source_mid_y for net_id in edge["nets"])
    ]
    bottom_edges = [
        edge
        for edge in rail_edges
        if all(float(node_by_id.get(net_id, {}).get("y", 0.0) or 0.0) > source_mid_y for net_id in edge["nets"])
    ]
    rung_edges = [
        edge
        for edge in rail_edges
        if edge not in top_edges and edge not in bottom_edges
    ]
    if len(top_edges) < 1 or len(bottom_edges) < 1 or len(rung_edges) < 1:
        return None

    def ordered_chain(start: str, chain_edges: list[dict]) -> list[str] | None:
        graph: dict[str, list[str]] = {}
        for edge in chain_edges:
            a, b = edge["nets"]
            graph.setdefault(a, []).append(b)
            graph.setdefault(b, []).append(a)
        if start not in graph:
            return None
        ordered = [start]
        previous = None
        current = start
        while True:
            next_nodes = [node_id for node_id in graph.get(current, []) if node_id != previous and node_id not in ordered]
            if not next_nodes:
                break
            next_node = sorted(next_nodes, key=lambda node_id: float(node_by_id.get(node_id, {}).get("x", 0.0) or 0.0))[0]
            ordered.append(next_node)
            previous, current = current, next_node
        if len(ordered) < 2:
            return None
        return ordered

    top_chain = ordered_chain(top_start, top_edges)
    bottom_chain = ordered_chain(bottom_start, bottom_edges)
    if not top_chain or not bottom_chain:
        return None
    column_count = max(len(top_chain), len(bottom_chain))
    if column_count < 2:
        return None

    def column_node(chain: list[str], index: int) -> str:
        return chain[min(index, len(chain) - 1)]

    columns = [
        (column_node(top_chain, index), column_node(bottom_chain, index))
        for index in range(column_count)
    ]
    if len(set(columns)) != len(columns):
        return None

    edge_by_pair: dict[frozenset[str], list[dict]] = {}
    for edge in rail_edges:
        edge_by_pair.setdefault(frozenset(edge["nets"]), []).append(edge)

    redrawn = deepcopy(topology)
    components = {str(component.get("id")): component for component in redrawn.get("components", [])}
    pin_groups = _pin_group_by_component(redrawn)
    x_by_col = {index: RAIL_LEFT_X + BRANCH_MARGIN + index * BRANCH_SPACING for index in range(column_count)}
    node_points: dict[str, list[Point]] = {}
    for index, (top_net, bottom_net) in enumerate(columns):
        node_points.setdefault(top_net, []).append((x_by_col[index], RAIL_TOP_Y))
        node_points.setdefault(bottom_net, []).append((x_by_col[index], RAIL_BOTTOM_Y))

    # Do not draw full rail lines through series component bodies. Series
    # components already connect adjacent column nodes via their own leads.
    wires = []

    def place_pin(group: dict, pin_id: str | None, x: float, y: float, axis: str, side: str) -> None:
        if not pin_id:
            return
        pin = _pin_ref_for_group(group, pin_id)
        if pin is not None:
            pin["x"], pin["y"], pin["axis"], pin["side"] = x, y, axis, side

    def place_edge(edge: dict, net_a: str, net_b: str, p1: Point, p2: Point, axis: str) -> None:
        component = components.get(edge["component_id"])
        group = pin_groups.get(edge["component_id"])
        if not component or not group:
            return
        place_pin(group, edge["pin_by_net"].get(net_a), p1[0], p1[1], axis, "left" if axis == "horizontal" else "top")
        place_pin(group, edge["pin_by_net"].get(net_b), p2[0], p2[1], axis, "right" if axis == "horizontal" else "bottom")
        group["axis"] = axis
        group["axis_source"] = "schematic_two_rail_ladder_redraw"
        component["bbox"] = [
            min(p1[0], p2[0]) - 18.0,
            min(p1[1], p2[1]) - 18.0,
            max(p1[0], p2[0]) + 18.0,
            max(p1[1], p2[1]) + 18.0,
        ]

    place_edge(source_edge, top_start, bottom_start, (x_by_col[0], RAIL_TOP_Y), (x_by_col[0], RAIL_BOTTOM_Y), "vertical")

    for index in range(column_count - 1):
        left_top, right_top = column_node(top_chain, index), column_node(top_chain, index + 1)
        left_bottom, right_bottom = column_node(bottom_chain, index), column_node(bottom_chain, index + 1)
        for edge in edge_by_pair.get(frozenset([left_top, right_top]), [])[:1]:
            place_edge(edge, left_top, right_top, (x_by_col[index], RAIL_TOP_Y), (x_by_col[index + 1], RAIL_TOP_Y), "horizontal")
        for edge in edge_by_pair.get(frozenset([left_bottom, right_bottom]), [])[:1]:
            place_edge(edge, left_bottom, right_bottom, (x_by_col[index], RAIL_BOTTOM_Y), (x_by_col[index + 1], RAIL_BOTTOM_Y), "horizontal")

    parallel_gap = float(_export_cfg(config).get("dxf_two_rail_parallel_rung_gap", 42.0))
    for index, (top_net, bottom_net) in enumerate(columns[1:], start=1):
        rungs = edge_by_pair.get(frozenset([top_net, bottom_net]), [])
        if not rungs:
            continue
        center = (len(rungs) - 1) / 2.0
        xs = [x_by_col[index] + (i - center) * parallel_gap for i in range(len(rungs))]
        if len(xs) > 1:
            wires.append({"segment_id": f"two_rail_top_tie_{index}", "orientation": "h", "x1": min(xs), "y1": RAIL_TOP_Y, "x2": max(xs), "y2": RAIL_TOP_Y})
            wires.append({"segment_id": f"two_rail_bottom_tie_{index}", "orientation": "h", "x1": min(xs), "y1": RAIL_BOTTOM_Y, "x2": max(xs), "y2": RAIL_BOTTOM_Y})
        for edge, x in zip(rungs, xs):
            place_edge(edge, top_net, bottom_net, (x, RAIL_TOP_Y), (x, RAIL_BOTTOM_Y), "vertical")
            node_points.setdefault(top_net, []).append((x, RAIL_TOP_Y))
            node_points.setdefault(bottom_net, []).append((x, RAIL_BOTTOM_Y))

    for node in redrawn.get("nodes", []):
        node_id = str(node.get("node_id"))
        points = node_points.get(node_id, [])
        if not points:
            continue
        node["x"] = sum(point[0] for point in points) / len(points)
        node["y"] = sum(point[1] for point in points) / len(points)
        node["points"] = [[point[0], point[1]] for point in points]
        node["bbox"] = [
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        ]

    redrawn["wires"] = wires
    redrawn["export_layout"] = {
        "mode": "schematic_two_rail_ladder_redraw",
        "normalized": True,
        "topology_redraw": True,
        "source_component": source_edge["component_id"],
        "top_chain": top_chain,
        "bottom_chain": bottom_chain,
        "column_count": column_count,
    }
    return redrawn


def _is_mesh_like_topology(topology: dict) -> bool:
    counts = sorted(_net_pin_count(topology).values(), reverse=True)
    if len(counts) < 4:
        return False
    return counts[0] <= 3 and counts[1] <= 3


def _topology_graph_redraw_layout(topology: dict, config: dict) -> dict | None:
    edges = _component_edges(topology)
    if len(edges) < 3:
        return None
    nodes = {
        str(node.get("node_id")): node
        for node in topology.get("nodes", [])
        if node.get("node_id")
    }
    if len(nodes) < 3:
        return None

    node_ids = [node_id for edge in edges for node_id in edge["nets"] if node_id in nodes]
    if len(set(node_ids)) < 3:
        return None
    xs = [float(nodes[node_id].get("x", 0.0) or 0.0) for node_id in set(node_ids)]
    ys = [float(nodes[node_id].get("y", 0.0) or 0.0) for node_id in set(node_ids)]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    target_w = max(360.0, min(720.0, span_x * 0.7))
    target_h = max(240.0, min(460.0, span_y * 0.7))

    def map_point(node_id: str) -> Point:
        node = nodes[node_id]
        x = RAIL_LEFT_X + BRANCH_MARGIN + (float(node.get("x", 0.0) or 0.0) - min_x) / span_x * target_w
        y = RAIL_TOP_Y + (float(node.get("y", 0.0) or 0.0) - min_y) / span_y * target_h
        return (round(x / 10.0) * 10.0, round(y / 10.0) * 10.0)

    node_points = {node_id: map_point(node_id) for node_id in set(node_ids)}
    pair_groups: dict[frozenset[str], list[dict]] = {}
    for edge in edges:
        pair_groups.setdefault(frozenset(edge["nets"]), []).append(edge)

    redrawn = deepcopy(topology)
    components = {str(component.get("id")): component for component in redrawn.get("components", [])}
    pin_groups = _pin_group_by_component(redrawn)
    wires = []
    wire_counter = 1

    def place_pin(group: dict, pin_id: str | None, x: float, y: float, axis: str, side: str) -> None:
        if not pin_id:
            return
        pin = _pin_ref_for_group(group, pin_id)
        if pin is not None:
            pin["x"], pin["y"], pin["axis"], pin["side"] = x, y, axis, side

    for pair, group_edges in pair_groups.items():
        pair_nodes = list(pair)
        if len(pair_nodes) != 2:
            continue
        a, b = pair_nodes
        if a not in node_points or b not in node_points:
            continue
        ax, ay = node_points[a]
        bx, by = node_points[b]
        dx, dy = bx - ax, by - ay
        length = max((dx * dx + dy * dy) ** 0.5, 1.0)
        px, py = -dy / length, dx / length
        offsets = [0.0]
        if len(group_edges) > 1:
            step = float(_export_cfg(config).get("dxf_graph_parallel_edge_gap", 34.0))
            center = (len(group_edges) - 1) / 2.0
            offsets = [(index - center) * step for index in range(len(group_edges))]
        for edge, offset in zip(sorted(group_edges, key=lambda item: item.get("x_hint", 0.0)), offsets):
            component = components.get(edge["component_id"])
            pin_group = pin_groups.get(edge["component_id"])
            if not component or not pin_group:
                continue
            p1 = (ax + px * offset, ay + py * offset)
            p2 = (bx + px * offset, by + py * offset)
            axis = "horizontal" if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]) else "vertical"
            if abs(offset) > 0.01:
                wires.append({"segment_id": f"graph_stub_{wire_counter}", "orientation": "h", "x1": ax, "y1": ay, "x2": p1[0], "y2": p1[1]})
                wire_counter += 1
                wires.append({"segment_id": f"graph_stub_{wire_counter}", "orientation": "h", "x1": bx, "y1": by, "x2": p2[0], "y2": p2[1]})
                wire_counter += 1
            place_pin(pin_group, edge["pin_by_net"].get(a), p1[0], p1[1], axis, "left" if axis == "horizontal" else "top")
            place_pin(pin_group, edge["pin_by_net"].get(b), p2[0], p2[1], axis, "right" if axis == "horizontal" else "bottom")
            pin_group["axis"] = axis
            pin_group["axis_source"] = "schematic_graph_redraw"
            component["bbox"] = [
                min(p1[0], p2[0]) - 18.0,
                min(p1[1], p2[1]) - 18.0,
                max(p1[0], p2[0]) + 18.0,
                max(p1[1], p2[1]) + 18.0,
            ]

    for node in redrawn.get("nodes", []):
        node_id = str(node.get("node_id"))
        point = node_points.get(node_id)
        if not point:
            continue
        node["x"], node["y"] = point
        node["points"] = [[point[0], point[1]]]
        node["bbox"] = [point[0], point[1], point[0], point[1]]

    redrawn["wires"] = wires
    redrawn["export_layout"] = {
        "mode": "schematic_graph_redraw",
        "normalized": True,
        "topology_redraw": True,
        "node_count": len(node_points),
        "edge_count": len(edges),
        "parallel_edge_groups": sum(1 for items in pair_groups.values() if len(items) > 1),
    }
    return redrawn


def _topology_redraw_layout(topology: dict, config: dict) -> dict | None:
    ladder = _topology_ladder_redraw_layout(topology, config)
    if ladder is not None:
        return ladder

    two_rail = _topology_two_rail_ladder_redraw_layout(topology, config)
    if two_rail is not None:
        return two_rail

    if _is_mesh_like_topology(topology):
        graph = _topology_graph_redraw_layout(topology, config)
        if graph is not None:
            return graph

    rail_pair = _choose_rail_pair(topology)
    if not rail_pair:
        return None
    top_net, bottom_net = rail_pair
    edges = _component_edges(topology)
    edge_lookup = {edge["component_id"]: edge for edge in edges}
    paths = _find_rail_paths(topology, top_net, bottom_net)
    if len(paths) < 2:
        return None

    paths.sort(key=lambda path: _path_x_hint(path, edge_lookup))
    branch_x = {
        tuple(path): RAIL_LEFT_X + BRANCH_MARGIN + index * BRANCH_SPACING
        for index, path in enumerate(paths)
    }
    rail_end_margin = max(
        0.0,
        float(_export_cfg(config).get("dxf_topology_redraw_rail_margin", RAIL_END_MARGIN)),
    )
    rail_start = min(branch_x.values()) - rail_end_margin
    rail_end = max(branch_x.values()) + rail_end_margin

    redrawn = deepcopy(topology)
    components = {str(component.get("id")): component for component in redrawn.get("components", [])}
    pin_groups = _pin_group_by_component(redrawn)
    wires = [
        {"segment_id": "sch_top_rail", "orientation": "h", "x1": rail_start, "y1": RAIL_TOP_Y, "x2": rail_end, "y2": RAIL_TOP_Y},
        {"segment_id": "sch_bottom_rail", "orientation": "h", "x1": rail_start, "y1": RAIL_BOTTOM_Y, "x2": rail_end, "y2": RAIL_BOTTOM_Y},
    ]
    node_points: dict[str, list[Point]] = {top_net: [], bottom_net: []}

    for path in paths:
        x = branch_x[tuple(path)]
        y_values = [
            RAIL_TOP_Y + (RAIL_BOTTOM_Y - RAIL_TOP_Y) * index / len(path)
            for index in range(len(path) + 1)
        ]
        current_net = top_net
        for index, component_id in enumerate(path):
            edge = edge_lookup[component_id]
            net_a, net_b = edge["nets"]
            next_net = net_b if net_a == current_net else net_a
            y1 = y_values[index]
            y2 = y_values[index + 1]
            component = components.get(component_id)
            group = pin_groups.get(component_id)
            if not component or not group:
                current_net = next_net
                continue
            pin_a = edge["pin_by_net"].get(current_net)
            pin_b = edge["pin_by_net"].get(next_net)
            if pin_a:
                pin = _pin_ref_for_group(group, pin_a)
                if pin is not None:
                    pin["x"], pin["y"], pin["axis"], pin["side"] = x, y1, "vertical", "top"
            if pin_b:
                pin = _pin_ref_for_group(group, pin_b)
                if pin is not None:
                    pin["x"], pin["y"], pin["axis"], pin["side"] = x, y2, "vertical", "bottom"
            group["axis"] = "vertical"
            group["axis_source"] = "schematic_redraw"
            component["bbox"] = [x - 22.0, min(y1, y2), x + 22.0, max(y1, y2)]
            node_points.setdefault(current_net, []).append((x, y1))
            node_points.setdefault(next_net, []).append((x, y2))
            current_net = next_net

    for node in redrawn.get("nodes", []):
        node_id = str(node.get("node_id"))
        points = node_points.get(node_id, [])
        if not points:
            continue
        node["x"] = sum(point[0] for point in points) / len(points)
        node["y"] = sum(point[1] for point in points) / len(points)
        node["points"] = [[point[0], point[1]] for point in points]
        node["bbox"] = [
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        ]

    redrawn["wires"] = wires
    redrawn["export_layout"] = {
        "mode": "schematic_redraw",
        "normalized": True,
        "topology_redraw": True,
        "top_net": top_net,
        "bottom_net": bottom_net,
        "rail_start_x": rail_start,
        "rail_end_x": rail_end,
        "branch_count": len(paths),
        "branches": [
            {
                "x": branch_x[tuple(path)],
                "components": path,
                "labels": [_component_refdes(edge_lookup[item]["component"]) for item in path if item in edge_lookup],
            }
            for path in paths
        ],
    }
    return redrawn


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
    if export_cfg.get("dxf_topology_redraw", True):
        redrawn = _topology_redraw_layout(normalized, config)
        if redrawn is not None:
            redrawn["export_layout"]["original_wire_count"] = original_wire_count
            redrawn["export_layout"]["export_wire_count"] = len(redrawn.get("wires", []))
            return redrawn
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
