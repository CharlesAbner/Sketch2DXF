"""Build or query Sketch2DXF failure memory from agent eval reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_workflow.failure_memory import (
    MEMORY_OUTPUT_JSON,
    collect_eval_reports,
    query_failure_memory,
    render_memory_query_markdown,
    summarize_memory,
    update_failure_memory_from_eval_reports,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update/query Sketch2DXF failure memory.")
    parser.add_argument(
        "command",
        choices=("update", "query", "summary"),
        help="update from eval artifacts, query for a case, or print summary.",
    )
    parser.add_argument(
        "--memory-file",
        default=str(Path("outputs") / "agent_memory" / MEMORY_OUTPUT_JSON),
        help="Memory JSON path.",
    )
    parser.add_argument("--eval-report", default=None, help="Single agent_eval_report.json.")
    parser.add_argument("--eval-summary", default=None, help="agent_eval_summary.json to ingest.")
    parser.add_argument("--eval-reports-dir", default=None, help="Recursive directory containing eval reports.")
    parser.add_argument("--debug-dir", default=None, help="Debug run directory for memory query.")
    parser.add_argument("--limit", type=int, default=5, help="Max patterns returned by query.")
    parser.add_argument("--output-dir", default=None, help="Optional query output directory.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    memory_file = Path(args.memory_file)

    if args.command == "update":
        reports = collect_eval_reports(
            eval_report=args.eval_report,
            eval_summary=args.eval_summary,
            eval_reports_dir=args.eval_reports_dir,
        )
        result = update_failure_memory_from_eval_reports(
            reports,
            memory_file=memory_file,
            output_file=memory_file,
        )
        print(
            json.dumps(
                {
                    "schema_version": result["memory"].get("schema_version"),
                    "ingested_report_count": len(reports),
                    "pattern_count": result["memory"].get("pattern_count"),
                    "case_count": len(result["memory"].get("case_index", {})),
                    "outputs": result["outputs"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "query":
        query = query_failure_memory(
            memory_file,
            debug_dir=args.debug_dir,
            limit=args.limit,
        )
        outputs = {}
        if args.output_dir:
            out_dir = Path(args.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            json_path = out_dir / "failure_memory_query.json"
            md_path = out_dir / "failure_memory_query.md"
            json_path.write_text(json.dumps(query, ensure_ascii=False, indent=2), encoding="utf-8")
            md_path.write_text(render_memory_query_markdown(query), encoding="utf-8")
            outputs = {"query": str(json_path), "query_markdown": str(md_path)}
        print(
            json.dumps(
                {
                    "memory_file": str(memory_file),
                    "exists": query.get("exists"),
                    "pattern_count": query.get("pattern_count"),
                    "matched_pattern_count": query.get("matched_pattern_count"),
                    "case_signals": query.get("case_signals"),
                    "matched_patterns": query.get("matched_patterns"),
                    "outputs": outputs,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print(json.dumps(summarize_memory(memory_file), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
