"""Run agent repair dry-run candidate generation for one debug-run directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_workflow.repair_dry_run import run_agent_repair_dry_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Sketch2DXF agent repair dry-run candidates.")
    parser.add_argument("debug_dir", help="Path to a debug run directory.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to debug_dir.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_agent_repair_dry_run(args.debug_dir, output_dir=args.output_dir)
    report = result["report"]
    print(
        json.dumps(
            {
                "case_id": report.get("case_id"),
                "schema_version": report.get("schema_version"),
                "source_agent_audit_exists": report.get("source_agent_audit_report", {}).get("exists"),
                "selected_repair_tools": [
                    item.get("tool_id") for item in report.get("selected_repair_tools", [])
                ],
                "candidate_count": report.get("validation_summary", {}).get("candidate_count"),
                "top_candidates": [
                    {
                        "candidate_id": item.get("candidate_id"),
                        "repair_type": item.get("repair_type"),
                        "score": item.get("score"),
                        "ranking_score": item.get("ranking_score"),
                        "validation_result": item.get("validation_result"),
                        "recommendation": item.get("recommendation"),
                    }
                    for item in report.get("repair_candidates", [])[:3]
                ],
                "topology_mutated": report.get("topology_mutated"),
                "recommended_next_step": report.get("recommended_next_step"),
                "outputs": result["outputs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
