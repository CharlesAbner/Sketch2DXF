"""Probe rule sensitivity on generated hand-drawn stress cases.

The goal is not to replace the normal regression harness.  This script runs a
small matrix of config variants against generated test images and records how
stable the topology-level outputs remain when rules are ablated or parameters
are perturbed.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import OUTPUTS_DIR, get_default_config
from src.pipeline import run_pipeline


DEFAULT_MANIFEST = "data/generated/handdrawn_stress/manifest.json"


def _set_nested(config: dict, dotted_key: str, value: Any) -> None:
    current = config
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _variant_config(base_config: dict, overrides: dict[str, Any]) -> dict:
    config = deepcopy(base_config)
    for dotted_key, value in overrides.items():
        _set_nested(config, dotted_key, value)
    return config


def _variants(base_config: dict) -> list[dict]:
    specs = [
        {
            "variant_id": "baseline",
            "category": "baseline",
            "description": "Default 2.2 configuration.",
            "overrides": {},
        },
        {
            "variant_id": "ablate_graph_nodes_legacy",
            "category": "rule_ablation",
            "description": "Disable graph-derived nodes and force legacy node selection.",
            "overrides": {
                "topology.use_graph_derived_nodes": False,
            },
        },
        {
            "variant_id": "ablate_terminal_corridor",
            "category": "rule_ablation",
            "description": "Collapse terminal corridor so attachments mostly depend on nearby projection.",
            "overrides": {
                "topology.pin_corridor_length": 0,
                "topology.pin_corridor_width": 0,
                "topology.pin_corridor_backtrack": 0,
            },
        },
        {
            "variant_id": "ablate_candidate_support",
            "category": "rule_ablation",
            "description": "Disable non-best candidate terminal support in supported graph.",
            "overrides": {
                "topology.supported_graph_min_candidate_attachment_score": 1.1,
                "topology.supported_graph_candidate_score_margin": 0.0,
            },
        },
        {
            "variant_id": "ablate_relay_support",
            "category": "rule_ablation",
            "description": "Disable relay promotion of unsupported raw evidence between supported neighbors.",
            "overrides": {
                "topology.supported_graph_relay_min_supported_neighbors": 999,
            },
        },
        {
            "variant_id": "ablate_bridge_candidates",
            "category": "rule_ablation",
            "description": "Disable bridge candidates between nearby raw evidence components.",
            "overrides": {
                "topology.evidence_graph_enable_bridge_candidates": False,
            },
        },
        {
            "variant_id": "ablate_unsupported_filtering",
            "category": "rule_ablation",
            "description": "Force unsupported raw evidence into graph-derived node construction.",
            "overrides": {
                "topology.supported_graph_force_all_components_supported": True,
            },
        },
        {
            "variant_id": "ablate_corner_regularization",
            "category": "rule_ablation",
            "description": "Disable orthogonal corner snapping/extension in wire extraction.",
            "overrides": {
                "perception.wire_corner_gap": 0,
                "perception.wire_corner_axis_slack": 0,
                "perception.wire_corner_extension_gap": 0,
            },
        },
        {
            "variant_id": "perturb_tight_25",
            "category": "parameter_perturbation",
            "description": "Tighten representative topology thresholds by roughly 20-30%.",
            "overrides": {
                "topology.pin_corridor_length": 36,
                "topology.pin_corridor_width": 14,
                "topology.node_bridge_gap": 24,
                "topology.node_bridge_axis_tolerance": 8,
                "topology.node_bridge_point_to_segment_gap": 10,
                "topology.node_bridge_point_to_segment_axis_tolerance": 9,
                "topology.wire_connect_radius": 6,
                "topology.supported_graph_min_candidate_attachment_score": 0.6,
            },
        },
        {
            "variant_id": "perturb_loose_25",
            "category": "parameter_perturbation",
            "description": "Loosen representative topology thresholds by roughly 20-30%.",
            "overrides": {
                "topology.pin_corridor_length": 64,
                "topology.pin_corridor_width": 22,
                "topology.node_bridge_gap": 40,
                "topology.node_bridge_axis_tolerance": 12,
                "topology.node_bridge_point_to_segment_gap": 18,
                "topology.node_bridge_point_to_segment_axis_tolerance": 15,
                "topology.wire_connect_radius": 10,
                "topology.supported_graph_min_candidate_attachment_score": 0.4,
            },
        },
    ]
    return [
        {
            **spec,
            "config": _variant_config(base_config, spec["overrides"]),
        }
        for spec in specs
    ]


def _expected_counts(case: dict) -> dict:
    component_count = len(case.get("expected_components", []))
    expected_nets = case.get("expected_nets", [])
    pin_count = sum(len(component.get("pins", [])) for component in case.get("expected_components", []))
    connection_count = sum(len(net) for net in expected_nets)
    return {
        "expected_component_count": component_count,
        "expected_pin_count": pin_count,
        "expected_net_count": len(expected_nets),
        "expected_connection_count": connection_count,
    }


def _case_summary_metrics(case_summary: dict) -> dict:
    summary = case_summary.get("summary", {})
    risk_counts = summary.get("risk_counts", {})
    repair_counts = summary.get("repair_candidate_counts", {})
    return {
        "quality_label": summary.get("quality_label"),
        "review_status": case_summary.get("review_status"),
        "selected_node_source": summary.get("selected_node_source"),
        "fallback_used": bool(summary.get("fallback_used")),
        "consistency_score": float(summary.get("consistency_score", 0.0)),
        "needs_repair": bool(summary.get("needs_repair")),
        "export_success": bool(summary.get("export_success")),
        "component_count": int(summary.get("component_count", 0)),
        "pin_count": int(summary.get("pin_count", 0)),
        "connection_count": int(summary.get("connection_count", 0)),
        "node_count": int(summary.get("node_count", 0)),
        "net_count": int(summary.get("net_count", 0)),
        "raw_component_count": int(summary.get("raw_component_count", 0)),
        "supported_raw_component_count": int(summary.get("supported_raw_component_count", 0)),
        "unsupported_raw_component_count": int(summary.get("unsupported_raw_component_count", 0)),
        "relay_supported_raw_component_count": int(summary.get("relay_supported_raw_component_count", 0)),
        "risk_error_count": int(risk_counts.get("error", 0)),
        "risk_warning_count": int(risk_counts.get("warning", 0)),
        "risk_info_count": int(risk_counts.get("info", 0)),
        "repair_candidate_count": int(summary.get("repair_candidate_count", 0)),
        "repair_error_count": int(repair_counts.get("error", 0)),
        "repair_warning_count": int(repair_counts.get("warning", 0)),
    }


def _audit_metrics(audit_inputs: dict) -> dict:
    evidence_summary = audit_inputs.get("evidence_summary", {})
    wire_stats = evidence_summary.get("wire_stats", {})
    evidence_graph_stats = evidence_summary.get("evidence_graph_stats", {})
    supported_graph_stats = evidence_summary.get("supported_graph_stats", {})
    return {
        "wire_raw_segment_count": int(wire_stats.get("raw_segment_count", 0)),
        "wire_filtered_segment_count": int(wire_stats.get("filtered_segment_count", 0)),
        "wire_segment_count": int(wire_stats.get("segment_count", 0)),
        "wire_removed_near_component_noise_count": int(
            wire_stats.get("removed_near_component_noise_count", 0)
        ),
        "evidence_vertex_count": int(evidence_graph_stats.get("vertex_count", 0)),
        "evidence_edge_count": int(evidence_graph_stats.get("edge_count", 0)),
        "evidence_bridge_candidate_count": int(evidence_graph_stats.get("bridge_candidate_count", 0)),
        "support_link_count": int(supported_graph_stats.get("support_link_count", 0)),
        "best_support_link_count": int(supported_graph_stats.get("best_support_link_count", 0)),
        "candidate_support_link_count": int(supported_graph_stats.get("candidate_support_link_count", 0)),
        "supported_bridge_candidate_count": int(
            supported_graph_stats.get("supported_bridge_candidate_count", 0)
        ),
    }


def _semantic_checks(metrics: dict, expected: dict) -> dict:
    checks = {
        "components_match": metrics["component_count"] == expected["expected_component_count"],
        "pins_match": metrics["pin_count"] == expected["expected_pin_count"],
        "nets_match": metrics["net_count"] == expected["expected_net_count"],
        "connections_match": metrics["connection_count"] == expected["expected_connection_count"],
        "no_error_risks": metrics["risk_error_count"] == 0,
        "no_error_repairs": metrics["repair_error_count"] == 0,
        "graph_selected": metrics["selected_node_source"] == "graph_derived",
        "no_fallback": metrics["fallback_used"] is False,
        "export_success": metrics["export_success"] is True,
    }
    core_keys = [
        "components_match",
        "pins_match",
        "nets_match",
        "connections_match",
        "no_error_risks",
        "no_error_repairs",
        "export_success",
    ]
    checks["semantic_pass"] = all(checks[key] for key in core_keys)
    checks["silent_failure_risk"] = (
        not checks["semantic_pass"]
        and metrics["risk_error_count"] == 0
        and metrics["repair_error_count"] == 0
    )
    return checks


def _run_one(case: dict, variant: dict) -> dict:
    config = deepcopy(variant["config"])
    config["detector"]["proposal_backend"] = "yolo"
    start = time.perf_counter()
    state = run_pipeline(case["image_path"], config)
    elapsed_sec = round(time.perf_counter() - start, 3)
    case_summary = state["validation"]["case_summary"]
    audit_inputs = state["validation"].get("audit_inputs", {})
    metrics = _case_summary_metrics(case_summary)
    metrics.update(_audit_metrics(audit_inputs))
    expected = _expected_counts(case)
    checks = _semantic_checks(metrics, expected)
    return {
        "case_id": case["case_id"],
        "image_path": case["image_path"],
        "stressors": case.get("stressors", []),
        "variant_id": variant["variant_id"],
        "category": variant["category"],
        "elapsed_sec": elapsed_sec,
        **expected,
        **metrics,
        **checks,
    }


def _variant_summary(rows: list[dict]) -> list[dict]:
    summaries = []
    for variant_id in sorted({row["variant_id"] for row in rows}):
        variant_rows = [row for row in rows if row["variant_id"] == variant_id]
        summaries.append(
            {
                "variant_id": variant_id,
                "category": variant_rows[0]["category"],
                "case_count": len(variant_rows),
                "semantic_pass_count": sum(1 for row in variant_rows if row["semantic_pass"]),
                "semantic_fail_count": sum(1 for row in variant_rows if not row["semantic_pass"]),
                "fallback_count": sum(1 for row in variant_rows if row["fallback_used"]),
                "silent_failure_risk_count": sum(1 for row in variant_rows if row["silent_failure_risk"]),
                "total_warning_count": sum(row["risk_warning_count"] for row in variant_rows),
                "total_repair_candidate_count": sum(row["repair_candidate_count"] for row in variant_rows),
                "failed_case_ids": [
                    row["case_id"] for row in variant_rows if not row["semantic_pass"]
                ],
            }
        )
    return summaries


def _case_stability(rows: list[dict]) -> list[dict]:
    baseline_rows = {row["case_id"]: row for row in rows if row["variant_id"] == "baseline"}
    case_ids = sorted({row["case_id"] for row in rows})
    stability = []
    for case_id in case_ids:
        case_rows = [row for row in rows if row["case_id"] == case_id]
        baseline = baseline_rows.get(case_id, {})
        ablation_breaks = [
            row["variant_id"]
            for row in case_rows
            if row["category"] == "rule_ablation"
            and baseline.get("semantic_pass")
            and not row["semantic_pass"]
        ]
        perturbation_breaks = [
            row["variant_id"]
            for row in case_rows
            if row["category"] == "parameter_perturbation"
            and baseline.get("semantic_pass")
            and not row["semantic_pass"]
        ]
        stability.append(
            {
                "case_id": case_id,
                "stressors": case_rows[0].get("stressors", []),
                "baseline_semantic_pass": bool(baseline.get("semantic_pass")),
                "baseline_quality_label": baseline.get("quality_label"),
                "baseline_counts": {
                    "components": baseline.get("component_count"),
                    "pins": baseline.get("pin_count"),
                    "nets": baseline.get("net_count"),
                    "connections": baseline.get("connection_count"),
                    "warnings": baseline.get("risk_warning_count"),
                    "repairs": baseline.get("repair_candidate_count"),
                },
                "pass_count_all_variants": sum(1 for row in case_rows if row["semantic_pass"]),
                "variant_count": len(case_rows),
                "ablation_breaks": ablation_breaks,
                "perturbation_breaks": perturbation_breaks,
                "net_count_values": sorted({row["net_count"] for row in case_rows}),
                "component_count_values": sorted({row["component_count"] for row in case_rows}),
            }
        )
    return stability


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_id",
        "variant_id",
        "category",
        "semantic_pass",
        "silent_failure_risk",
        "quality_label",
        "selected_node_source",
        "fallback_used",
        "component_count",
        "expected_component_count",
        "pin_count",
        "expected_pin_count",
        "net_count",
        "expected_net_count",
        "connection_count",
        "expected_connection_count",
        "node_count",
        "raw_component_count",
        "supported_raw_component_count",
        "unsupported_raw_component_count",
        "wire_raw_segment_count",
        "wire_segment_count",
        "wire_removed_near_component_noise_count",
        "evidence_vertex_count",
        "evidence_edge_count",
        "evidence_bridge_candidate_count",
        "support_link_count",
        "candidate_support_link_count",
        "risk_error_count",
        "risk_warning_count",
        "repair_candidate_count",
        "elapsed_sec",
    ]
    with path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run generalization probes on generated hand-drawn cases.")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST, help="Path to generated-case manifest.")
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="1-based index of the first manifest case to run.",
    )
    parser.add_argument("--first-n", type=int, default=5, help="Number of manifest cases to run.")
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUTS_DIR / "generalization_probe"),
        help="Directory for JSON/CSV reports.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    start = max(0, args.start_index - 1)
    cases = manifest["cases"][start : start + args.first_n]

    base_config = get_default_config()
    variants = _variants(base_config)

    rows = []
    for case in cases:
        for variant in variants:
            print(f"Running {case['case_id']} / {variant['variant_id']} ...")
            rows.append(_run_one(case, variant))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "generalization-probe-v1",
        "manifest": args.manifest,
        "case_count": len(cases),
        "variant_count": len(variants),
        "variants": [
            {
                key: value
                for key, value in variant.items()
                if key in {"variant_id", "category", "description", "overrides"}
            }
            for variant in variants
        ],
        "variant_summary": _variant_summary(rows),
        "case_stability": _case_stability(rows),
        "rows": rows,
    }
    report_path = output_dir / "generalization_probe_report.json"
    csv_path = output_dir / "generalization_probe_matrix.csv"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(csv_path, rows)
    print(f"Report saved to: {report_path}")
    print(f"CSV saved to: {csv_path}")
    print(json.dumps({"variant_summary": report["variant_summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
