"""
本文件的作用：
- 对 topology / netlist 做轻量规则校验，帮助快速发现明显不合理的结构。
- 当前只保留最直观、最容易解释的规则，便于调试和答辩展示。
"""

from __future__ import annotations


def check_topology_consistency(topology: dict, config: dict) -> dict:
    """Run a small rule-based consistency check on the recovered topology."""
    _ = config
    warnings = []
    errors = []

    if not topology["components"]:
        warnings.append("No components detected.")
    if not topology["connections"]:
        warnings.append("No component-node connections recovered.")

    components = topology.get("components", [])
    pins = topology.get("pins", [])
    nets = topology.get("nets", [])

    pin_count_lookup = {
        pin_group["component_id"]: int(pin_group.get("pin_count", len(pin_group.get("pins", []))))
        for pin_group in pins
    }
    connected_pins_by_component: dict[str, set[str]] = {}
    for connection in topology.get("connections", []):
        connected_pins_by_component.setdefault(connection["component_id"], set()).add(connection["pin_id"])

    floating_pins = []
    incomplete_components = []
    for component in components:
        component_id = component["id"]
        expected_pins = pin_count_lookup.get(component_id, 0)
        connected_pins = connected_pins_by_component.get(component_id, set())
        if expected_pins > 0 and len(connected_pins) < expected_pins:
            incomplete_components.append(component.get("refdes", component_id))

        for pin_group in pins:
            if pin_group["component_id"] != component_id:
                continue
            for pin in pin_group.get("pins", []):
                if pin["pin_id"] not in connected_pins:
                    floating_pins.append(pin["pin_id"])

    isolated_nets = [net.get("name", net["net_id"]) for net in nets if net.get("pin_count", 0) < 2]
    if floating_pins:
        warnings.append(f"Floating pins detected: {', '.join(sorted(floating_pins))}.")
    if incomplete_components:
        warnings.append(f"Components with missing connections: {', '.join(sorted(incomplete_components))}.")
    if isolated_nets:
        warnings.append(f"Isolated nets detected: {', '.join(sorted(isolated_nets))}.")

    consistency_score = 1.0
    if warnings:
        consistency_score -= min(0.5, 0.1 * len(warnings))
    if errors:
        consistency_score = min(consistency_score, 0.25)

    return {
        "warnings": warnings,
        "errors": errors,
        "consistency_score": round(max(0.0, consistency_score), 3),
        "needs_repair": bool(warnings or errors),
    }
