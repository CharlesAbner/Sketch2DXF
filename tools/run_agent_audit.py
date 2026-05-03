"""Run the standalone artifact audit workflow for one debug-run directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_workflow import run_agent_audit_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Sketch2DXF standalone artifact audit workflow.")
    parser.add_argument("debug_dir", help="Path to a debug run directory.")
    parser.add_argument(
        "--backend",
        "--llm-provider",
        dest="backend",
        choices=("rule", "mock", "openai", "deepseek", "custom"),
        default="rule",
        help="LLM provider. rule is offline; openai/deepseek are presets; custom requires --base-url.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model name for an LLM provider.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Optional OpenAI-compatible API base URL. Required for --backend custom unless set by environment.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional API key. Prefer environment variables for real keys; the key is not written to reports.",
    )
    parser.add_argument(
        "--api-key-env",
        default=None,
        help="Optional environment variable name containing the API key.",
    )
    parser.add_argument(
        "--workflow-engine",
        choices=("auto", "local", "langgraph"),
        default="auto",
        help="Use LangGraph when installed, otherwise local sequential execution.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to debug_dir.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_agent_audit_workflow(
        args.debug_dir,
        output_dir=args.output_dir,
        backend=args.backend,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        workflow_engine=args.workflow_engine,
    )
    report = result["report"]
    print(json.dumps(
        {
            "case_id": report.get("case_id"),
            "overall_status": report.get("overall_status"),
            "primary_issue": report.get("primary_issue"),
            "suspected_stage": report.get("suspected_stage"),
            "confidence": report.get("confidence"),
            "workflow_engine": report.get("workflow_engine"),
            "llm_used": report.get("llm_used"),
            "outputs": result["outputs"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
