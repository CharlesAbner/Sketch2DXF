"""Export recovered topology/netlist results to editable DXF.

The exporter consumes an in-memory topology object. In clean mode, the layout
engine may redraw common circuit structures into standardized schematic
layouts before DXF entities are written.
"""

from __future__ import annotations

from pathlib import Path

from src.config import OUTPUTS_DIR
from src.export.dxf_symbols import get_dxf_symbol_definition
from src.export.layout_engine import normalize_layout
from src.io_utils.json_io import save_json


LAYER_DEFS = {
    "WIRES": {"color": 7, "lineweight": 30},
    "COMPONENTS": {"color": 7, "lineweight": 35},
    "LABELS": {"color": 3, "lineweight": 18},
    "NETS": {"color": 5, "lineweight": 18},
    "PINS": {"color": 1, "lineweight": 13},
    "NODES": {"color": 6, "lineweight": 18},
    "JUNCTIONS": {"color": 7, "lineweight": 35},
    "TITLE": {"color": 8, "lineweight": 13},
    "REPAIR": {"color": 30, "lineweight": 13},
}

RESISTOR_BODY_LENGTH = 54.0
RESISTOR_ZIGZAG_WIDTH = 8.0
CAPACITOR_PLATE_LENGTH = 32.0
CAPACITOR_GAP = 5.0
SOURCE_LONG_PLATE = 36.0
SOURCE_SHORT_PLATE = 22.0
SOURCE_GAP = 6.0
BOX_BODY_LENGTH = 46.0
BOX_BODY_WIDTH = 18.0


def _unit_axis(p1: tuple[float, float], p2: tuple[float, float], axis: str) -> tuple[float, float]:
    if axis == "vertical":
        return (0.0, 1.0 if p2[1] >= p1[1] else -1.0)
    return (1.0 if p2[0] >= p1[0] else -1.0, 0.0)


