"""Evaluate Sketch2DXF agent advisor/apply outputs for one or many cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_workflow.eval_harness import run_multi_case_eval, run_single_case_eval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Sketch2DXF agent repair runs.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--case-dir", default=None, help="Evaluate one debug run directory.")
    target.add_argument("--cases-dir", default=None, help="Evaluate many debug run directories.")
    parser.add_argument("--pattern", default="*", help="Case directory glob pattern for --cases-dir.")
    parser.add_argument("--max-cases", type=int, default=None, help="Optional max case count in batch mode.")
    parser.add_argument("--advisor-dir", default=None, help="Single-case advisor output directory.")
    parser.add_argument("--advisor-report", default=None, help="Single-case advisor report JSON path.")
    parser.add_argument("--apply-dir", default=None, help="Single-case repair apply output directory.")
    parser.add_argument("--apply-report", default=None, help="Single-case repair replay report JSON path.")
    parser.add_argument("--output-dir", default=None, help="Output directory for eval artifacts.")
    parser.add_argument("--strategy-name", default="default", help="Strategy/model/parameter label for comparison.")
    parser.add_argument(
        "--llm-backend",
        choices=("rule", "mock", "openai", "deepseek", "custom"),
        default="rule",
        help="Optional LLM semantic evaluator backend. rule disables LLM calls.",
    )
    parser.add_argument("--model", default=None, help="LLM model name.")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL.")
    parser.add_argument("--api-key", default=None, help="API key. Prefer environment variables for normal use.")
    parser.add_argument("--api-key-env", default=None, help="Environment variable name containing the API key.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.case_dir:
        result = run_single_case_eval(
            args.case_dir,
            advisor_dir=args.advisor_dir,
            advisor_report=args.advisor_report,
            apply_dir=args.apply_dir,
            apply_report=args.apply_report,
            output_dir=args.output_dir,
            strategy_name=args.strategy_name,
            llm_backend=args.llm_backend,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
        )
        report = result["report"]
        print(
            json.dumps(
                {
                    "case_id": report.get("case_id"),
                    "schema_version": report.get("schema_version"),
                    "eval_status": report.get("eval_status"),
                    "strategy_name": report.get("strategy_name"),
                    "scores": report.get("scores"),
                    "repair_status": report.get("repair_effect", {}).get("status"),
                    "repair_improvement": report.get("repair_effect", {}).get("improvement"),
                    "semantic_eval": {
                        "used": report.get("semantic_eval", {}).get("used"),
                        "backend": report.get("semantic_eval", {}).get("backend"),
                        "verdict": report.get("semantic_eval", {}).get("semantic_verdict"),
                        "error": report.get("semantic_eval", {}).get("error"),
                    },
                    "outputs": result["outputs"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    result = run_multi_case_eval(
        args.cases_dir,
        pattern=args.pattern,
        output_dir=args.output_dir,
        strategy_name=args.strategy_name,
        llm_backend=args.llm_backend,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        max_cases=args.max_cases,
    )
    summary = result["summary"]
    print(
        json.dumps(
            {
                "schema_version": summary.get("schema_version"),
                "strategy_name": summary.get("strategy_name"),
                "total_cases": summary.get("total_cases"),
                "status_counts": summary.get("status_counts"),
                "average_scores": summary.get("average_scores"),
                "repair_applied_count": summary.get("repair_applied_count"),
                "repair_improved_count": summary.get("repair_improved_count"),
                "llm_semantic_eval_used_count": summary.get("llm_semantic_eval_used_count"),
                "outputs": result["outputs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
