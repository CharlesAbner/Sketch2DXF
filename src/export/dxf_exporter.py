"""
本文件的作用：
- 根据 topology / netlist 结果导出可打开的第一版 DXF。
- 当前重点是“结果可检查、结构可读”，而不是最终的高质量 schematic 美化。
"""

from __future__ import annotations

from pathlib import Path

from src.config import OUTPUTS_DIR
from src.export.dxf_symbols import get_dxf_symbol_definition
from src.export.layout_engine import normalize_layout
from src.io_utils.json_io import save_json


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
    if axis == "vertical":
        x = p1[0]
        y1, y2 = sorted([p1[1], p2[1]])
        step = (y2 - y1) / 6.0
        width = max(4.0, abs(y2 - y1) * 0.12)
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
        y = p1[1]
        x1, x2 = sorted([p1[0], p2[0]])
        step = (x2 - x1) / 6.0
        height = max(4.0, abs(x2 - x1) * 0.12)
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
    if axis == "vertical":
        x = p1[0]
        y1, y2 = sorted([p1[1], p2[1]])
        mid = (y1 + y2) / 2.0
        gap = max(3.0, abs(y2 - y1) * 0.08)
        half_width = max(6.0, abs(y2 - y1) * 0.18)
        msp.add_line((x, y1), (x, mid + gap), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((x, mid - gap), (x, y2), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((x - half_width, mid + gap), (x + half_width, mid + gap), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((x - half_width, mid - gap), (x + half_width, mid - gap), dxfattribs={"layer": "COMPONENTS"})
    else:
        y = p1[1]
        x1, x2 = sorted([p1[0], p2[0]])
        mid = (x1 + x2) / 2.0
        gap = max(3.0, abs(x2 - x1) * 0.08)
        half_height = max(6.0, abs(x2 - x1) * 0.18)
        msp.add_line((x1, y), (mid - gap, y), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((mid + gap, y), (x2, y), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((mid - gap, y - half_height), (mid - gap, y + half_height), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((mid + gap, y - half_height), (mid + gap, y + half_height), dxfattribs={"layer": "COMPONENTS"})


def _draw_power_source(msp, p1: tuple[float, float], p2: tuple[float, float], axis: str) -> None:
    if axis == "vertical":
        x = p1[0]
        y1, y2 = sorted([p1[1], p2[1]])
        mid = (y1 + y2) / 2.0
        gap = max(3.0, abs(y2 - y1) * 0.08)
        short_half = max(4.0, abs(y2 - y1) * 0.10)
        long_half = max(7.0, abs(y2 - y1) * 0.18)
        msp.add_line((x, y1), (x, mid + gap), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((x, mid - gap), (x, y2), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((x - long_half, mid + gap), (x + long_half, mid + gap), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((x - short_half, mid - gap), (x + short_half, mid - gap), dxfattribs={"layer": "COMPONENTS"})
    else:
        y = p1[1]
        x1, x2 = sorted([p1[0], p2[0]])
        mid = (x1 + x2) / 2.0
        gap = max(3.0, abs(x2 - x1) * 0.08)
        short_half = max(4.0, abs(x2 - x1) * 0.10)
        long_half = max(7.0, abs(x2 - x1) * 0.18)
        msp.add_line((x1, y), (mid - gap, y), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((mid + gap, y), (x2, y), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((mid - gap, y - short_half), (mid - gap, y + short_half), dxfattribs={"layer": "COMPONENTS"})
        msp.add_line((mid + gap, y - long_half), (mid + gap, y + long_half), dxfattribs={"layer": "COMPONENTS"})


def _draw_box(msp, p1: tuple[float, float], p2: tuple[float, float], axis: str) -> None:
    if axis == "vertical":
        x = p1[0]
        y1, y2 = sorted([p1[1], p2[1]])
        half_width = max(6.0, abs(y2 - y1) * 0.18)
        points = [
            (x - half_width, y1),
            (x - half_width, y2),
            (x + half_width, y2),
            (x + half_width, y1),
            (x - half_width, y1),
        ]
    else:
        y = p1[1]
        x1, x2 = sorted([p1[0], p2[0]])
        half_height = max(6.0, abs(x2 - x1) * 0.18)
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
        _draw_resistor(msp, p1, p2, axis)
    elif primitive == "capacitor":
        _draw_capacitor(msp, p1, p2, axis)
    elif primitive == "power_source":
        _draw_power_source(msp, p1, p2, axis)
    else:
        _draw_box(msp, p1, p2, axis)


def _component_label_anchor(component: dict, axis: str, max_y: float) -> tuple[float, float]:
    x1, y1, x2, y2 = component["bbox"]
    if axis == "vertical":
        return _cad_point((x1 + x2) / 2.0 + 10.0, y1, max_y)
    return _cad_point(x1, y1 - 12.0, max_y)


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


def _draw_node_markers(msp, nodes: list[dict], nets: list[dict], max_y: float) -> None:
    net_names = {net.get("net_id"): net.get("name", net.get("net_id")) for net in nets}
    for node in nodes:
        x, y = _cad_point(node.get("x", 0), node.get("y", 0), max_y)
        msp.add_circle((x, y), radius=2.2, dxfattribs={"layer": "NODES"})
        node_id = node.get("node_id")
        label = net_names.get(node_id, node_id)
        _add_text(msp, label, (x + 4.0, y + 4.0), 4.0, "NETS")


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
        for layer_name, color in (
            ("WIRES", 2),
            ("COMPONENTS", 7),
            ("LABELS", 3),
            ("NETS", 5),
            ("PINS", 1),
            ("NODES", 6),
            ("TITLE", 8),
            ("REPAIR", 30),
        ):
            if layer_name not in doc.layers:
                doc.layers.add(layer_name, color=color)

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
            label_x, label_y = _component_label_anchor(component, axis, max_y)
            label = component.get("refdes", component_id)
            _add_text(msp, label, (label_x, label_y), 6.0, "LABELS")

        _draw_pin_markers(msp, list(pin_groups.values()), max_y)
        _draw_node_markers(msp, nodes, normalized_topology.get("nets", []), max_y)
        _draw_title_block(msp, normalized_topology, output_stem, max_y)

        doc.saveas(output_path)
        return {
            "dxf_path": str(output_path),
            "json_path": str(netlist_path),
            "export_success": True,
            "export_errors": export_errors,
        }
    except Exception as exc:  # pragma: no cover
        export_errors.append(str(exc))
        return {
            "dxf_path": str(output_path),
            "json_path": str(netlist_path),
            "export_success": False,
            "export_errors": export_errors,
        }