def _axis_span(
    p1: tuple[float, float],
    p2: tuple[float, float],
    axis: str,
    body_length: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    ux, uy = _unit_axis(p1, p2, axis)
    if axis == "vertical":
        center = (p1[0], (p1[1] + p2[1]) / 2.0)
    else:
        center = ((p1[0] + p2[0]) / 2.0, p1[1])
    half = min(body_length / 2.0, max(abs(p2[0] - p1[0]), abs(p2[1] - p1[1])) * 0.42)
    start = (center[0] - ux * half, center[1] - uy * half)
    end = (center[0] + ux * half, center[1] + uy * half)
    return center, start, end


def _draw_leads(
    msp,
    p1: tuple[float, float],
    p2: tuple[float, float],
    body_start: tuple[float, float],
    body_end: tuple[float, float],
) -> None:
    msp.add_line(p1, body_start, dxfattribs={"layer": "COMPONENTS"})
    msp.add_line(body_end, p2, dxfattribs={"layer": "COMPONENTS"})


def _export_cfg(config: dict) -> dict:
    return config.get("export", {})


def _dxf_mode(config: dict) -> str:
    return str(_export_cfg(config).get("dxf_mode", "clean")).lower()


def _is_clean_mode(config: dict) -> bool:
    return _dxf_mode(config) == "clean"


def _export_bool(config: dict, key: str, clean_default: bool, debug_default: bool) -> bool:
    export_cfg = _export_cfg(config)
    if export_cfg.get(key) is not None:
        return bool(export_cfg[key])
    return clean_default if _is_clean_mode(config) else debug_default


def _cad_point(x: float, y: float, max_y: float) -> tuple[float, float]:
    return float(x), float(max_y - y)


def _component_axis(pins_by_component: dict[str, dict], component_id: str) -> str:
    pin_group = pins_by_component.get(component_id, {})
    return pin_group.get("axis", "horizontal")


def _segment_points(
    segment: dict,
    max_y: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    return _cad_point(segment["x1"], segment["y1"], max_y), _cad_point(segment["x2"], segment["y2"], max_y)


def _draw_resistor(msp, p1: tuple[float, float], p2: tuple[float, float], axis: str) -> None:
    _, body_start, body_end = _axis_span(p1, p2, axis, RESISTOR_BODY_LENGTH)
    _draw_leads(msp, p1, p2, body_start, body_end)
    if axis == "vertical":
        x = body_start[0]
        y1, y2 = body_start[1], body_end[1]
        step = (y2 - y1) / 6.0
        width = RESISTOR_ZIGZAG_WIDTH
        points = [
            (x, y1),
            (x - width, y1 + step),
            (x + width, y1 + step * 2),
            (x - width, y1 + step * 3),
            (x + width, y1 + step * 4),
            (x - width, y1 + step * 5),
            (x, y2),
        ]
    else:
        y = body_start[1]
        x1, x2 = body_start[0], body_end[0]
        step = (x2 - x1) / 6.0
        height = RESISTOR_ZIGZAG_WIDTH
        points = [
            (x1, y),
            (x1 + step, y + height),
            (x1 + step * 2, y - height),
            (x1 + step * 3, y + height),
            (x1 + step * 4, y - height),
            (x1 + step * 5, y + height),
            (x2, y),
        ]
    msp.add_lwpolyline(points, dxfattribs={"layer": "COMPONENTS"})


def _draw_capacitor(msp, p1: tuple[float, float], p2: tuple[float, float], axis: str) -> None:
    center, _, _ = _axis_span(p1, p2, axis, CAPACITOR_PLATE_LENGTH)
    ux, uy = _unit_axis(p1, p2, axis)
    lead_a = (center[0] - ux * CAPACITOR_GAP, center[1] - uy * CAPACITOR_GAP)
    lead_b = (center[0] + ux * CAPACITOR_GAP, center[1] + uy * CAPACITOR_GAP)
    _draw_leads(msp, p1, p2, lead_a, lead_b)
    if axis == "vertical":
        x = center[0]
        half_width = CAPACITOR_PLATE_LENGTH / 2.0
        msp.add_line((x - half_width, lead_a[1]), (x + half_width, lead_a[1]), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((x - half_width, lead_b[1]), (x + half_width, lead_b[1]), dxfattribs={"layer": "COMPONENTS"})
    else:
        y = center[1]
        half_height = CAPACITOR_PLATE_LENGTH / 2.0
        msp.add_line((lead_a[0], y - half_height), (lead_a[0], y + half_height), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((lead_b[0], y - half_height), (lead_b[0], y + half_height), dxfattribs={"layer": "COMPONENTS"})


def _draw_power_source(msp, p1: tuple[float, float], p2: tuple[float, float], axis: str) -> None:
    center, _, _ = _axis_span(p1, p2, axis, SOURCE_LONG_PLATE)
    ux, uy = _unit_axis(p1, p2, axis)
    lead_a = (center[0] - ux * SOURCE_GAP, center[1] - uy * SOURCE_GAP)
    lead_b = (center[0] + ux * SOURCE_GAP, center[1] + uy * SOURCE_GAP)
    _draw_leads(msp, p1, p2, lead_a, lead_b)
    if axis == "vertical":
        x = center[0]
        msp.add_line((x - SOURCE_LONG_PLATE / 2.0, lead_a[1]), (x + SOURCE_LONG_PLATE / 2.0, lead_a[1]), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((x - SOURCE_SHORT_PLATE / 2.0, lead_b[1]), (x + SOURCE_SHORT_PLATE / 2.0, lead_b[1]), dxfattribs={"layer": "COMPONENTS"})
    else:
        y = center[1]
        msp.add_line((lead_a[0], y - SOURCE_SHORT_PLATE / 2.0), (lead_a[0], y + SOURCE_SHORT_PLATE / 2.0), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((lead_b[0], y - SOURCE_LONG_PLATE / 2.0), (lead_b[0], y + SOURCE_LONG_PLATE / 2.0), dxfattribs={"layer": "COMPONENTS"})


def _draw_box(msp, p1: tuple[float, float], p2: tuple[float, float], axis: str) -> None:
    _, body_start, body_end = _axis_span(p1, p2, axis, BOX_BODY_LENGTH)
    _draw_leads(msp, p1, p2, body_start, body_end)
    if axis == "vertical":
        x = body_start[0]
        y1, y2 = sorted([body_start[1], body_end[1]])
        half_width = BOX_BODY_WIDTH / 2.0
        points = [
            (x - half_width, y1),
            (x - half_width, y2),
            (x + half_width, y2),
            (x + half_width, y1),
            (x - half_width, y1),
        ]
    else:
        y = body_start[1]
        x1, x2 = sorted([body_start[0], body_end[0]])
        half_height = BOX_BODY_WIDTH / 2.0
        points = [
            (x1, y - half_height),
            (x2, y - half_height),
            (x2, y + half_height),
            (x1, y + half_height),
            (x1, y - half_height),
        ]
    msp.add_lwpolyline(points, dxfattribs={"layer": "COMPONENTS"})


def _draw_component_symbol(msp, component: dict, pin_group: dict, max_y: float) -> None:
    symbol = get_dxf_symbol_definition(component.get("class_name", "unknown"))
    pins = pin_group.get("pins", [])
    if len(pins) != 2:
        return

    axis = pin_group.get("axis", "horizontal")
    p1 = _cad_point(pins[0]["x"], pins[0]["y"], max_y)
    p2 = _cad_point(pins[1]["x"], pins[1]["y"], max_y)
    primitive = symbol.get("primitive", "box")
    if primitive == "resistor":
        _draw_box(msp, p1, p2, axis)
    elif primitive == "capacitor":
        _draw_capacitor(msp, p1, p2, axis)
    elif primitive == "power_source":
        _draw_power_source(msp, p1, p2, axis)
    else:
        _draw_box(msp, p1, p2, axis)


def _component_label_anchor(component: dict, axis: str, max_y: float) -> tuple[float, float]:
    x1, y1, x2, y2 = component["bbox"]
    if axis == "vertical":
        return _cad_point(x2 + 12.0, (y1 + y2) / 2.0 - 10.0, max_y)
    return _cad_point((x1 + x2) / 2.0 - 10.0, y1 - 16.0, max_y)


def _add_text(msp, text: str, point: tuple[float, float], height: float, layer: str) -> None:
    if not text:
        return
    msp.add_text(str(text), dxfattribs={"height": height, "layer": layer}).set_placement(point)


def _draw_pin_markers(msp, pin_groups: list[dict], max_y: float) -> None:
    for group in pin_groups:
        for pin in group.get("pins", []):
            x, y = _cad_point(pin.get("x", 0), pin.get("y", 0), max_y)
            msp.add_circle((x, y), radius=1.8, dxfattribs={"layer": "PINS"})
            label = pin.get("pin_ref") or pin.get("pin_id")
            _add_text(msp, label, (x + 2.5, y - 2.5), 2.5, "PINS")


def _draw_node_markers(
    msp,
    nodes: list[dict],
    nets: list[dict],
    max_y: float,
    show_labels: bool,
) -> None:
    net_names = {net.get("net_id"): net.get("name", net.get("net_id")) for net in nets}
    for node in nodes:
        x, y = _cad_point(node.get("x", 0), node.get("y", 0), max_y)
        msp.add_circle((x, y), radius=2.2, dxfattribs={"layer": "NODES"})
        if show_labels:
            node_id = node.get("node_id")
            label = net_names.get(node_id, node_id)
            _add_text(msp, label, (x + 4.0, y + 4.0), 4.0, "NETS")


def _draw_junction_dots(msp, nodes: list[dict], max_y: float) -> None:
    for node in nodes:
        pin_count = int(node.get("terminal_support_count", len(node.get("pin_ids", [])) or 0))
        component_count = len(node.get("component_ids", []))
        has_junction = bool(node.get("junction_ids"))
        if pin_count < 3 and component_count < 3 and not has_junction:
            continue
        x, y = _cad_point(node.get("x", 0), node.get("y", 0), max_y)
        msp.add_circle((x, y), radius=3.2, dxfattribs={"layer": "JUNCTIONS"})
        try:
            msp.add_solid(
                [(x - 2.2, y), (x, y + 2.2), (x + 2.2, y), (x, y - 2.2)],
                dxfattribs={"layer": "JUNCTIONS"},
            )
        except Exception:
            pass


def _drawing_bounds(topology: dict, max_y: float) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for wire in topology.get("wires", []):
        xs.extend([float(wire["x1"]), float(wire["x2"])])
        ys.extend([float(wire["y1"]), float(wire["y2"])])
    for component in topology.get("components", []):
        x1, y1, x2, y2 = component["bbox"]
        xs.extend([float(x1), float(x2)])
        ys.extend([float(y1), float(y2)])
    if not xs or not ys:
        return 0.0, 0.0, 160.0, 80.0
    cad_ys = [max_y - y for y in ys]
    return min(xs), min(cad_ys), max(xs), max(cad_ys)


def _draw_title_block(msp, topology: dict, output_stem: str, max_y: float) -> None:
    min_x, min_y, max_x, _ = _drawing_bounds(topology, max_y)
    x0 = min_x
    y0 = min_y - 32.0
    width = max(180.0, max_x - min_x)
    height = 24.0
    points = [
        (x0, y0),
        (x0 + width, y0),
        (x0 + width, y0 + height),
        (x0, y0 + height),
        (x0, y0),
    ]
    msp.add_lwpolyline(points, dxfattribs={"layer": "TITLE"})
    _add_text(msp, f"Sketch2DXF: {output_stem}", (x0 + 4.0, y0 + 16.0), 4.0, "TITLE")
    stats = topology.get("stats", {})
    summary = (
        f"components={len(topology.get('components', []))} "
        f"nets={len(topology.get('nets', []))} "
        f"nodes={len(topology.get('nodes', []))} "
        f"connections={stats.get('connection_count', len(topology.get('connections', [])))}"
    )
    _add_text(msp, summary, (x0 + 4.0, y0 + 9.0), 3.0, "TITLE")
    repair_history = topology.get("repair_history", [])
    approval = topology.get("human_approval", {})
    if repair_history:
        last_repair = repair_history[-1]
        repair_text = (
            f"repair={last_repair.get('candidate_id')} "
            f"type={last_repair.get('repair_type')} "
            f"approval={approval.get('decision', 'n/a')}"
        )
        _add_text(msp, repair_text, (x0 + 4.0, y0 + 3.0), 3.0, "REPAIR")


def _resolve_output_stem(config: dict) -> str:
    export_cfg = config.get("export", {})
    output_stem = export_cfg.get("output_stem")
    if output_stem:
        return str(output_stem)
    return "result"


def export_to_dxf(topology_result: dict, config: dict) -> dict:
    """Export the recovered circuit as a minimal but readable DXF document."""
    export_errors: list[str] = []
    output_stem = _resolve_output_stem(config)
    dxf_mode = _dxf_mode(config)
    output_path = OUTPUTS_DIR / "dxf" / f"{output_stem}.dxf"
    netlist_path = OUTPUTS_DIR / "netlist" / f"{output_stem}_netlist.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    netlist_path.parent.mkdir(parents=True, exist_ok=True)

    normalized_topology = normalize_layout(topology_result, config)
    save_json(netlist_path, normalized_topology.get("netlist", {}))

    try:
        import ezdxf

        doc = ezdxf.new(dxfversion="R2010")
        doc.units = 4  # millimeters
        for layer_name, attrs in LAYER_DEFS.items():
            color = int(attrs["color"])
            lineweight = int(attrs["lineweight"])
            if layer_name not in doc.layers:
                doc.layers.add(layer_name, color=color, lineweight=lineweight)
            else:
                layer = doc.layers.get(layer_name)
                layer.dxf.color = color
                layer.dxf.lineweight = lineweight

        msp = doc.modelspace()
        wires = normalized_topology.get("wires", [])
        components = normalized_topology.get("components", [])
        pin_groups = {group["component_id"]: group for group in normalized_topology.get("pins", [])}
        nodes = normalized_topology.get("nodes", [])

        all_y = []
        for wire in wires:
            all_y.extend([wire["y1"], wire["y2"]])
        for component in components:
            all_y.extend([component["bbox"][1], component["bbox"][3]])
        max_y = max(all_y) if all_y else 0.0

        for wire in wires:
            p1, p2 = _segment_points(wire, max_y)
            msp.add_line(p1, p2, dxfattribs={"layer": "WIRES"})

        for component in components:
            component_id = component["id"]
            pin_group = pin_groups.get(component_id, {})
            axis = _component_axis(pin_groups, component_id)
            _draw_component_symbol(msp, component, pin_group, max_y)
            if _export_bool(config, "dxf_show_component_labels", True, True):
                label_x, label_y = _component_label_anchor(component, axis, max_y)
                label = component.get("refdes", component_id)
                _add_text(msp, label, (label_x, label_y), 6.0 if _is_clean_mode(config) else 6.0, "LABELS")

        if _export_bool(config, "dxf_show_junction_dots", True, True):
            _draw_junction_dots(msp, nodes, max_y)
        if _export_bool(config, "dxf_show_pin_markers", False, True):
            _draw_pin_markers(msp, list(pin_groups.values()), max_y)
        if _export_bool(config, "dxf_show_node_markers", False, True):
            _draw_node_markers(
                msp,
                nodes,
                normalized_topology.get("nets", []),
                max_y,
                show_labels=_export_bool(config, "dxf_show_net_labels", False, True),
            )
        if _export_bool(config, "dxf_show_title_block", False, True):
            _draw_title_block(msp, normalized_topology, output_stem, max_y)

        doc.saveas(output_path)
        return {
            "dxf_path": str(output_path),
            "json_path": str(netlist_path),
            "export_success": True,
            "export_errors": export_errors,
            "dxf_mode": dxf_mode,
            "layout": normalized_topology.get("export_layout", {}),
        }
    except Exception as exc:  # pragma: no cover
        export_errors.append(str(exc))
        return {
            "dxf_path": str(output_path),
            "json_path": str(netlist_path),
            "export_success": False,
            "export_errors": export_errors,
            "dxf_mode": dxf_mode,
            "layout": normalized_topology.get("export_layout", {}),
        }
