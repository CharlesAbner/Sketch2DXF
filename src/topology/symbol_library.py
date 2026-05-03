"""
本文件的作用：
- 定义元件类别、pin 数量、相对位置等结构知识。
- 它是 pin 定位、连接规则和一致性检查的领域知识基础。

建议说明：
- 先只支持少数几类元件，把规则写清楚。
- 后续扩类别时，尽量只改这个文件和少量调用逻辑。
"""

from __future__ import annotations


SYMBOL_LIBRARY = {
    "resistor": {"pin_count": 2, "pin_layout": "horizontal_pair"},
    "capacitor": {"pin_count": 2, "pin_layout": "horizontal_pair"},
    "voltage_source": {"pin_count": 2, "pin_layout": "horizontal_pair"},
    "ground": {"pin_count": 1, "pin_layout": "top_single"},
    "unknown": {"pin_count": 0, "pin_layout": "none"},
}


def get_symbol_definition(class_name: str) -> dict:
    """Fetch symbol metadata for a component class."""
    return SYMBOL_LIBRARY.get(class_name, SYMBOL_LIBRARY["unknown"])
