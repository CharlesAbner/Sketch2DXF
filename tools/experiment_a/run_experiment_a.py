"""Experiment A: run the final case set through the full Agent repair loop.

Case set:
- 001, 003, 004, 005
- 101-110
- 202-204
- 301-303

For each case this script calls main_run.py, so the exact single-case demo path
is reused for the experiment path.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.generators.final_case_registry import final_cases, missing_generated_case_paths


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "experiments" / "experiment_a"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_command(command: list[str], env: dict[str, str], dry_run: bool) -> dict[str, Any]:
    print("\n$ " + " ".join(command))
    if dry_run:
        return {"returncode": 0, "stdout": "", "stderr": "", "dry_run": True, "command": command}
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
        "command": command,
    }


def _case_report(run_name: str, case_id: str, image_path: Path, command_result: dict[str, Any]) -> dict[str, Any]:
    debug_dir = PROJECT_ROOT / "outputs" / "debug_runs" / run_name
    advisor_dir = debug_dir / "agent_advisor"
    repair_dir = debug_dir / "repair_apply"
    eval_dir = debug_dir / "agent_eval"

    advisor = _read_json(advisor_dir / "agent_repair_advisor_report.json") or {}
    replay = _read_json(repair_dir / "repair_replay_report.json") or {}
    eval_report = _read_json(eval_dir / "agent_eval_report.json") or {}
    case_summary = _read_json(debug_dir / "case_summary.json") or {}

    plan_steps = advisor.get("repair_plan", {}).get("steps", []) if isinstance(advisor, dict) else []
    return {
        "case_id": case_id,
        "image_path": str(image_path),
        "run_name": run_name,
        "debug_dir": str(debug_dir),
        "command_result": {
            "returncode": command_result.get("returncode"),
            "dry_run": command_result.get("dry_run"),
        },
        "review_status": case_summary.get("review_status"),
        "quality_label": case_summary.get("summary", {}).get("quality_label"),
        "selected_node_source": case_summary.get("summary", {}).get("selected_node_source"),
        "fallback_used": case_summary.get("summary", {}).get("fallback_used"),
        "advisor": {
            "exists": bool(advisor),
            "llm_used": advisor.get("llm_used"),
            "final_decision": advisor.get("final_decision"),
            "tool_result_count": len(advisor.get("tool_results", [])) if isinstance(advisor, dict) else 0,
            "repair_plan_step_count": len(plan_steps) if isinstance(plan_steps, list) else 0,
            "repair_plan_candidate_ids": [
                step.get("candidate_id") for step in plan_steps if isinstance(step, dict)
            ],
        },
        "apply": {
            "exists": bool(replay),
            "status": replay.get("status"),
            "applied_candidates": [
                item.get("candidate_id") for item in replay.get("applied_candidates", [])
            ]
            if isinstance(replay, dict)
            else [],
            "export_success": replay.get("export", {}).get("export_success") if isinstance(replay, dict) else None,
        },
        "eval": {
            "exists": bool(eval_report),
            "eval_status": eval_report.get("eval_status"),
            "scores": eval_report.get("scores"),
            "repair_status": eval_report.get("repair_effect", {}).get("status")
            if isinstance(eval_report, dict)
            else None,
            "semantic_verdict": eval_report.get("semantic_eval", {}).get("semantic_verdict")
            if isinstance(eval_report, dict)
            else None,
        },
        "artifacts": {
            "overlay": str(debug_dir / "13_overlay.png"),
            "dxf": str(debug_dir / "14_export.dxf"),
            "advisor_report": str(advisor_dir / "agent_repair_advisor_report.md"),
            "human_dossier": str(advisor_dir / "agent_human_review_dossier.md"),
            "corrected_dxf": str(repair_dir / "corrected_export.dxf"),
            "eval_report": str(eval_dir / "agent_eval_report.md"),
        },
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Experiment A Report: {report.get('run_id')}",
        "",
        f"- status: `{report.get('overall_status')}`",
        f"- backend/model: `{report.get('backend')}` / `{report.get('model')}`",
        f"- case count: `{report.get('case_count')}`",
        f"- success count: `{report.get('success_count')}`",
        f"- failure count: `{report.get('failure_count')}`",
        "",
        "## Cases",
        "",
        "| case | group | status | decision | plan steps | apply | eval |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for case in report.get("cases", []):
        lines.append(
            "| "
            f"{case.get('case_id')} | "
            f"{case.get('group')} | "
            f"{case.get('review_status')} | "
            f"{case.get('advisor', {}).get('final_decision')} | "
            f"{case.get('advisor', {}).get('repair_plan_step_count')} | "
            f"{case.get('apply', {}).get('status')} | "
            f"{case.get('eval', {}).get('eval_status')} |"
        )
    if report.get("failures"):
        lines.extend(["", "## Failures", ""])
        for failure in report["failures"]:
            lines.append(f"- `{failure.get('case_id')}`: {failure.get('error')}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Experiment A full Agent loop on final cases.")
    parser.add_argument("--python-exe", default=sys.executable, help="Python executable.")
    parser.add_argument("--run-tag", default=None, help="Optional run tag. Defaults to timestamp.")
    parser.add_argument("--output-dir", default=None, help="Experiment report output directory.")
    parser.add_argument("--case-id", action="append", default=None, help="Run only selected case id(s).")
    parser.add_argument("--proposal-backend", choices=("traditional", "yolo"), default="yolo")
    parser.add_argument("--backend", choices=("rule", "mock", "openai", "deepseek", "custom"), default="deepseek")
    parser.add_argument("--audit-backend", choices=("rule", "mock", "openai", "deepseek", "custom"), default=None)
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--workflow-engine", choices=("auto", "local", "langgraph"), default="langgraph")
    parser.add_argument("--max-agent-tool-steps", type=int, default=12)
    parser.add_argument("--max-tool-calls-per-step", type=int, default=1)
    parser.add_argument("--skip-generate", action="store_true", help="Do not generate missing synthetic images.")
    parser.add_argument("--no-auto-apply", action="store_true", help="Run advisor/eval without automatic yes apply.")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    env = os.environ.copy()
    if args.backend not in {"rule", "mock"} and not env.get(args.api_key_env) and not args.dry_run:
        raise SystemExit(f"No API key found in ${args.api_key_env}.")

    if not args.skip_generate and missing_generated_case_paths():
        generate_cmd = [args.python_exe, "-B", "tools/generators/generate_cases.py", "--suite", "final"]
        result = _run_command(generate_cmd, env, args.dry_run)
        if int(result.get("returncode", 1)) != 0:
            raise SystemExit("case generation failed")

    tag = args.run_tag or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = f"experiment_a_{tag}"
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = []
    failures = []
    for case in final_cases(args.case_id):
        run_name = f"{run_id}_{case.case_id}"
        command = [
            args.python_exe,
            "-B",
            "main_run.py",
            str(case.image_path),
            "--run-name",
            run_name,
            "--proposal-backend",
            args.proposal_backend,
            "--backend",
            args.backend,
            "--audit-backend",
            args.audit_backend or args.backend,
            "--model",
            args.model,
            "--api-key-env",
            args.api_key_env,
            "--workflow-engine",
            args.workflow_engine,
            "--max-agent-tool-steps",
            str(args.max_agent_tool_steps),
            "--max-tool-calls-per-step",
            str(args.max_tool_calls_per_step),
            "--strategy-name",
            f"{run_id}_{args.backend}_{args.model}".replace("-", "_").replace(".", "_"),
        ]
        if args.base_url:
            command.extend(["--base-url", args.base_url])
        if args.no_auto_apply:
            command.append("--no-apply")
        else:
            command.append("--yes")
        if args.dry_run:
            command.append("--dry-run")

        result = _run_command(command, env, args.dry_run)
        if int(result.get("returncode", 1)) == 0:
            case_payload = _case_report(run_name, case.case_id, case.image_path, result)
            case_payload["group"] = case.group
            cases.append(case_payload)
        else:
            failures.append(
                {
                    "case_id": case.case_id,
                    "image_path": str(case.image_path),
                    "returncode": result.get("returncode"),
                    "error": result.get("stderr") or result.get("stdout"),
                }
            )
            if not args.continue_on_error:
                break

    report = {
        "schema_version": "experiment-a-final-agent-loop-v1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": "fail" if failures else "pass",
        "backend": args.backend,
        "model": args.model,
        "case_count": len(cases) + len(failures),
        "success_count": len(cases),
        "failure_count": len(failures),
        "auto_apply": not args.no_auto_apply,
        "cases": cases,
        "failures": failures,
        "outputs": {
            "json": str(output_dir / "experiment_a_report.json"),
            "markdown": str(output_dir / "experiment_a_report.md"),
        },
    }
    _write_json(output_dir / "experiment_a_report.json", report)
    (output_dir / "experiment_a_report.md").write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["overall_status"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
