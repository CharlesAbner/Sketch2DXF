"""Run the agent advisor workflow used by the 3.7 agent suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_workflow import run_agent_repair_advisor_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Sketch2DXF LangGraph-native repair advisor used by the 3.7 agent suite."
    )
    parser.add_argument("debug_dir", help="Path to a debug run directory.")
    parser.add_argument(
        "--backend",
        "--llm-provider",
        dest="backend",
        choices=("rule", "mock", "openai", "deepseek", "custom"),
        default="rule",
        help="Planner/reviewer LLM provider. rule is fully offline.",
    )
    parser.add_argument(
        "--audit-backend",
        choices=("rule", "mock", "openai", "deepseek", "custom"),
        default="rule",
        help="Backend used only when an audit report must be generated or refreshed.",
    )
    parser.add_argument("--model", default=None, help="Optional model name for an LLM provider.")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Optional OpenAI-compatible API base URL.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional API key. Prefer environment variables; the key is not written to reports.",
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
        "--refresh-audit",
        action="store_true",
        help="Regenerate agent_audit_report.json before planning tool calls.",
    )
    parser.add_argument(
        "--max-agent-tool-steps",
        type=int,
        default=None,
        help="Maximum planner/tool loop iterations. Defaults to config value 6.",
    )
    parser.add_argument(
        "--max-tool-calls-per-step",
        type=int,
        default=None,
        help="Maximum tool calls the LLM may request in one planner iteration. Defaults to config value 3.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to debug_dir.",
    )
    parser.add_argument(
        "--memory-file",
        default=None,
        help="Optional failure_memory.json used as advisor context.",
    )
    parser.add_argument(
        "--memory-limit",
        type=int,
        default=5,
        help="Maximum failure memory patterns included in advisor observation.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_agent_repair_advisor_workflow(
        args.debug_dir,
        output_dir=args.output_dir,
        backend=args.backend,
        audit_backend=args.audit_backend,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        workflow_engine=args.workflow_engine,
        refresh_audit=args.refresh_audit,
        max_agent_tool_steps=args.max_agent_tool_steps,
        max_tool_calls_per_step=args.max_tool_calls_per_step,
        memory_file=args.memory_file,
        memory_limit=args.memory_limit,
    )
    report = result["report"]
    print(
        json.dumps(
            {
                "case_id": report.get("case_id"),
                "schema_version": report.get("schema_version"),
                "workflow_engine": report.get("workflow_engine"),
                "workflow_mode": report.get("workflow_mode"),
                "backend": report.get("backend"),
                "llm_used": report.get("llm_used"),
                "planner_kind": report.get("planner", {}).get("planner_kind"),
                "planner_stop_reason": report.get("planner", {}).get("stop_reason"),
                "planner_iteration_count": len(report.get("planner", {}).get("planner_steps", [])),
                "agent_loop_config": report.get("agent_loop_config"),
                "memory_matches": report.get("observation", {}).get("failure_memory", {}).get("matched_pattern_count"),
                "tool_calls": [
                    item.get("tool_name") for item in report.get("planner", {}).get("tool_calls", [])
                ],
                "tool_result_count": len(report.get("tool_results", [])),
                "critic_status": report.get("critic", {}).get("guardrail_status"),
                "critic_issue_count": len(report.get("critic", {}).get("issues", [])),
                "trace_step_count": len(report.get("agent_trace", [])),
                "trace_steps": [item.get("step") for item in report.get("agent_trace", [])],
                "final_decision": report.get("final_decision"),
                "repair_plan": report.get("repair_plan"),
                "topology_mutated": report.get("topology_mutated"),
                "outputs": result["outputs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
