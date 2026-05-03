"""
本文件的作用：
- 汇总 components、pins、nodes、wires、matches，构造最终 topology。
- 同时把 topology 进一步整理成 nets、component_nets 和 netlist，供校验、导出和展示复用。
"""

from __future__ import annotations


CLASS_PREFIXES = {
    "resistor": "R",
    "capacitor.unpolarized": "C",
    "inductor": "L",
    "power_source": "V",
    "voltage.ac": "V",
    "voltage.dc": "V",
    "voltage.battery": "V",
    "gnd": "GND",
    "vss": "VSS",
}


def _class_to_prefix(class_name: str) -> str:
    return CLASS_PREFIXES.get(class_name, "X")


def _annotate_components(components: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    counters: dict[str, int] = {}
    annotated_components = []
    component_lookup: dict[str, dict] = {}

    for component in components:
        class_name = component.get("class_name", "unknown")
        prefix = _class_to_prefix(class_name)
        counters[prefix] = counters.get(prefix, 0) + 1
        refdes = f"{prefix}{counters[prefix]}"
        annotated = {
            **component,
            "refdes": refdes,
        }
        annotated_components.append(annotated)
        component_lookup[annotated["id"]] = annotated

    return annotated_components, component_lookup


def _pin_number(pin: dict) -> str:
    pin_id = pin.get("pin_id", "")
    if "_p" in pin_id:
        return pin_id.rsplit("_p", 1)[-1]
    return pin.get("side", "")


def _pin_ref(pin: dict, component_lookup: dict[str, dict]) -> str:
    component = component_lookup.get(pin["component_id"], {})
    refdes = component.get("refdes", pin["component_id"])
    return f"{refdes}.{_pin_number(pin)}"


def _build_net_views(
    components: list[dict],
    pins: list[dict],
    nodes: list[dict],
    connections: list[dict],
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    annotated_components, component_lookup = _annotate_components(components)
    pin_lookup: dict[str, dict] = {}
    for pin_group in pins:
        component_id = pin_group["component_id"]
        for pin in pin_group.get("pins", []):
            pin_lookup[pin["pin_id"]] = {
                **pin,
                "component_id": component_id,
            }

    node_lookup = {node["node_id"]: node for node in nodes}
    net_pins: dict[str, list[dict]] = {}
    for connection in connections:
        node_id = connection["node_id"]
        pin_id = connection["pin_id"]
        pin = pin_lookup.get(pin_id)
        if pin is None:
            continue
        net_pins.setdefault(node_id, []).append(
            {
                "pin_id": pin_id,
                "component_id": pin["component_id"],
                "side": pin.get("side"),
            }
        )

    nets = []
    for node_id, node in node_lookup.items():
        attached_pins = net_pins.get(node_id, [])
        pin_refs = [_pin_ref(item, component_lookup) for item in attached_pins]
        nets.append(
            {
                "net_id": node_id,
                "name": node_id,
                "node_id": node_id,
                "pin_count": len(attached_pins),
                "pins": attached_pins,
                "pin_refs": pin_refs,
                "component_ids": sorted({item["component_id"] for item in attached_pins}),
                "component_refs": sorted(
                    {
                        component_lookup[item["component_id"]]["refdes"]
                        for item in attached_pins
                        if item["component_id"] in component_lookup
                    }
                ),
                "bbox": node.get("bbox"),
                "points": node.get("points", []),
            }
        )

    component_nets = []
    for component in components:
        component_id = component["id"]
        component_connections = [item for item in connections if item["component_id"] == component_id]
        component = component_lookup.get(component_id, component)
        component_nets.append(
            {
                "component_id": component_id,
                "refdes": component.get("refdes", component_id),
                "class_name": component.get("class_name", "unknown"),
                "pins": [
                    {
                        "pin_id": item["pin_id"],
                        "net_id": item["node_id"],
                    }
                    for item in component_connections
                ],
                "net_ids": [item["node_id"] for item in component_connections],
            }
        )

    return nets, component_nets, {
        "components": annotated_components,
        "lookup": component_lookup,
        "pins": pin_lookup,
    }


def _build_netlist(
    components: list[dict],
    nets: list[dict],
    component_nets: list[dict],
    pin_lookup: dict[str, dict],
    component_lookup: dict[str, dict],
) -> dict:
    netlist_components = []
    for component_net in component_nets:
        component_id = component_net["component_id"]
        component = component_lookup[component_id]
        pin_entries = []
        for pin_entry in component_net.get("pins", []):
            pin = pin_lookup.get(pin_entry["pin_id"], {})
            pin_entries.append(
                {
                    "pin_id": pin_entry["pin_id"],
                    "pin_ref": _pin_ref(
                        {
                            "pin_id": pin_entry["pin_id"],
                            "component_id": component_id,
                            "side": pin.get("side"),
                        },
                        component_lookup,
                    ),
                    "pin_number": _pin_number(pin_entry),
                    "side": pin.get("side"),
                    "net_id": pin_entry["net_id"],
                }
            )
        netlist_components.append(
            {
                "component_id": component_id,
                "refdes": component["refdes"],
                "class_name": component.get("class_name", "unknown"),
                "pin_count": len(pin_entries),
                "pins": pin_entries,
                "net_ids": component_net.get("net_ids", []),
            }
        )

    netlist_nets = []
    for net in nets:
        netlist_nets.append(
            {
                "net_id": net["net_id"],
                "name": net.get("name", net["net_id"]),
                "pin_count": net.get("pin_count", 0),
                "pin_refs": net.get("pin_refs", []),
                "component_refs": net.get("component_refs", []),
            }
        )

    return {
        "components": netlist_components,
        "nets": netlist_nets,
    }


def build_topology(
    perception_result: dict,
    pin_result: dict,
    node_result: dict,
    match_result: dict,
) -> dict:
    """Build the final topology object and its derived netlist views."""
    components = perception_result["components"]
    wires = perception_result["wire_segments"]
    nodes = node_result["nodes"]
    valid_node_ids = {node["node_id"] for node in nodes}
    node_id_map = node_result.get("node_id_map", {})
    pins = pin_result["pins"]
    connections = []
    for match in match_result["matches"]:
        raw_node_id = match.get("node_id")
        node_id = node_id_map.get(raw_node_id, raw_node_id)
        if node_id not in valid_node_ids:
            continue
        connection = {
            **match,
            "node_id": node_id,
        }
        if raw_node_id != node_id:
            connection["raw_node_id"] = raw_node_id
        connections.append(connection)

    nets, component_nets, metadata = _build_net_views(components, pins, nodes, connections)
    annotated_components = metadata["components"]
    component_lookup = metadata["lookup"]
    pin_lookup = metadata["pins"]
    netlist = _build_netlist(annotated_components, nets, component_nets, pin_lookup, component_lookup)

    return {
        "components": annotated_components,
        "pins": pins,
        "nodes": nodes,
        "raw_nodes": node_result.get("raw_nodes", nodes),
        "discarded_nodes": node_result.get("discarded_nodes", []),
        "node_id_map": node_id_map,
        "bridge_connections": node_result.get("bridge_connections", []),
        "wires": wires,
        "connections": connections,
        "nets": nets,
        "component_nets": component_nets,
        "netlist": netlist,
        "stats": {
            "component_count": len(annotated_components),
            "pin_group_count": len(pins),
            "node_count": len(nodes),
            "raw_node_count": len(node_result.get("raw_nodes", nodes)),
            "discarded_node_count": len(node_result.get("discarded_nodes", [])),
            "wire_count": len(wires),
            "connection_count": len(connections),
            "net_count": len(nets),
        },
    }
