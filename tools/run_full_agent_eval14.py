"""Run the full 14-case debug -> LLM agent -> auto-apply -> LLM eval loop.

Edit the user config block below, especially API_KEY, then run:

    D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_full_agent_eval14.py

This script intentionally does not overwrite the original topology/netlist/DXF
inside each debug run. Auto-apply is limited to apply-able repair candidates
selected by the advisor.
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


# ============================= USER CONFIG =============================
# Paste your key here, or leave it blank and set the environment variable
# named by API_KEY_ENV before running the script.
API_KEY = ""

# DeepSeek defaults. You can switch to "openai" or "custom" if needed.
LLM_BACKEND = "deepseek"
LLM_MODEL = "deepseek-v4-pro"
LLM_BASE_URL = None  # DeepSeek defaults to https://api.deepseek.com when None.
API_KEY_ENV = "DEEPSEEK_API_KEY"

# Use LLM in advisor planner/reviewer and in semantic eval. Audit LLM can be
# slower/costlier; keep it True if you want every LLM-capable layer enabled.
USE_LLM_FOR_AUDIT = True
REFRESH_AUDIT = True

# Core pipeline and agent settings.
PROPOSAL_BACKEND = "yolo"
DEBUG_LEVEL = "standard"
WORKFLOW_ENGINE = "langgraph"
MAX_AGENT_TOOL_STEPS = 8
MAX_TOOL_CALLS_PER_STEP = 1

# Human approval metadata for automatic apply-able repairs.
AUTO_APPROVE_APPLYABLE_REPAIRS = True
AUTO_APPROVED_BY = "auto_full_eval14"
AUTO_APPROVAL_NOTES = "Automatically accepted apply-able repair candidate during full eval14 run."

# Run naming. Leave RUN_TAG blank to use a timestamp and avoid overwriting old runs.
RUN_NAME_PREFIX = "full14"
RUN_TAG = ""

# Optional failure memory context for advisor. Leave blank to disable.
MEMORY_FILE = ""
MEMORY_LIMIT = 5
# ======================================================================


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "debug_runs"

CASE_SPECS = [
    ("001", PROJECT_ROOT / "data" / "samples_easy" / "001_series_loop.png"),
    ("003", PROJECT_ROOT / "data" / "samples_easy" / "003_parallel_branches.png"),
    ("004", PROJECT_ROOT / "data" / "samples_easy" / "004_china_R.jpg"),
    ("005", PROJECT_ROOT / "data" / "samples_easy" / "005_min.png"),
    ("101", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "101_series_clean.png"),
    ("102", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "102_parallel_branches.png"),
    ("103", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "103_broken_gap.png"),
    ("104", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "104_text_noise_near_wire.png"),
    ("105", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "105_crossing_no_connect.png"),
    ("106", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "106_fake_line_near_terminal.png"),
    ("107", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "107_slanted_wires.png"),
    ("108", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "108_t_junction_branch.png"),
    ("109", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "109_tiny_loop.png"),
    ("110", PROJECT_ROOT / "data" / "generated" / "handdrawn_stress" / "110_rc_ladder.png"),
]


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_command(command: list[str], env: dict[str, str], dry_run: bool = False) -> dict[str, Any]:
    safe_command = [
        "<redacted>" if item and item == API_KEY else item
        for item in command
    ]
    if dry_run:
        return {
            "command": safe_command,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "dry_run": True,
        }
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": safe_command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "dry_run": False,
    }


def _require_success(step: str, result: dict[str, Any]) -> None:
    if int(result.get("returncode", 1)) != 0:
        raise RuntimeError(
            f"{step} failed with code {result.get('returncode')}\n"
            f"STDOUT:\n{result.get('stdout')}\n"
            f"STDERR:\n{result.get('stderr')}"
        )


def _ensure_generated_stress_images(python_exe: str, env: dict[str, str], dry_run: bool) -> dict[str, Any] | None:
    missing = [str(path) for case_id, path in CASE_SPECS if case_id >= "101" and not path.exists()]
    if not missing:
        return None
    command = [
        python_exe,
        "-B",
        "tools/generate_handdrawn_tests.py",
        "--output-dir",
        "data/generated/handdrawn_stress",
    ]
    result = _run_command(command, env, dry_run=dry_run)
    _require_success("generate_handdrawn_tests", result)
    return {
        "missing_before": missing,
        "command_result": result,
    }


def _candidate_pool(advisor_report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    dossier = advisor_report.get("human_review_dossier", {})
    candidates.extend(dossier.get("selected_candidates", []))
    for step in advisor_report.get("repair_plan", {}).get("steps", []) or []:
        if isinstance(step, dict) and isinstance(step.get("candidate"), dict):
            candidates.append(step["candidate"])
    for step in dossier.get("repair_plan", {}).get("steps", []) or []:
        if isinstance(step, dict) and isinstance(step.get("candidate"), dict):
            candidates.append(step["candidate"])
    for result in advisor_report.get("tool_results", []):
        summary = result.get("result_summary", {})
        candidates.extend(summary.get("top_candidates", []))
        candidates.extend(summary.get("candidates", []))

    deduped = []
    seen = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("repair_candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        deduped.append(candidate)
    return deduped


def _selected_applyable_candidate(advisor_report: dict[str, Any]) -> dict[str, Any] | None:
    plan_candidate_ids = [
        str(step.get("candidate_id"))
        for step in advisor_report.get("repair_plan", {}).get("steps", []) or []
        if isinstance(step, dict) and step.get("candidate_id")
    ]
    if not plan_candidate_ids:
        return None
    candidate_lookup = {
        str(candidate.get("candidate_id") or candidate.get("repair_candidate_id")): candidate
        for candidate in _candidate_pool(advisor_report)
    }
    for candidate_id in plan_candidate_ids:
        candidate = candidate_lookup.get(candidate_id)
        if candidate and candidate.get("repair_type") in {
            "merge_nodes",
            "reattach_pin",
            "gap_bridge_merge",
            "single_pin_stub_bridge",
            "component_pin_axis_flip",
            "component_class_override",
        }:
            return candidate
    return None


def _llm_args() -> list[str]:
    args = ["--backend", LLM_BACKEND, "--model", LLM_MODEL]
    if LLM_BASE_URL:
        args.extend(["--base-url", LLM_BASE_URL])
    if API_KEY_ENV:
        args.extend(["--api-key-env", API_KEY_ENV])
    return args


def _eval_llm_args() -> list[str]:
    args = ["--llm-backend", LLM_BACKEND, "--model", LLM_MODEL]
    if LLM_BASE_URL:
        args.extend(["--base-url", LLM_BASE_URL])
    if API_KEY_ENV:
        args.extend(["--api-key-env", API_KEY_ENV])
    return args


def _audit_args() -> list[str]:
    if USE_LLM_FOR_AUDIT:
        args = ["--audit-backend", LLM_BACKEND]
        if REFRESH_AUDIT:
            args.append("--refresh-audit")
        return args
    return ["--audit-backend", "rule"]


def _prepare_env() -> dict[str, str]:
    env = os.environ.copy()
    if API_KEY:
        env[API_KEY_ENV] = API_KEY
    return env


def _validate_llm_config(env: dict[str, str]) -> None:
    if LLM_BACKEND == "rule":
        return
    if not API_KEY_ENV:
        raise ValueError("API_KEY_ENV must be set when LLM_BACKEND is not rule.")
    if not env.get(API_KEY_ENV):
        raise ValueError(
            f"No API key found. Fill API_KEY at the top of this script or set ${API_KEY_ENV}."
        )


def run_case(
    python_exe: str,
    case_id: str,
    image_path: Path,
    run_name: str,
    strategy_name: str,
    env: dict[str, str],
    dry_run: bool,
) -> dict[str, Any]:
    debug_dir = DEFAULT_OUTPUT_ROOT / run_name
    advisor_dir = debug_dir / f"agent_{LLM_BACKEND}_{strategy_name}"
    repair_dir = debug_dir / f"repair_{strategy_name}"
    eval_dir = debug_dir / f"agent_eval_{strategy_name}"

    case_report: dict[str, Any] = {
        "case_id": case_id,
        "image_path": str(image_path),
        "run_name": run_name,
        "debug_dir": str(debug_dir),
        "advisor_dir": str(advisor_dir),
        "repair_dir": None,
        "eval_dir": str(eval_dir),
        "steps": {},
        "auto_apply": {
            "attempted": False,
            "applied": False,
            "candidate_id": None,
            "repair_type": None,
            "reason": None,
        },
    }

    debug_command = [
        python_exe,
        "-B",
        "debug_run.py",
        str(image_path),
        "--proposal-backend",
        PROPOSAL_BACKEND,
        "--run-name",
        run_name,
        "--debug-level",
        DEBUG_LEVEL,
    ]
    result = _run_command(debug_command, env, dry_run=dry_run)
    case_report["steps"]["debug_run"] = result
    _require_success(f"{case_id} debug_run", result)

    advisor_command = [
        python_exe,
        "-B",
        "tools/run_agent_repair_advisor.py",
        str(debug_dir),
        *_llm_args(),
        *_audit_args(),
        "--workflow-engine",
        WORKFLOW_ENGINE,
        "--max-agent-tool-steps",
        str(MAX_AGENT_TOOL_STEPS),
        "--max-tool-calls-per-step",
        str(MAX_TOOL_CALLS_PER_STEP),
        "--output-dir",
        str(advisor_dir),
    ]
    if MEMORY_FILE:
        advisor_command.extend(["--memory-file", MEMORY_FILE, "--memory-limit", str(MEMORY_LIMIT)])
    result = _run_command(advisor_command, env, dry_run=dry_run)
    case_report["steps"]["advisor"] = result
    _require_success(f"{case_id} advisor", result)

    advisor_report_path = advisor_dir / "agent_repair_advisor_report.json"
    advisor_report = _read_json(advisor_report_path) if not dry_run else {}
    candidate = _selected_applyable_candidate(advisor_report) if isinstance(advisor_report, dict) else None
    if AUTO_APPROVE_APPLYABLE_REPAIRS and candidate:
        candidate_id = str(candidate.get("candidate_id"))
        plan_id = str(advisor_report.get("repair_plan", {}).get("plan_id") or "PLAN1")
        case_report["auto_apply"].update(
            {
                "attempted": True,
                "plan_id": plan_id,
                "candidate_id": candidate_id,
                "repair_type": candidate.get("repair_type"),
            }
        )
        apply_command = [
            python_exe,
            "-B",
            "tools/run_agent_repair_apply.py",
            str(debug_dir),
            "--advisor-dir",
            str(advisor_dir),
            "--plan-id",
            plan_id,
            "--approval",
            "accept",
            "--approved-by",
            AUTO_APPROVED_BY,
            "--notes",
            AUTO_APPROVAL_NOTES,
            "--output-dir",
            str(repair_dir),
        ]
        result = _run_command(apply_command, env, dry_run=dry_run)
        case_report["steps"]["apply"] = result
        _require_success(f"{case_id} apply", result)
        case_report["repair_dir"] = str(repair_dir)
        case_report["auto_apply"]["applied"] = True
    else:
        plan_candidate_ids = [
            str(step.get("candidate_id"))
            for step in advisor_report.get("repair_plan", {}).get("steps", []) or []
            if isinstance(step, dict) and step.get("candidate_id")
        ] if isinstance(advisor_report, dict) else []
        case_report["auto_apply"]["reason"] = (
            "No selected apply-able repair candidate."
            if plan_candidate_ids
            else "Advisor prepared no repair plan."
        )

    eval_command = [
        python_exe,
        "-B",
        "tools/run_agent_eval_harness.py",
        "--case-dir",
        str(debug_dir),
        "--advisor-dir",
        str(advisor_dir),
        "--strategy-name",
        strategy_name,
        *_eval_llm_args(),
        "--output-dir",
        str(eval_dir),
    ]
    if case_report["repair_dir"]:
        eval_command.extend(["--apply-dir", str(repair_dir)])
    result = _run_command(eval_command, env, dry_run=dry_run)
    case_report["steps"]["eval"] = result
    _require_success(f"{case_id} eval", result)

    eval_report = _read_json(eval_dir / "agent_eval_report.json") if not dry_run else {}
    if isinstance(eval_report, dict):
        case_report["eval_status"] = eval_report.get("eval_status")
        case_report["scores"] = eval_report.get("scores", {})
        case_report["repair_effect"] = {
            "status": eval_report.get("repair_effect", {}).get("status"),
            "candidate": eval_report.get("repair_effect", {}).get("candidate", {}),
            "improvement": eval_report.get("repair_effect", {}).get("improvement", {}),
            "findings": eval_report.get("deterministic_findings", []),
        }
        case_report["semantic_eval"] = eval_report.get("semantic_eval", {})
    return case_report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Full Agent Eval14 Run: {report.get('run_id')}",
        "",
        f"- status: `{report.get('overall_status')}`",
        f"- strategy: `{report.get('strategy_name')}`",
        f"- backend/model: `{report.get('llm_backend')}` / `{report.get('llm_model')}`",
        f"- proposal backend: `{report.get('proposal_backend')}`",
        f"- generated at: `{report.get('generated_at')}`",
        "",
        "## Summary",
        "",
        f"- cases: `{len(report.get('cases', []))}`",
        f"- auto-applied repairs: `{report.get('auto_applied_count')}`",
        f"- failed cases: `{report.get('failed_case_count')}`",
        f"- eval status counts: `{report.get('eval_status_counts')}`",
        f"- outputs: `{report.get('outputs', {})}`",
        "",
        "## Cases",
        "",
        "| case | eval | auto apply | repair status | semantic | overall |",
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for case in report.get("cases", []):
        semantic = case.get("semantic_eval", {})
        repair = case.get("repair_effect", {})
        lines.append(
            "| "
            f"{case.get('case_id')} | "
            f"{case.get('eval_status')} | "
            f"{case.get('auto_apply', {}).get('candidate_id')} | "
            f"{repair.get('status')} | "
            f"{semantic.get('semantic_verdict')} | "
            f"{case.get('scores', {}).get('overall_score')} |"
        )
    if report.get("failures"):
        lines.extend(["", "## Failures", ""])
        for failure in report.get("failures", []):
            lines.append(f"- `{failure.get('case_id')}` step=`{failure.get('step')}`: {failure.get('error')}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full 14-case LLM agent eval pipeline.")
    parser.add_argument(
        "--python-exe",
        default=sys.executable,
        help="Python executable to use for subprocesses. Defaults to current interpreter.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Master output directory. Defaults to outputs/debug_runs/<run_id>.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print/report commands without executing them.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue later cases when one case fails.",
    )
    parser.add_argument(
        "--skip-summary",
        action="store_true",
        help="Skip final multi-case summary.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    env = _prepare_env()
    if not args.dry_run:
        _validate_llm_config(env)

    tag = RUN_TAG or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = f"{RUN_NAME_PREFIX}_{tag}"
    strategy_name = f"{run_id}_{LLM_BACKEND}_{LLM_MODEL}".replace(".", "_").replace("-", "_")
    output_root = Path(args.output_root) if args.output_root else DEFAULT_OUTPUT_ROOT / run_id
    output_root.mkdir(parents=True, exist_ok=True)

    setup_result = _ensure_generated_stress_images(args.python_exe, env, args.dry_run)

    cases = []
    failures = []
    for case_id, image_path in CASE_SPECS:
        run_name = f"{run_id}_{case_id}"
        try:
            case_report = run_case(
                args.python_exe,
                case_id,
                image_path,
                run_name,
                strategy_name,
                env,
                args.dry_run,
            )
            cases.append(case_report)
        except Exception as exc:
            failures.append(
                {
                    "case_id": case_id,
                    "image_path": str(image_path),
                    "step": "case_pipeline",
                    "error": str(exc),
                }
            )
            if not args.continue_on_error:
                break

    summary_result: dict[str, Any] | None = None
    if not args.skip_summary and not args.dry_run:
        pattern = f"{run_id}_*"
        summary_command = [
            args.python_exe,
            "-B",
            "tools/run_agent_eval_harness.py",
            "--cases-dir",
            str(DEFAULT_OUTPUT_ROOT),
            "--pattern",
            pattern,
            "--strategy-name",
            strategy_name,
            *_eval_llm_args(),
            "--output-dir",
            str(output_root / "agent_eval_summary"),
        ]
        summary_result = _run_command(summary_command, env, dry_run=False)
        if int(summary_result.get("returncode", 1)) != 0:
            failures.append(
                {
                    "case_id": "summary",
                    "step": "multi_case_summary",
                    "error": summary_result.get("stderr") or summary_result.get("stdout"),
                }
            )

    eval_status_counts: dict[str, int] = {}
    for case in cases:
        status = str(case.get("eval_status") or "unknown")
        eval_status_counts[status] = eval_status_counts.get(status, 0) + 1

    report = {
        "schema_version": "3.8-full-agent-eval14",
        "run_id": run_id,
        "strategy_name": strategy_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": "fail" if failures else "pass",
        "llm_backend": LLM_BACKEND,
        "llm_model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
        "proposal_backend": PROPOSAL_BACKEND,
        "workflow_engine": WORKFLOW_ENGINE,
        "use_llm_for_audit": USE_LLM_FOR_AUDIT,
        "refresh_audit": REFRESH_AUDIT,
        "auto_approve_applyable_repairs": AUTO_APPROVE_APPLYABLE_REPAIRS,
        "auto_applied_count": sum(1 for case in cases if case.get("auto_apply", {}).get("applied")),
        "failed_case_count": len(failures),
        "eval_status_counts": eval_status_counts,
        "setup": setup_result,
        "cases": cases,
        "failures": failures,
        "summary_command_result": summary_result,
        "outputs": {
            "master_dir": str(output_root),
            "json": str(output_root / "full_agent_eval14_report.json"),
            "markdown": str(output_root / "full_agent_eval14_report.md"),
            "summary_dir": str(output_root / "agent_eval_summary") if summary_result else None,
        },
    }
    json_path = output_root / "full_agent_eval14_report.json"
    md_path = output_root / "full_agent_eval14_report.md"
    _write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(
        json.dumps(
            {
                "schema_version": report["schema_version"],
                "run_id": run_id,
                "overall_status": report["overall_status"],
                "strategy_name": strategy_name,
                "eval_status_counts": eval_status_counts,
                "auto_applied_count": report["auto_applied_count"],
                "failed_case_count": len(failures),
                "outputs": report["outputs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
