"""Experiment B: rule ablation and parameter perturbation on the final case set.

This experiment does not run the LLM Agent.  It measures how stable the
deterministic topology pipeline is under rule/parameter changes for the same
case set used by Experiment A.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_default_config
from src.pipeline import run_pipeline
from tools.generators.final_case_registry import final_cases, missing_generated_case_paths


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "experiments" / "experiment_b"


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


def _variants(base_config: dict) -> list[dict[str, Any]]:
    specs = [
        {
            "variant_id": "baseline",
            "category": "baseline",
            "description": "Current deterministic configuration.",
            "overrides": {},
        },
        {
            "variant_id": "ablate_graph_nodes_legacy",
            "category": "rule_ablation",
            "description": "Disable graph-derived nodes and force legacy node selection.",
            "overrides": {"topology.use_graph_derived_nodes": False},
        },
        {
            "variant_id": "ablate_terminal_corridor",
            "category": "rule_ablation",
            "description": "Collapse terminal corridor so attachments mostly depend on nearest projection.",
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
            "description": "Disable relay promotion of unsupported evidence between supported neighbors.",
            "overrides": {"topology.supported_graph_relay_min_supported_neighbors": 999},
        },
        {
            "variant_id": "ablate_bridge_candidates",
            "category": "rule_ablation",
            "description": "Disable bridge candidates between nearby raw evidence components.",
            "overrides": {"topology.evidence_graph_enable_bridge_candidates": False},
        },
        {
            "variant_id": "ablate_unsupported_filtering",
            "category": "rule_ablation",
            "description": "Force unsupported raw evidence into graph-derived node construction.",
            "overrides": {"topology.supported_graph_force_all_components_supported": True},
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
            "description": "Tighten representative thresholds by roughly 20-30%.",
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
            "description": "Loosen representative thresholds by roughly 20-30%.",
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
    return [{**spec, "config": _variant_config(base_config, spec["overrides"])} for spec in specs]


def _summary_metrics(state: dict[str, Any]) -> dict[str, Any]:
    case_summary = state.get("validation", {}).get("case_summary", {})
    summary = case_summary.get("summary", {})
    risk_counts = summary.get("risk_counts", {})
    repair_counts = summary.get("repair_candidate_counts", {})
    return {
        "review_status": case_summary.get("review_status"),
        "quality_label": summary.get("quality_label"),
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
        "risk_error_count": int(risk_counts.get("error", 0)),
        "risk_warning_count": int(risk_counts.get("warning", 0)),
        "risk_info_count": int(risk_counts.get("info", 0)),
        "repair_candidate_count": int(summary.get("repair_candidate_count", 0)),
        "repair_error_count": int(repair_counts.get("error", 0)),
        "repair_warning_count": int(repair_counts.get("warning", 0)),
    }


def _run_one(case: Any, variant: dict[str, Any], proposal_backend: str) -> dict[str, Any]:
    config = deepcopy(variant["config"])
    config["detector"]["proposal_backend"] = proposal_backend
    start = time.perf_counter()
    state = run_pipeline(str(case.image_path), config)
    elapsed_sec = round(time.perf_counter() - start, 3)
    return {
        "case_id": case.case_id,
        "group": case.group,
        "image_path": str(case.image_path),
        "variant_id": variant["variant_id"],
        "category": variant["category"],
        "elapsed_sec": elapsed_sec,
        **_summary_metrics(state),
    }


def _attach_baseline_diffs(rows: list[dict[str, Any]]) -> None:
    baselines = {row["case_id"]: row for row in rows if row["variant_id"] == "baseline"}
    compare_fields = [
        "review_status",
        "selected_node_source",
        "fallback_used",
        "export_success",
        "component_count",
        "pin_count",
        "net_count",
        "connection_count",
        "node_count",
    ]
    for row in rows:
        baseline = baselines.get(row["case_id"], {})
        changed_fields = [
            field for field in compare_fields if row.get(field) != baseline.get(field)
        ]
        row["changed_from_baseline"] = bool(changed_fields)
        row["changed_fields"] = changed_fields
        row["topology_counts_changed"] = any(
            field in changed_fields for field in ["component_count", "pin_count", "net_count", "connection_count"]
        )


def _variant_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for variant_id in sorted({row["variant_id"] for row in rows}):
        variant_rows = [row for row in rows if row["variant_id"] == variant_id]
        summaries.append(
            {
                "variant_id": variant_id,
                "category": variant_rows[0]["category"],
                "case_count": len(variant_rows),
                "changed_from_baseline_count": sum(1 for row in variant_rows if row["changed_from_baseline"]),
                "topology_counts_changed_count": sum(1 for row in variant_rows if row["topology_counts_changed"]),
                "export_failure_count": sum(1 for row in variant_rows if not row["export_success"]),
                "fallback_count": sum(1 for row in variant_rows if row["fallback_used"]),
                "risk_error_count": sum(row["risk_error_count"] for row in variant_rows),
                "risk_warning_count": sum(row["risk_warning_count"] for row in variant_rows),
            }
        )
    return summaries


def _case_stability(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    case_ids = sorted({row["case_id"] for row in rows})
    stability = []
    for case_id in case_ids:
        case_rows = [row for row in rows if row["case_id"] == case_id]
        baseline = next((row for row in case_rows if row["variant_id"] == "baseline"), {})
        stability.append(
            {
                "case_id": case_id,
                "group": baseline.get("group"),
                "baseline_review_status": baseline.get("review_status"),
                "baseline_counts": {
                    "components": baseline.get("component_count"),
                    "pins": baseline.get("pin_count"),
                    "nets": baseline.get("net_count"),
                    "connections": baseline.get("connection_count"),
                },
                "changed_variant_ids": [
                    row["variant_id"] for row in case_rows if row.get("changed_from_baseline")
                ],
                "topology_changed_variant_ids": [
                    row["variant_id"] for row in case_rows if row.get("topology_counts_changed")
                ],
            }
        )
    return stability


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "case_id",
        "group",
        "variant_id",
        "category",
        "changed_from_baseline",
        "topology_counts_changed",
        "changed_fields",
        "review_status",
        "quality_label",
        "selected_node_source",
        "fallback_used",
        "export_success",
        "component_count",
        "pin_count",
        "net_count",
        "connection_count",
        "node_count",
        "risk_error_count",
        "risk_warning_count",
        "repair_candidate_count",
        "elapsed_sec",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Experiment B Report: {report.get('run_id')}",
        "",
        f"- case count: `{report.get('case_count')}`",
        f"- variant count: `{report.get('variant_count')}`",
        f"- matrix rows: `{len(report.get('rows', []))}`",
        "",
        "## Variant Summary",
        "",
        "| variant | category | changed | topology changed | export failures |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for item in report.get("variant_summary", []):
        lines.append(
            "| "
            f"{item.get('variant_id')} | "
            f"{item.get('category')} | "
            f"{item.get('changed_from_baseline_count')} | "
            f"{item.get('topology_counts_changed_count')} | "
            f"{item.get('export_failure_count')} |"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Experiment B rule perturbation matrix.")
    parser.add_argument("--python-exe", default=sys.executable, help="Python executable for generation subprocess.")
    parser.add_argument("--run-tag", default=None, help="Optional run tag. Defaults to timestamp.")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--case-id", action="append", default=None, help="Run only selected case id(s).")
    parser.add_argument("--proposal-backend", choices=("traditional", "yolo"), default="yolo")
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Only print generation command; skip matrix execution.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.skip_generate and missing_generated_case_paths():
        command = [args.python_exe, "-B", "tools/generators/generate_cases.py", "--suite", "final"]
        print("$ " + " ".join(command))
        if not args.dry_run:
            completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)
            if completed.stdout.strip():
                print(completed.stdout.strip())
            if completed.stderr.strip():
                print(completed.stderr.strip(), file=sys.stderr)
            if completed.returncode != 0:
                raise SystemExit("case generation failed")

    tag = args.run_tag or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = f"experiment_b_{tag}"
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print(f"Dry run only. Planned cases: {[case.case_id for case in final_cases(args.case_id)]}")
        return

    base_config = get_default_config()
    variants = _variants(base_config)
    rows = []
    for case in final_cases(args.case_id):
        for variant in variants:
            print(f"Running {case.case_id} / {variant['variant_id']} ...")
            rows.append(_run_one(case, variant, args.proposal_backend))
    _attach_baseline_diffs(rows)

    report = {
        "schema_version": "experiment-b-rule-perturbation-v1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "proposal_backend": args.proposal_backend,
        "case_count": len(final_cases(args.case_id)),
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
        "outputs": {
            "json": str(output_dir / "experiment_b_report.json"),
            "markdown": str(output_dir / "experiment_b_report.md"),
            "csv": str(output_dir / "experiment_b_matrix.csv"),
        },
    }
    (output_dir / "experiment_b_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "experiment_b_report.md").write_text(_render_markdown(report), encoding="utf-8")
    _write_csv(output_dir / "experiment_b_matrix.csv", rows)
    print(json.dumps({"outputs": report["outputs"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
