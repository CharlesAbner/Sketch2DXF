"""
本文件的作用：
- 约定 export 层的返回字段，减少不同导出模块之间的隐式耦合。
"""

from __future__ import annotations


EXPORT_RESULT_SCHEMA = {
    "overlay_path": "str",
    "dxf_path": "str",
    "json_path": "str",  # exported netlist JSON path
    "export_success": "bool",
    "export_errors": "list[str]",
}
