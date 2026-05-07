"""
本文件的作用：
- 用于单图逐阶段调试，并把关键中间产物保存到 debug 目录。
- 它和 `pipeline.py` 共享同一条核心主链路，只是额外负责阶段停止、可视化和 JSON 落盘。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.config import OUTPUTS_DIR, get_default_config
from src.export.dxf_exporter import export_to_dxf
from src.export.overlay_renderer import render_overlay
from src.io_utils.image_io import load_image, save_image
from src.io_utils.json_io import save_json
from src.perception.component_classifier import classify_component_proposals
from src.perception.component_proposals import extract_component_proposals
from src.perception.junction_detect import detect_junctions_and_endpoints
from src.perception.perception_fusion import fuse_perception_results
from src.perception.wire_extract import extract_wires
from src.preprocess.preprocess import run_preprocess
from src.topology.audit_inputs import build_audit_inputs
from src.topology.case_summary import build_case_summary
from src.topology.component_node_matcher import match_components_to_nodes
from src.topology.evidence_graph import build_evidence_graph
from src.topology.graph_node_dry_run import build_graph_nodes_dry_run
from src.topology.graph_node_selector import select_node_result_with_graph_fallback
from src.topology.node_builder import build_nodes, filter_nodes_by_terminal_support
from src.topology.pin_locator import locate_component_pins
from src.topology.repair_candidates import build_repair_candidates
from src.topology.supported_graph import build_supported_graph
from src.topology.terminal_attachment import build_terminal_attachments


STAGES = ("preprocess", "proposal", "wire", "junction", "node", "topology", "overlay", "all")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Debug Sketch2DXF step by step on a single image.")
    parser.add_argument("image_path", help="Path to an input image.")
    parser.add_argument(
        "--stage",
        choices=STAGES,
        default="all",
        help="Highest stage to execute. Later stages automatically include previous ones.",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="Optional custom output folder name. Defaults to the input file stem.",
    )
    parser.add_argument(
        "--proposal-backend",
        choices=("traditional", "yolo"),
        default=None,
        help="Override the configured component proposal backend for this debug run.",
    )
    parser.add_argument(
        "--debug-level",
        choices=("standard", "full"),
        default="standard",
        help="standard saves compact audit artifacts; full also saves heavy intermediate JSON/images.",
    )
    parser.add_argument(
        "--dxf-mode",
        choices=("clean", "debug"),
        default=None,
        help="DXF export style. clean hides internal pin/node labels; debug keeps them.",
    )
    return parser


def stage_rank(stage: str) -> int:
    order = {
        "preprocess": 1,
        "proposal": 2,
        "wire": 3,
        "junction": 4,
        "node": 5,
        "topology": 6,
        "overlay": 7,
        "all": 7,
    }
    return order[stage]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def array_to_u8(image: np.ndarray) -> np.ndarray:
    if image.dtype == np.uint8:
        return image
    clipped = np.clip(image, 0, 255)
    return clipped.astype(np.uint8)


def save_preprocess_outputs(base_dir: Path, preprocess_result: dict[str, Any]) -> None:
    save_image(base_dir / "01_gray.png", array_to_u8(preprocess_result["gray"]))
    save_image(base_dir / "02_binary.png", array_to_u8(preprocess_result["binary"]))
    save_image(base_dir / "03_clean.png", array_to_u8(preprocess_result["clean"]))
    save_image(base_dir / "04_deskewed.png", array_to_u8(preprocess_result["deskewed"]))
    save_image(base_dir / "05_skeleton.png", array_to_u8(preprocess_result["skeleton"]))
    save_json(
        base_dir / "preprocess_stats.json",
        {
            "angle": preprocess_result["angle"],
            "stats": preprocess_result["stats"],
        },
    )


def draw_segments(image: np.ndarray, wire_result: dict[str, Any]) -> np.ndarray:
    canvas = image.copy()
    for segment in wire_result["segments"]:
        color = (0, 255, 255) if segment.get("orientation") == "h" else (255, 255, 0)
        pt1 = (int(segment["x1"]), int(segment["y1"]))
        pt2 = (int(segment["x2"]), int(segment["y2"]))
        cv2.line(canvas, pt1, pt2, color, 2, lineType=cv2.LINE_AA)
    return canvas


def draw_proposals(image: np.ndarray, proposal_result: dict[str, Any]) -> np.ndarray:
    canvas = image.copy()
    for proposal in proposal_result["proposals"]:
        x1, y1, x2, y2 = [int(v) for v in proposal["bbox"]]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 165, 255), 2)
        label = proposal.get("class_name", proposal["id"])
        score = proposal.get("score")
        if score is not None:
            label = f"{label} {float(score):.2f}"
        cv2.putText(
            canvas,
            label,
            (x1, max(12, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 165, 255),
            1,
            lineType=cv2.LINE_AA,
        )
    return canvas


def draw_points(image: np.ndarray, points: list[dict[str, Any]], color: tuple[int, int, int]) -> np.ndarray:
    canvas = image.copy()
    for point in points:
        x = int(point["x"])
        y = int(point["y"])
        cv2.circle(canvas, (x, y), 4, color, -1, lineType=cv2.LINE_AA)
        label = point.get("node_id", point.get("id", ""))
        if label:
            cv2.putText(
                canvas,
                label,
                (x + 4, y - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                color,
                1,
                lineType=cv2.LINE_AA,
            )
    return canvas


def draw_nodes(image: np.ndarray, node_result: dict[str, Any]) -> np.ndarray:
    return draw_points(image, node_result["nodes"], (255, 0, 0))


def draw_node_support(image: np.ndarray, node_result: dict[str, Any]) -> np.ndarray:
    canvas = image.copy()
    for node in node_result.get("discarded_nodes", []):
        x = int(node["x"])
        y = int(node["y"])
        cv2.circle(canvas, (x, y), 4, (160, 160, 160), -1, lineType=cv2.LINE_AA)
        cv2.putText(
            canvas,
            f"{node.get('node_id', '')}:discard",
            (x + 4, y - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            (160, 160, 160),
            1,
            lineType=cv2.LINE_AA,
        )
    for node in node_result.get("nodes", []):
        x = int(node["x"])
        y = int(node["y"])
        cv2.circle(canvas, (x, y), 5, (255, 0, 0), -1, lineType=cv2.LINE_AA)
        label = f"{node.get('node_id', '')}:pins={node.get('terminal_support_count', 0)}"
        cv2.putText(
            canvas,
            label,
            (x + 5, y - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            (255, 0, 0),
            1,
            lineType=cv2.LINE_AA,
        )
    return canvas


def serialize_proposals(proposal_result: dict[str, Any]) -> dict[str, Any]:
    proposals = []
    for proposal in proposal_result["proposals"]:
        proposals.append({key: value for key, value in proposal.items() if key != "patch"})
    return {
        "stats": proposal_result.get("stats", {}),
        "proposals": proposals,
    }


def serialize_preprocess(preprocess_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "angle": preprocess_result["angle"],
        "stats": preprocess_result["stats"],
        "shapes": {
            "gray": list(preprocess_result["gray"].shape),
            "binary": list(preprocess_result["binary"].shape),
            "clean": list(preprocess_result["clean"].shape),
            "skeleton": list(preprocess_result["skeleton"].shape),
        },
    }


def serialize_wire_evidence(wire_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "stats": wire_result.get("stats", {}),
        "raw_segments": wire_result.get("raw_segments", []),
        "filtered_segments": wire_result.get("filtered_segments", []),
        "segments": wire_result.get("segments", []),
    }


def main() -> None:
    args = build_parser().parse_args()
    config = get_default_config()
    if args.proposal_backend is not None:
        config["detector"]["proposal_backend"] = args.proposal_backend

    image_path = Path(args.image_path)
    run_name = args.run_name or image_path.stem
    out_dir = ensure_dir(OUTPUTS_DIR / "debug_runs" / run_name)
    config.setdefault("export", {})
    config["export"]["output_stem"] = run_name
    if args.dxf_mode is not None:
        config["export"]["dxf_mode"] = args.dxf_mode

    image = load_image(image_path)
    save_image(out_dir / "00_input.png", image)
    target_rank = stage_rank(args.stage)
    full_debug = args.debug_level == "full"

    preprocess_result = run_preprocess(image, config)
    if full_debug or target_rank == stage_rank("preprocess"):
        save_preprocess_outputs(out_dir, preprocess_result)
        save_json(out_dir / "preprocess.json", serialize_preprocess(preprocess_result))
    if target_rank == 1:
        print(f"Finished preprocess. Outputs saved to: {out_dir}")
        return

    proposal_result = extract_component_proposals(preprocess_result, config)
    save_image(out_dir / "06_proposals.png", draw_proposals(image, proposal_result))
    save_json(out_dir / "proposals.json", serialize_proposals(proposal_result))
    if target_rank == 2:
        print(f"Finished proposal stage. Outputs saved to: {out_dir}")
        return

    wire_result = extract_wires(preprocess_result, proposal_result, config)
    if full_debug:
        save_image(out_dir / "07_connections_only.png", array_to_u8(wire_result["connections_only"]))
        save_image(out_dir / "08_wire_mask.png", array_to_u8(wire_result["wire_mask"]))
        save_json(out_dir / "wire.json", wire_result)
    save_image(out_dir / "09_wire_segments.png", draw_segments(image, wire_result))
    save_json(out_dir / "wire_evidence.json", serialize_wire_evidence(wire_result))
    if target_rank == 3:
        print(f"Finished wire stage. Outputs saved to: {out_dir}")
        return

    junction_result = detect_junctions_and_endpoints(preprocess_result, wire_result, config)
    evidence_graph_result = build_evidence_graph(wire_result, junction_result, config)
    save_json(out_dir / "evidence_graph.json", evidence_graph_result)
    if full_debug or target_rank == stage_rank("junction"):
        save_image(out_dir / "10_endpoints.png", draw_points(image, junction_result["endpoints"], (0, 255, 0)))
        save_image(out_dir / "11_junctions.png", draw_points(image, junction_result["junctions"], (0, 0, 255)))
        save_json(out_dir / "junctions.json", junction_result)
    if target_rank == 4:
        print(f"Finished junction stage. Outputs saved to: {out_dir}")
        return

    node_result = build_nodes(junction_result, wire_result, config)
    if full_debug or target_rank == stage_rank("node"):
        save_image(out_dir / "12_nodes.png", draw_nodes(image, node_result))
        save_json(out_dir / "nodes.json", node_result)
    if target_rank == 5:
        print(f"Finished node stage. Outputs saved to: {out_dir}")
        return

    classification_result = classify_component_proposals(proposal_result, config)
    perception_result = fuse_perception_results(
        wire_result,
        junction_result,
        proposal_result,
        classification_result,
        config,
    )
    pin_result = locate_component_pins(perception_result, config)
    terminal_attachment_result = build_terminal_attachments(pin_result, evidence_graph_result, config)
    save_json(out_dir / "terminal_attachments.json", terminal_attachment_result)
    supported_graph_result = build_supported_graph(evidence_graph_result, terminal_attachment_result, config)
    save_json(out_dir / "supported_graph.json", supported_graph_result)
    legacy_match_result = match_components_to_nodes(pin_result, node_result, wire_result, junction_result, config)
    legacy_node_result = filter_nodes_by_terminal_support(node_result, legacy_match_result, config)
    graph_nodes_dry_run_result = build_graph_nodes_dry_run(supported_graph_result, legacy_node_result, config)
    save_json(out_dir / "graph_nodes_dry_run.json", graph_nodes_dry_run_result)
    selection_result = select_node_result_with_graph_fallback(
        graph_nodes_dry_run_result,
        legacy_node_result,
        legacy_match_result,
        perception_result,
        pin_result,
        wire_result,
        junction_result,
        config,
    )
    node_result = selection_result["node_result"]
    match_result = selection_result["match_result"]
    topology_result = selection_result["topology_result"]
    consistency_result = selection_result["consistency_result"]
    save_json(out_dir / "node_selection.json", selection_result["selection"])
    export_result = export_to_dxf(topology_result, config)
    audit_inputs_result = build_audit_inputs(
        proposal_result,
        pin_result,
        wire_result,
        evidence_graph_result,
        terminal_attachment_result,
        supported_graph_result,
        graph_nodes_dry_run_result,
        selection_result["selection"],
        node_result,
        match_result,
        topology_result,
        consistency_result,
        export_result,
        config,
    )
    save_json(out_dir / "audit_inputs.json", audit_inputs_result)
    repair_candidates_result = build_repair_candidates(
        pin_result,
        terminal_attachment_result,
        supported_graph_result,
        graph_nodes_dry_run_result,
        selection_result["selection"],
        node_result,
        match_result,
        topology_result,
        consistency_result,
        config,
    )
    save_json(out_dir / "repair_candidates.json", repair_candidates_result)
    case_summary_result = build_case_summary(
        run_name,
        str(image_path),
        audit_inputs_result,
        repair_candidates_result,
        debug_run_name=run_name,
    )
    save_json(out_dir / "case_summary.json", case_summary_result)

    save_json(out_dir / "matches.json", match_result)
    if full_debug:
        save_json(
            out_dir / "nodes_raw.json",
            {
                "nodes": node_result.get("raw_nodes", []),
                "stats": node_result.get("stats", {}),
            },
        )
    save_json(out_dir / "nodes.json", node_result)
    save_image(out_dir / "12_active_nodes.png", draw_node_support(image, node_result))
    save_json(out_dir / "topology.json", topology_result)
    if full_debug:
        save_json(
            out_dir / "nets.json",
            {
                "nets": topology_result.get("nets", []),
                "component_nets": topology_result.get("component_nets", []),
            },
        )
    save_json(out_dir / "netlist.json", topology_result.get("netlist", {}))
    save_json(out_dir / "validation.json", consistency_result)
    save_json(out_dir / "export.json", export_result)
    dxf_path = export_result.get("dxf_path")
    json_path = export_result.get("json_path")
    if dxf_path:
        dxf_file = Path(dxf_path)
        if dxf_file.exists():
            (out_dir / "14_export.dxf").write_bytes(dxf_file.read_bytes())
    if json_path:
        exported_netlist = Path(json_path)
        if full_debug and exported_netlist.exists():
            (out_dir / "15_export_netlist.json").write_bytes(exported_netlist.read_bytes())
    if target_rank == 6:
        print(f"Finished topology stage. Outputs saved to: {out_dir}")
        return

    overlay_result = render_overlay(image, preprocess_result, perception_result, topology_result, config)
    overlay_path = Path(overlay_result["overlay_path"])
    if overlay_path.exists():
        overlay_copy = out_dir / "13_overlay.png"
        overlay_copy.write_bytes(overlay_path.read_bytes())
    print(f"Finished {args.debug_level} debug run. Outputs saved to: {out_dir}")


if __name__ == "__main__":
    main()
