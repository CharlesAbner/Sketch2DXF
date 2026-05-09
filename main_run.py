"""One-command Sketch2DXF run: debug artifacts -> Agent advisor -> human approval -> apply -> eval.

This is the human-facing entry point for demos.  It deliberately reuses the
core workflow functions instead of exposing many low-level tool scripts:

1. debug_run.py builds deterministic topology artifacts.
2. The Agent advisor inspects artifacts and dry-runs repairs.
3. The user reviews the generated report and types "yes".
4. The approved repair_plan is applied and replayed.
5. The advisor/apply result is evaluated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agent_workflow import run_agent_repair_advisor_workflow
from src.agent_workflow.eval_harness import run_single_case_eval
from src.agent_workflow.repair_apply import run_human_approved_repair_apply


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "debug_runs"


def _default_api_key_env(backend: str) -> str | None:
    if backend == "deepseek":
        return "DEEPSEEK_API_KEY"
    if backend in {"openai", "custom"}:
        return "OPENAI_API_KEY"
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _redact_command(command: list[str], secret_values: set[str]) -> list[str]:
    redacted = []
    for item in command:
        redacted.append("<redacted>" if item in secret_values else item)
    return redacted


def _run_command(command: list[str], env: dict[str, str], dry_run: bool, secrets: set[str]) -> dict[str, Any]:
    safe_command = _redact_command(command, secrets)
    print("\n$ " + " ".join(safe_command))
    if dry_run:
        return {"returncode": 0, "stdout": "", "stderr": "", "dry_run": True, "command": safe_command}
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "dry_run": False,
        "command": safe_command,
    }


def _require_success(step: str, result: dict[str, Any]) -> None:
    if int(result.get("returncode", 1)) != 0:
        raise SystemExit(f"{step} failed with code {result.get('returncode')}.")


def _validate_api_key(args: argparse.Namespace, env: dict[str, str]) -> None:
    if args.backend in {"rule", "mock"}:
        return
    if args.api_key:
        return
    if args.api_key_env and env.get(args.api_key_env):
        return
    raise SystemExit(
        f"No API key found for backend '{args.backend}'. "
        f"Set ${args.api_key_env} or pass --api-key."
    )


def _has_repair_plan(advisor_report: dict[str, Any] | None) -> bool:
    if not isinstance(advisor_report, dict):
        return False
    steps = advisor_report.get("repair_plan", {}).get("steps", [])
    return any(isinstance(step, dict) and step.get("candidate_id") for step in steps)


def _print_review_paths(debug_dir: Path, advisor_dir: Path, advisor_report: dict[str, Any] | None) -> None:
    print("\nAgent advisor finished. Please review these files:")
    print(f"- Advisor report: {advisor_dir / 'agent_repair_advisor_report.md'}")
    print(f"- Human review dossier: {advisor_dir / 'agent_human_review_dossier.md'}")
    print(f"- Overlay image: {debug_dir / '13_overlay.png'}")
    print(f"- Current DXF: {debug_dir / '14_export.dxf'}")
    if advisor_report:
        plan = advisor_report.get("repair_plan", {})
        print(f"- Repair plan: {plan.get('plan_id')} status={plan.get('status')}")
        for step in plan.get("steps", []) or []:
            if isinstance(step, dict):
                print(
                    "  "
                    f"* {step.get('step_id')}: {step.get('candidate_id')} "
                    f"({step.get('repair_type')})"
                )


def _print_advisor_summary(result: dict[str, Any]) -> None:
    report = result.get("report", {})
    print(
        json.dumps(
            {
                "case_id": report.get("case_id"),
                "workflow_engine": report.get("workflow_engine"),
                "backend": report.get("backend"),
                "llm_used": report.get("llm_used"),
                "planner_stop_reason": report.get("planner", {}).get("stop_reason"),
                "tool_result_count": len(report.get("tool_results", [])),
                "final_decision": report.get("final_decision"),
                "repair_plan": report.get("repair_plan"),
                "outputs": result.get("outputs"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _print_apply_summary(result: dict[str, Any]) -> None:
    report = result.get("report", {})
    print(
        json.dumps(
            {
                "case_id": report.get("case_id"),
                "status": report.get("status"),
                "decision": report.get("approval_decision", {}).get("decision"),
                "repair_plan": report.get("repair_plan"),
                "applied_candidates": [
                    item.get("candidate_id") for item in report.get("applied_candidates", [])
                ],
                "export_success": report.get("export", {}).get("export_success"),
                "outputs": result.get("outputs"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _print_eval_summary(result: dict[str, Any]) -> None:
    report = result.get("report", {})
    print(
        json.dumps(
            {
                "case_id": report.get("case_id"),
                "eval_status": report.get("eval_status"),
                "scores": report.get("scores"),
                "repair_status": report.get("repair_effect", {}).get("status"),
                "semantic_eval": {
                    "used": report.get("semantic_eval", {}).get("used"),
                    "backend": report.get("semantic_eval", {}).get("backend"),
                    "verdict": report.get("semantic_eval", {}).get("semantic_verdict"),
                    "error": report.get("semantic_eval", {}).get("error"),
                },
                "outputs": result.get("outputs"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one Sketch2DXF case through deterministic debug, Agent review, approval, apply, and eval."
    )
    parser.add_argument("image_path", help="Path to an input PNG/JPG image.")
    parser.add_argument("--run-name", default=None, help="Output run name under outputs/debug_runs.")
    parser.add_argument(
        "--proposal-backend",
        choices=("traditional", "yolo"),
        default=None,
        help="Override component proposal backend.",
    )
    parser.add_argument(
        "--debug-level",
        choices=("standard", "full"),
        default="standard",
        help="Debug artifact level passed to debug_run.py.",
    )
    parser.add_argument(
        "--dxf-mode",
        choices=("clean", "debug"),
        default="clean",
        help="DXF export style.",
    )
    parser.add_argument(
        "--backend",
        choices=("rule", "mock", "openai", "deepseek", "custom"),
        default="deepseek",
        help="Planner/reviewer backend for the Agent advisor.",
    )
    parser.add_argument(
        "--audit-backend",
        choices=("rule", "mock", "openai", "deepseek", "custom"),
        default=None,
        help="Audit backend. Defaults to the planner backend.",
    )
    parser.add_argument("--model", default="deepseek-v4-pro", help="LLM model name.")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL.")
    parser.add_argument("--api-key", default=None, help="API key. Prefer --api-key-env for normal use.")
    parser.add_argument(
        "--api-key-env",
        default=None,
        help="Environment variable containing the API key. Defaults by backend.",
    )
    parser.add_argument(
        "--workflow-engine",
        choices=("auto", "local", "langgraph"),
        default="langgraph",
        help="Agent workflow engine.",
    )
    parser.add_argument("--max-agent-tool-steps", type=int, default=12)
    parser.add_argument("--max-tool-calls-per-step", type=int, default=1)
    parser.add_argument("--memory-file", default=None, help="Optional failure memory JSON.")
    parser.add_argument("--memory-limit", type=int, default=5)
    parser.add_argument("--approved-by", default="manual_cli", help="Human approver name.")
    parser.add_argument("--notes", default="Approved from main_run.py.", help="Human approval notes.")
    parser.add_argument(
        "--strategy-name",
        default=None,
        help="Eval strategy name. Defaults to <run-name>_<backend>.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive prompt and approve the repair_plan automatically.",
    )
    parser.add_argument(
        "--no-apply",
        action="store_true",
        help="Run advisor and eval, but do not ask for approval or apply repairs.",
    )
    parser.add_argument(
        "--no-refresh-audit",
        action="store_true",
        help="Do not regenerate agent_audit_report.json before advisor planning.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the subprocess commands without executing them.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.api_key_env = args.api_key_env or _default_api_key_env(args.backend)
    args.audit_backend = args.audit_backend or args.backend

    image_path = Path(args.image_path)
    run_name = args.run_name or f"{image_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    debug_dir = DEFAULT_OUTPUT_ROOT / run_name
    advisor_dir = debug_dir / "agent_advisor"
    repair_dir = debug_dir / "repair_apply"
    eval_dir = debug_dir / "agent_eval"
    strategy_name = args.strategy_name or f"{run_name}_{args.backend}"

    env = os.environ.copy()
    secrets = {args.api_key} if args.api_key else set()
    if not args.dry_run:
        _validate_api_key(args, env)

    python_exe = sys.executable

    debug_command = [
        python_exe,
        "-B",
        "debug_run.py",
        str(image_path),
        "--run-name",
        run_name,
        "--debug-level",
        args.debug_level,
        "--dxf-mode",
        args.dxf_mode,
    ]
    if args.proposal_backend:
        debug_command.extend(["--proposal-backend", args.proposal_backend])
    result = _run_command(debug_command, env, args.dry_run, secrets)
    _require_success("debug_run", result)

    print("\n$ agent_advisor(core workflow)")
    if args.dry_run:
        print(f"- debug_dir: {debug_dir}")
        print(f"- output_dir: {advisor_dir}")
    else:
        advisor_result = run_agent_repair_advisor_workflow(
            debug_dir,
            output_dir=advisor_dir,
            backend=args.backend,
            audit_backend=args.audit_backend,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
            workflow_engine=args.workflow_engine,
            refresh_audit=not args.no_refresh_audit,
            max_agent_tool_steps=args.max_agent_tool_steps,
            max_tool_calls_per_step=args.max_tool_calls_per_step,
            memory_file=args.memory_file,
            memory_limit=args.memory_limit,
        )
        _print_advisor_summary(advisor_result)

    advisor_report_path = advisor_dir / "agent_repair_advisor_report.json"
    advisor_report = None if args.dry_run else _read_json(advisor_report_path)
    _print_review_paths(debug_dir, advisor_dir, advisor_report)

    apply_dir_for_eval: Path | None = None
    if not args.no_apply and _has_repair_plan(advisor_report):
        approved = args.yes
        if not approved:
            answer = input("\nType yes after human review to apply this repair_plan: ").strip().lower()
            approved = answer == "yes"
        if approved:
            plan_id = str(advisor_report.get("repair_plan", {}).get("plan_id") or "PLAN1")
            print("\n$ repair_apply(core workflow)")
            if args.dry_run:
                print(f"- plan_id: {plan_id}")
                print(f"- output_dir: {repair_dir}")
            else:
                apply_result = run_human_approved_repair_apply(
                    debug_dir,
                    advisor_dir=advisor_dir,
                    output_dir=repair_dir,
                    plan_id=plan_id,
                    approval="accept",
                    approved_by=args.approved_by,
                    notes=args.notes,
                )
                _print_apply_summary(apply_result)
            apply_dir_for_eval = repair_dir
            print(f"\nRepair applied. Corrected DXF: {repair_dir / 'corrected_export.dxf'}")
        else:
            print("\nRepair was not approved. Eval will run without apply output.")
    elif args.no_apply:
        print("\n--no-apply is set. Skipping approval/apply.")
    else:
        print("\nNo repair_plan with candidate steps was generated. Skipping apply.")

    print("\n$ agent_eval(core workflow)")
    if args.dry_run:
        print(f"- debug_dir: {debug_dir}")
        print(f"- advisor_dir: {advisor_dir}")
        if apply_dir_for_eval:
            print(f"- apply_dir: {apply_dir_for_eval}")
    else:
        eval_result = run_single_case_eval(
            debug_dir,
            advisor_dir=advisor_dir,
            apply_dir=apply_dir_for_eval,
            output_dir=eval_dir,
            strategy_name=strategy_name,
            llm_backend=args.backend,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
        )
        _print_eval_summary(eval_result)

    print("\nDone.")
    print(f"- Debug dir: {debug_dir}")
    print(f"- Advisor report: {advisor_dir / 'agent_repair_advisor_report.md'}")
    if apply_dir_for_eval:
        print(f"- Corrected DXF: {apply_dir_for_eval / 'corrected_export.dxf'}")
        print(f"- Replay report: {apply_dir_for_eval / 'repair_replay_report.md'}")
    print(f"- Eval report: {eval_dir / 'agent_eval_report.md'}")


if __name__ == "__main__":
    main()
