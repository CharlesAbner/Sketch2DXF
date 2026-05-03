"""Production end-to-end pipeline entry point.

``run_pipeline`` executes the current 2.2 topology-recovery chain: image
preprocess, component proposals, wire evidence, evidence graph, terminal
attachments, supported graph, graph-derived nodes, topology/netlist, DXF, and
compact audit artifacts. Step-by-step visualization belongs in ``debug_run.py``.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from src.agent.audit_agent import generate_audit_report
from src.agent.explanation_agent import generate_explanation
from src.export.dxf_exporter import export_to_dxf
from src.export.overlay_renderer import render_overlay
from src.io_utils.image_io import load_image
from src.perception.component_classifier import classify_component_proposals
from src.perception.component_proposals import extract_component_proposals
from src.perception.junction_detect import detect_junctions_and_endpoints
from src.perception.perception_fusion import fuse_perception_results
from src.perception.wire_extract import extract_wires
from src.preprocess.preprocess import run_preprocess
from src.state import create_project_state
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


def _with_export_stem(config: dict, output_stem: str) -> dict:
    runtime_config = deepcopy(config)
    runtime_config.setdefault("export", {})
    runtime_config["export"]["output_stem"] = output_stem
    return runtime_config


def run_pipeline(image_path: str, config: dict) -> dict[str, Any]:
    """Run the full end-to-end pipeline for a single image path."""
    image_path_obj = Path(image_path)
    runtime_config = _with_export_stem(config, image_path_obj.stem)

    state = create_project_state(str(image_path_obj))
    image = load_image(image_path_obj)
    state["input"]["image"] = image

    preprocess_result = run_preprocess(image, runtime_config)
    proposal_result = extract_component_proposals(preprocess_result, runtime_config)
    wire_result = extract_wires(preprocess_result, proposal_result, runtime_config)
    junction_result = detect_junctions_and_endpoints(preprocess_result, wire_result, runtime_config)
    evidence_graph_result = build_evidence_graph(wire_result, junction_result, runtime_config)
    classification_result = classify_component_proposals(proposal_result, runtime_config)
    perception_result = fuse_perception_results(
        wire_result,
        junction_result,
        proposal_result,
        classification_result,
        runtime_config,
    )
    pin_result = locate_component_pins(perception_result, runtime_config)
    terminal_attachment_result = build_terminal_attachments(pin_result, evidence_graph_result, runtime_config)
    supported_graph_result = build_supported_graph(evidence_graph_result, terminal_attachment_result, runtime_config)
    legacy_raw_node_result = build_nodes(junction_result, wire_result, runtime_config)
    legacy_match_result = match_components_to_nodes(
        pin_result,
        legacy_raw_node_result,
        wire_result,
        junction_result,
        runtime_config,
    )
    legacy_node_result = filter_nodes_by_terminal_support(
        legacy_raw_node_result,
        legacy_match_result,
        runtime_config,
    )
    graph_nodes_dry_run_result = build_graph_nodes_dry_run(
        supported_graph_result,
        legacy_node_result,
        runtime_config,
    )
    selection_result = select_node_result_with_graph_fallback(
        graph_nodes_dry_run_result,
        legacy_node_result,
        legacy_match_result,
        perception_result,
        pin_result,
        wire_result,
        junction_result,
        runtime_config,
    )
    node_result = selection_result["node_result"]
    match_result = selection_result["match_result"]
    topology_result = selection_result["topology_result"]
    consistency_result = selection_result["consistency_result"]
    overlay_result = render_overlay(image, preprocess_result, perception_result, topology_result, runtime_config)
    export_result = export_to_dxf(topology_result, runtime_config)
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
        runtime_config,
    )
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
        runtime_config,
    )
    case_summary_result = build_case_summary(
        image_path_obj.stem,
        str(image_path_obj),
        audit_inputs_result,
        repair_candidates_result,
    )
    audit_result = generate_audit_report(topology_result, consistency_result, runtime_config)
    explanation_result = generate_explanation(topology_result, audit_result, runtime_config)

    state["preprocess"] = preprocess_result
    state["perception"] = perception_result
    state["topology"] = {
        **topology_result,
        "pins": pin_result["pins"],
        "nodes": node_result["nodes"],
        "connections": match_result["matches"],
        "nets": topology_result.get("nets", []),
        "component_nets": topology_result.get("component_nets", []),
        "netlist": topology_result.get("netlist", {}),
        "node_selection": selection_result["selection"],
        "graph_nodes_dry_run": graph_nodes_dry_run_result,
    }
    state["validation"] = {
        **consistency_result,
        "audit": audit_result,
        "audit_inputs": audit_inputs_result,
        "repair_candidates": repair_candidates_result,
        "case_summary": case_summary_result,
        "explanation": explanation_result,
    }
    state["export"] = {
        **export_result,
        "overlay_path": overlay_result.get("overlay_path"),
    }
    return state


def run_pipeline_from_array(image: Any, config: dict) -> dict[str, Any]:
    """
    Experimental helper for in-memory images.

    This function is intentionally incomplete and is not the main production
    entry point. It currently stops after proposal generation and is kept only
    for lightweight notebook / UI experiments.
    """
    state = create_project_state()
    state["input"]["image"] = image
    preprocess_result = run_preprocess(image, config)
    proposal_result = extract_component_proposals(preprocess_result, config)
    state["preprocess"] = preprocess_result
    state["perception"]["proposals"] = proposal_result.get("proposals", [])
    return state
