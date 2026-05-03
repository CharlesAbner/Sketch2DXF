"""Apply a human-approved agent repair candidate and replay/export results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_workflow.repair_apply import run_human_approved_repair_apply


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply a human-approved Sketch2DXF repair candidate.")
    parser.add_argument("debug_dir", help="Path to the original debug run directory.")
    parser.add_argument(
        "--advisor-dir",
        default=None,
        help="Directory containing agent_repair_advisor_report.json.",
    )
    parser.add_argument(
        "--advisor-report",
        default=None,
        help="Explicit path to agent_repair_advisor_report.json.",
    )
    parser.add_argument(
        "--candidate-id",
        default=None,
        help="Candidate id to approve/apply. Defaults to the advisor-selected candidate.",
    )
    parser.add_argument(
        "--approval",
        choices=("pending", "accept", "reject"),
        default="pending",
        help="Human decision. pending only writes approval_request artifacts.",
    )
    parser.add_argument(
        "--approval-file",
        default=None,
        help="Optional approval_decision.json with decision/candidate_ids/approved_by/notes.",
    )
    parser.add_argument("--approved-by", default="manual_cli", help="Human approver name recorded in outputs.")
    parser.add_argument("--notes", default="", help="Human approval notes.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to <debug_dir>/repair_apply.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_human_approved_repair_apply(
        args.debug_dir,
        advisor_dir=args.advisor_dir,
        advisor_report=args.advisor_report,
        output_dir=args.output_dir,
        candidate_id=args.candidate_id,
        approval=args.approval,
        approval_file=args.approval_file,
        approved_by=args.approved_by,
        notes=args.notes,
    )
    report = result["report"]
    print(
        json.dumps(
            {
                "case_id": report.get("case_id"),
                "schema_version": report.get("schema_version"),
                "status": report.get("status"),
                "decision": report.get("approval_decision", {}).get("decision"),
                "candidate": report.get("candidate", {}).get("candidate_id"),
                "topology_mutated_in_place": report.get("topology_mutated_in_place"),
                "before_metrics": report.get("before_metrics"),
                "after_metrics": report.get("after_metrics"),
                "export_success": report.get("export", {}).get("export_success"),
                "outputs": result["outputs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
