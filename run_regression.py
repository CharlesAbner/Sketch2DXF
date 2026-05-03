"""Run a compact regression harness over known Sketch2DXF cases."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.config import OUTPUTS_DIR, get_default_config
from src.pipeline import run_pipeline
from src.topology.case_summary import build_case_summary


DEFAULT_CASES = [
    ("001_series_loop", "data/samples_easy/001_series_loop.png"),
    ("003_parallel_branches", "data/samples_easy/003_parallel_branches.png"),
    ("004_china_R", "data/samples_easy/004_china_R.jpg"),
    ("005_min", "data/samples_easy/005_min.png"),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Sketch2DXF regression cases.")
    parser.add_argument(
        "--proposal-backend",
        choices=("traditional", "yolo"),
        default="yolo",
        help="Proposal backend used for all cases.",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUTS_DIR / "regression" / "regression_report.json"),
        help="Path to the regression report JSON.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Optional case override in the form case_id=path. May be repeated.",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit with code 1 when any case violates regression expectations.",
    )
    return parser


def _case_specs(case_args: list[str]) -> list[tuple[str, str]]:
    if not case_args:
        return DEFAULT_CASES
    specs = []
    for item in case_args:
        if "=" not in item:
            raise ValueError(f"Invalid --case value: {item}. Expected case_id=path.")
        case_id, path = item.split("=", 1)
        specs.append((case_id.strip(), path.strip()))
    return specs


def _expectation_results(case_summary: dict) -> list[dict]:
    summary = case_summary.get("summary", {})
    checks = [
        (
            "selected_graph_derived",
            summary.get("selected_node_source") == "graph_derived",
            summary.get("selected_node_source"),
        ),
        ("no_fallback", summary.get("fallback_used") is False, summary.get("fallback_used")),
        ("export_success", summary.get("export_success") is True, summary.get("export_success")),
        (
            "consistency_score_at_least_1_0",
            float(summary.get("consistency_score", 0.0)) >= 1.0,
            summary.get("consistency_score"),
        ),
        (
            "no_error_risks",
            int(summary.get("risk_counts", {}).get("error", 0)) == 0,
            summary.get("risk_counts", {}).get("error", 0),
        ),
        (
            "no_error_repair_candidates",
            int(summary.get("repair_candidate_counts", {}).get("error", 0)) == 0,
            summary.get("repair_candidate_counts", {}).get("error", 0),
        ),
        (
            "repair_did_not_mutate_topology",
            case_summary.get("topology_mutated_by_repair") is False,
            case_summary.get("topology_mutated_by_repair"),
        ),
    ]
    return [
        {
            "name": name,
            "passed": bool(passed),
            "observed": observed,
        }
        for name, passed, observed in checks
    ]


def _regression_status(expectations: list[dict], case_summary: dict) -> str:
    if any(not item["passed"] for item in expectations):
        return "fail"
    if case_summary.get("review_status") == "needs_review":
        return "pass_with_warnings"
    return case_summary.get("review_status", "pass")


def _run_case(case_id: str, image_path: str, proposal_backend: str) -> dict:
    config = get_default_config()
    config["detector"]["proposal_backend"] = proposal_backend
    start = time.perf_counter()
    state = run_pipeline(image_path, config)
    elapsed_sec = round(time.perf_counter() - start, 3)
    audit_inputs = state["validation"]["audit_inputs"]
    repair_candidates = state["validation"]["repair_candidates"]
    case_summary = state["validation"].get("case_summary")
    if case_summary is None:
        case_summary = build_case_summary(
            case_id,
            image_path,
            audit_inputs,
            repair_candidates,
        )
    expectations = _expectation_results(case_summary)
    return {
        "case_id": case_id,
        "image_path": image_path,
        "elapsed_sec": elapsed_sec,
        "regression_status": _regression_status(expectations, case_summary),
        "expectations": expectations,
        "case_summary": case_summary,
    }


def _report_summary(case_results: list[dict]) -> dict:
    statuses = [case["regression_status"] for case in case_results]
    failed_cases = [case["case_id"] for case in case_results if case["regression_status"] == "fail"]
    warning_cases = [
        case["case_id"]
        for case in case_results
        if case["regression_status"] == "pass_with_warnings"
    ]
    total_candidates = sum(
        int(case["case_summary"]["summary"].get("repair_candidate_count", 0))
        for case in case_results
    )
    total_warning_candidates = sum(
        int(case["case_summary"]["summary"].get("repair_candidate_counts", {}).get("warning", 0))
        for case in case_results
    )
    total_error_candidates = sum(
        int(case["case_summary"]["summary"].get("repair_candidate_counts", {}).get("error", 0))
        for case in case_results
    )
    return {
        "case_count": len(case_results),
        "pass_count": sum(1 for status in statuses if status == "pass"),
        "pass_with_warnings_count": sum(1 for status in statuses if status == "pass_with_warnings"),
        "fail_count": sum(1 for status in statuses if status == "fail"),
        "failed_case_ids": failed_cases,
        "warning_case_ids": warning_cases,
        "total_repair_candidate_count": total_candidates,
        "total_warning_repair_candidate_count": total_warning_candidates,
        "total_error_repair_candidate_count": total_error_candidates,
    }


def main() -> None:
    args = build_parser().parse_args()
    case_results = [
        _run_case(case_id, image_path, args.proposal_backend)
        for case_id, image_path in _case_specs(args.case)
    ]
    report = {
        "schema_version": "2.2-regression-report",
        "proposal_backend": args.proposal_backend,
        "summary": _report_summary(case_results),
        "cases": case_results,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Regression report saved to: {output_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.fail_on_regression and report["summary"]["fail_count"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
