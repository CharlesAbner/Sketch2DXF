"""Central project configuration for Sketch2DXF.

The geometry/topology pipeline is deliberately deterministic and parameterized.
The agent layer is LLM-assisted, but its loop limits and safe-tool behavior are
also configured here so experiments remain reproducible.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


DEFAULT_CONFIG = {
    "paths": {
        "data_dir": str(DATA_DIR),
        "outputs_dir": str(OUTPUTS_DIR),
    },
    "preprocess": {
        "adaptive_block_size": 31,
        "adaptive_c": 8,
        "median_ksize": 3,
        "morph_ksize": 3,
        "enable_deskew": False,
        "enable_skeleton": True,
    },
    "perception": {
        "min_component_area": 40,
        "hough_threshold": 15,
        "hough_min_line_length": 15,
        "hough_max_gap": 10,
        "wire_orientation_angle_thresh": 15.0,
        "wire_merge_axis_gap": 25,
        "wire_merge_endpoint_gap": 20,
        "wire_corner_gap": 20,
        "wire_corner_axis_slack": 20,
        "wire_corner_extension_gap": 20,
        "wire_segment_min_length": 20,
        "wire_bridge_margin": 20,
        "wire_support_axis_gap": 10,
        "wire_support_gap": 25,
        "wire_noise_near_component_margin": 18,
        "proposal_corner_max_corners": 200,
        "proposal_corner_quality_level": 0.01,
        "proposal_corner_min_distance": 3,
        "proposal_dilate_kernel_size": 25,
        "proposal_min_area": 800,
        "proposal_bbox_padding": 8,
    },
    "detector": {
        "proposal_backend": "traditional",
        "yolo_weights": str(
            PROJECT_ROOT
            / "detector"
            / "runs"
            / "train"
            / "cghd_power_detector"
            / "weights"
            / "best.pt"
        ),
        "yolo_imgsz": 1024,
        "yolo_conf": 0.25,
        "yolo_iou": 0.45,
        "yolo_duplicate_iou": 0.92,
        "yolo_device": "0",
    },
    "topology": {
        "pin_match_radius": 18,
        "wire_connect_radius": 8,
        "pin_axis_probe_margin": 18,
        "pin_endpoint_match_radius": 14,
        "pin_segment_match_radius": 18,
        "pin_node_match_radius": 24,
        "pin_axis_alignment_tolerance": 10,
        "pin_match_candidate_limit": 5,
        "pin_match_min_confidence": 0.05,
        "pin_corridor_enabled": True,
        "pin_corridor_length": 48,
        "pin_corridor_width": 18,
        "pin_corridor_backtrack": 4,
        "terminal_attachment_candidate_limit": 8,
        "supported_graph_min_best_attachment_score": 0.3,
        "supported_graph_min_candidate_attachment_score": 0.5,
        "supported_graph_candidate_score_margin": 0.15,
        "supported_graph_relay_min_supported_neighbors": 2,
        "node_terminal_support_min_confidence": 0.0,
        "node_bridge_gap": 32,
        "node_bridge_axis_tolerance": 10,
        "node_bridge_point_to_segment_gap": 14,
        "node_bridge_point_to_segment_axis_tolerance": 12,
        "use_graph_derived_nodes": True,
        "graph_nodes_enable_fallback": True,
        "graph_nodes_min_diff_match_ratio": 1.0,
        "graph_nodes_min_match_count_ratio": 1.0,
        "graph_nodes_fallback_on_repair": True,
    },
    "export": {
        "drawing_unit": "mm",
        "enable_layout_normalization": False,
        "dxf_output_name_mode": "input_stem",
        "netlist_output_name_mode": "input_stem",
    },
    "audit": {
        "low_confidence_match_threshold": 0.6,
        "weak_confidence_match_threshold": 0.75,
    },
    "repair": {
        "low_confidence_match_threshold": 0.6,
        "weak_confidence_match_threshold": 0.75,
        "ambiguous_match_confidence_margin": 0.1,
        "ambiguous_attachment_score_margin": 0.15,
        "weak_attachment_score_threshold": 0.7,
        "candidate_alternative_limit": 3,
        "unsupported_bridge_review_distance": 32,
    },
    "agent": {
        "enable_audit_agent": True,
        "enable_explanation_agent": True,
        "max_agent_tool_steps": 6,
        "max_tool_calls_per_step": 3,
    },
}


def get_default_config() -> dict:
    """Return a shallow copy of the default project config."""
    return {
        section: value.copy() if isinstance(value, dict) else value
        for section, value in DEFAULT_CONFIG.items()
    }
