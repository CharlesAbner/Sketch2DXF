"""
本文件的作用：
- 定义统一的 ProjectState 容器，作为 pipeline 的标准状态对象。
- 让输入、感知、拓扑、校验和导出都围绕同一份结构组织。
"""

from __future__ import annotations

from typing import Any


def create_project_state(image_path: str | None = None) -> dict[str, Any]:
    """Create a normalized ProjectState container."""
    return {
        "input": {
            "image_path": image_path,
            "image": None,
        },
        "preprocess": {},
        "perception": {
            "wire_segments": [],
            "wire_mask": None,
            "junctions": [],
            "endpoints": [],
            "proposals": [],
            "components": [],
        },
        "topology": {
            "components": [],
            "pins": [],
            "nodes": [],
            "wires": [],
            "connections": [],
            "nets": [],
            "component_nets": [],
            "netlist": {},
        },
        "validation": {
            "warnings": [],
            "errors": [],
            "consistency_score": None,
            "needs_repair": False,
            "audit": {},
            "explanation": {},
        },
        "export": {
            "overlay_path": None,
            "dxf_path": None,
            "json_path": None,
            "export_success": False,
            "export_errors": [],
        },
    }
