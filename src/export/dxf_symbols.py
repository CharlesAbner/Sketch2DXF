"""Map recovered component classes to DXF drawing primitives."""

from __future__ import annotations


def get_dxf_symbol_definition(class_name: str) -> dict:
    """Return a minimal DXF symbol descriptor for a given component class."""
    symbol_map = {
        "resistor": {"class_name": class_name, "primitive": "resistor"},
        "capacitor.unpolarized": {"class_name": class_name, "primitive": "capacitor"},
        "power_source": {"class_name": class_name, "primitive": "power_source"},
        "voltage.ac": {"class_name": class_name, "primitive": "power_source"},
        "voltage.dc": {"class_name": class_name, "primitive": "power_source"},
        "voltage.battery": {"class_name": class_name, "primitive": "power_source"},
    }
    return symbol_map.get(class_name, {"class_name": class_name, "primitive": "box"})

