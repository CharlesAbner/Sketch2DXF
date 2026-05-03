"""Evaluate agent advisor/apply runs for one case or a batch of cases."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from src.agent_workflow.llm_client import complete_json


EVAL_SCHEMA_VERSION = "3.6-agent-eval-harness"
SUMMARY_SCHEMA_VERSION = "3.6-agent-eval-summary"
EVAL_REPORT_JSON = "agent_eval_report.json"
EVAL_REPORT_MD = "agent_eval_report.md"
EVAL_SUMMARY_JSON = "agent_eval_summary.json"
EVAL_SUMMARY_MD = "agent_eval_summary.md"


SEMANTIC_EVAL_SYSTEM_PROMPT = """You are the semantic evaluator for Sketch2DXF.
You receive deterministic before/after metrics, compact netlists, the selected
repair candidate, and the agent trace summary. Return JSON only.

Your role:
- Judge whether the corrected topology is semantically more plausible than the
  original topology.
- Look for short-circuit risk, missing source/load structure, suspicious merges,
  and changes that only improve a metric without circuit evidence.
- Do not inspect images.
- Do not invent node, pin, or component ids.
- Treat deterministic artifact checks as authoritative for file existence,
  approval, and mutation safety.

Required JSON keys:
- semantic_verdict: one of pass, warning, fail, inconclusive
- summary: str
- semantic_score: number from 0 to 1
- confirmed_by_artifacts: list[str]
- risks: list[str]
- next_checks: list[str]
"""


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _latest_file(root: Path, filename: str) -> Path | None:
    if not root.exists():
        return None
    matches = [path for path in root.rglob(filename) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _load_json_from_candidates(paths: list[Path], latest_root: Path | None, filename: str) -> tuple[dict[str, Any] | None, str | None]:
    explicit = _first_existing(paths)
    if explicit:
        doc = _read_json(explicit)
        return (doc if isinstance(doc, dict) else None), str(explicit)
    if latest_root:
        latest = _latest_file(latest_root, filename)
        if latest:
            doc = _read_json(latest)
            return (doc if isinstance(doc, dict) else None), str(latest)
    return None, None


def _load_advisor_report(
    debug_dir: Path,
    advisor_dir: Path | None,
    advisor_report: Path | None,
) -> tuple[dict[str, Any] | None, str | None]:
    return _load_json_from_candidates(
        [
            *( [advisor_report] if advisor_report else [] ),
            *( [advisor_dir / "agent_repair_advisor_report.json"] if advisor_dir else [] ),
            debug_dir / "agent_repair_advisor_report.json",
        ],
        debug_dir,
        "agent_repair_advisor_report.json",
    )


def _load_replay_report(
    debug_dir: Path,
    apply_dir: Path | None,
    apply_report: Path | None,
) -> tuple[dict[str, Any] | None, str | None]:
    return _load_json_from_candidates(
        [
            *( [apply_report] if apply_report else [] ),
            *( [apply_dir / "repair_replay_report.json"] if apply_dir else [] ),
            debug_dir / "repair_replay_report.json",
            debug_dir / "repair_apply" / "repair_replay_report.json",
        ],
        debug_dir,
        "repair_replay_report.json",
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _artifact_status(path: Path | None) -> dict[str, Any]:
    if not path:
        return {"exists": False, "path": None, "bytes": 0}
    return {
        "exists": path.exists(),
        "path": str(path),
        "bytes": path.stat().st_size if path.exists() and path.is_file() else 0,
    }


def _netlist_metrics(netlist: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(netlist, dict):
        return {
            "exists": False,
            "component_count": 0,
            "pin_count": 0,
            "net_count": 0,
            "single_pin_net_count": 0,
            "single_pin_net_ids": [],
            "zero_pin_net_count": 0,
            "zero_pin_net_ids": [],
            "same_net_two_pin_components": [],
            "missing_pin_net_refs": [],
            "all_pins_on_one_net": False,
        }

    components = netlist.get("components", [])
    nets = netlist.get("nets", [])
    pins = []
    missing_pin_net_refs = []
    same_net_two_pin_components = []
    for component in components:
        component_pins = component.get("pins", [])
        pins.extend(component_pins)
        pin_nets = [pin.get("net_id") for pin in component_pins]
        for pin in component_pins:
            if not pin.get("net_id"):
                missing_pin_net_refs.append(pin.get("pin_ref") or pin.get("pin_id"))
        if len(component_pins) == 2 and len(set(pin_nets)) == 1:
            same_net_two_pin_components.append(component.get("refdes") or component.get("component_id"))

    single_pin_net_ids = [net.get("net_id") for net in nets if _safe_int(net.get("pin_count")) == 1]
    zero_pin_net_ids = [net.get("net_id") for net in nets if _safe_int(net.get("pin_count")) == 0]
    used_net_ids = {pin.get("net_id") for pin in pins if pin.get("net_id")}
    return {
        "exists": True,
        "component_count": len(components),
        "pin_count": len(pins),
        "net_count": len(nets),
        "single_pin_net_count": len(single_pin_net_ids),
        "single_pin_net_ids": single_pin_net_ids,
        "zero_pin_net_count": len(zero_pin_net_ids),
        "zero_pin_net_ids": zero_pin_net_ids,
        "same_net_two_pin_components": same_net_two_pin_components,
        "missing_pin_net_refs": missing_pin_net_refs,
        "all_pins_on_one_net": len(pins) > 2 and len(used_net_ids) == 1,
    }


def _compact_netlist(netlist: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(netlist, dict):
        return {"exists": False}
    return {
        "exists": True,
        "components": [
            {
                "component_id": component.get("component_id"),
                "refdes": component.get("refdes"),
                "class_name": component.get("class_name"),
                "pins": [
                    {
                        "pin_id": pin.get("pin_id"),
                        "pin_ref": pin.get("pin_ref"),
                        "side": pin.get("side"),
                        "net_id": pin.get("net_id"),
                    }
                    for pin in component.get("pins", [])
                ],
            }
            for component in netlist.get("components", [])[:16]
        ],
        "nets": [
            {
                "net_id": net.get("net_id"),
                "pin_count": net.get("pin_count"),
                "pin_refs": net.get("pin_refs", []),
                "component_refs": net.get("component_refs", []),
            }
            for net in netlist.get("nets", [])[:24]
        ],
    }


def _extract_corrected_paths(replay_report: dict[str, Any] | None, apply_dir: Path | None) -> dict[str, Path | None]:
    outputs = replay_report.get("outputs", {}) if isinstance(replay_report, dict) else {}
    export = replay_report.get("export", {}) if isinstance(replay_report, dict) else {}
    corrected_topology = outputs.get("corrected_topology")
    corrected_netlist = outputs.get("corrected_netlist") or export.get("corrected_netlist_path")
    corrected_dxf = outputs.get("corrected_dxf") or export.get("corrected_dxf_path")
    fallback_dir = apply_dir
    return {
        "corrected_topology": Path(corrected_topology) if corrected_topology else (fallback_dir / "corrected_topology.json" if fallback_dir else None),
        "corrected_netlist": Path(corrected_netlist) if corrected_netlist else (fallback_dir / "corrected_netlist.json" if fallback_dir else None),
        "corrected_dxf": Path(corrected_dxf) if corrected_dxf else (fallback_dir / "corrected_export.dxf" if fallback_dir else None),
    }


def _agent_behavior(advisor_report: dict[str, Any] | None, replay_report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(advisor_report, dict):
        return {
            "exists": False,
            "score": 0.0,
            "findings": [{"severity": "error", "code": "missing_advisor_report", "message": "Advisor report is missing."}],
        }

    tool_results = advisor_report.get("tool_results", [])
    tool_names = [result.get("tool_name") for result in tool_results]
    selected_ids = [str(item) for item in advisor_report.get("selected_candidate_ids", [])]
    candidate_id = None
    if isinstance(replay_report, dict):
        candidate_id = replay_report.get("candidate", {}).get("candidate_id")
    guardrail_messages = []
    planner = advisor_report.get("planner", {})
    for step in planner.get("planner_steps", []):
        guardrail_messages.extend(_as_list(step.get("guardrail_feedback")))
    guardrail_messages.extend(_as_list(planner.get("guardrail_feedback")))

    findings = []
    if advisor_report.get("topology_mutated"):
        findings.append({"severity": "error", "code": "advisor_mutated_topology", "message": "Advisor reported topology mutation."})
    if not tool_results:
        findings.append({"severity": "warning", "code": "no_tool_results", "message": "Advisor produced no tool results."})
    if selected_ids and candidate_id and str(candidate_id) not in selected_ids:
        findings.append({
            "severity": "warning",
            "code": "applied_candidate_not_selected",
            "message": f"Applied candidate {candidate_id} was not in advisor selected ids {selected_ids}.",
        })
    if "repair_dry_run" in tool_names and "get_terminal_attachments" not in tool_names:
        findings.append({
            "severity": "warning",
            "code": "dry_run_without_attachment_tool",
            "message": "repair_dry_run ran without terminal attachment inspection.",
        })

    penalty = 0.0
    for finding in findings:
        penalty += 0.35 if finding["severity"] == "error" else 0.15
    score = max(0.0, 1.0 - penalty)
    return {
        "exists": True,
        "workflow_engine": advisor_report.get("workflow_engine"),
        "workflow_mode": advisor_report.get("workflow_mode"),
        "backend": advisor_report.get("backend"),
        "model": advisor_report.get("model"),
        "llm_used": advisor_report.get("llm_used"),
        "final_decision": advisor_report.get("final_decision"),
        "selected_candidate_ids": selected_ids,
        "tool_result_count": len(tool_results),
        "tool_names": tool_names,
        "guardrail_message_count": len([item for item in guardrail_messages if item]),
        "trace_step_count": len(advisor_report.get("agent_trace", [])),
        "score": round(score, 3),
        "findings": findings,
    }


def _repair_effect(
    debug_dir: Path,
    replay_report: dict[str, Any] | None,
    original_netlist: dict[str, Any] | None,
    corrected_netlist: dict[str, Any] | None,
    corrected_paths: dict[str, Path | None],
) -> dict[str, Any]:
    before = _netlist_metrics(original_netlist)
    after = _netlist_metrics(corrected_netlist)
    if isinstance(replay_report, dict):
        before = {**before, **replay_report.get("before_metrics", {})}
        after = {**after, **replay_report.get("after_metrics", {})}

    dxf_status = _artifact_status(corrected_paths.get("corrected_dxf"))
    export = replay_report.get("export", {}) if isinstance(replay_report, dict) else {}
    export_success = bool(export.get("export_success")) or (dxf_status["exists"] and dxf_status["bytes"] > 0)
    approval_decision = replay_report.get("approval_decision", {}).get("decision") if isinstance(replay_report, dict) else None
    status = replay_report.get("status") if isinstance(replay_report, dict) else "missing"

    findings = []
    if not isinstance(replay_report, dict):
        findings.append({"severity": "warning", "code": "missing_replay_report", "message": "No repair replay report was found."})
    if status == "applied" and approval_decision != "accept":
        findings.append({"severity": "error", "code": "applied_without_accept", "message": "Replay status is applied but approval decision is not accept."})
    if isinstance(replay_report, dict) and replay_report.get("topology_mutated_in_place"):
        findings.append({"severity": "error", "code": "topology_mutated_in_place", "message": "Repair mutated original topology in place."})
    if status == "applied" and not export_success:
        findings.append({"severity": "error", "code": "corrected_export_failed", "message": "Corrected DXF export did not succeed."})
    if before.get("component_count") and after.get("component_count") and before.get("component_count") != after.get("component_count"):
        findings.append({"severity": "error", "code": "component_count_changed", "message": "Component count changed after repair."})
    if before.get("pin_count") and after.get("pin_count") and before.get("pin_count") != after.get("pin_count"):
        findings.append({"severity": "error", "code": "pin_count_changed", "message": "Pin count changed after repair."})
    if after.get("zero_pin_net_count", 0) > before.get("zero_pin_net_count", 0):
        findings.append({"severity": "error", "code": "zero_pin_regression", "message": "Zero-pin net count increased after repair."})
    if after.get("single_pin_net_count", 0) > before.get("single_pin_net_count", 0):
        findings.append({"severity": "warning", "code": "single_pin_regression", "message": "Single-pin net count increased after repair."})
    if status == "applied" and after.get("single_pin_net_count", 0) >= before.get("single_pin_net_count", 0):
        findings.append({"severity": "warning", "code": "single_pin_not_improved", "message": "Applied repair did not reduce single-pin nets."})
    if after.get("same_net_two_pin_components"):
        findings.append({
            "severity": "warning",
            "code": "two_pin_component_same_net",
            "message": f"Two-pin components have both pins on one net: {after.get('same_net_two_pin_components')}.",
        })
    if after.get("all_pins_on_one_net"):
        findings.append({"severity": "error", "code": "all_pins_on_one_net", "message": "All pins are assigned to one net after repair."})

    single_pin_delta = _safe_int(before.get("single_pin_net_count")) - _safe_int(after.get("single_pin_net_count"))
    zero_pin_delta = _safe_int(before.get("zero_pin_net_count")) - _safe_int(after.get("zero_pin_net_count"))
    improvement = {
        "single_pin_delta": single_pin_delta,
        "zero_pin_delta": zero_pin_delta,
        "net_count_delta": _safe_int(before.get("net_count")) - _safe_int(after.get("net_count")),
        "connection_count_delta": _safe_int(after.get("connection_count")) - _safe_int(before.get("connection_count")),
    }
    penalty = 0.0
    for finding in findings:
        penalty += 0.4 if finding["severity"] == "error" else 0.15
    if status == "applied" and single_pin_delta > 0:
        penalty -= 0.1
    score = min(1.0, max(0.0, 1.0 - penalty))
    return {
        "debug_dir": str(debug_dir),
        "status": status,
        "approval_decision": approval_decision,
        "candidate": replay_report.get("candidate", {}) if isinstance(replay_report, dict) else {},
        "before_metrics": before,
        "after_metrics": after,
        "improvement": improvement,
        "export_success": export_success,
        "corrected_dxf": dxf_status,
        "score": round(score, 3),
        "findings": findings,
    }


def _overall_status(findings: list[dict[str, Any]], repair_effect: dict[str, Any], semantic_eval: dict[str, Any]) -> str:
    if any(item.get("severity") == "error" for item in findings):
        return "fail"
    semantic_verdict = semantic_eval.get("semantic_verdict")
    if semantic_verdict == "fail":
        return "fail"
    if semantic_verdict in {"warning", "inconclusive"}:
        return "pass_with_warnings"
    if any(item.get("severity") == "warning" for item in findings):
        return "pass_with_warnings"
    if repair_effect.get("status") != "applied":
        return "needs_review"
    return "pass"


def _score_summary(agent_behavior: dict[str, Any], repair_effect: dict[str, Any], semantic_eval: dict[str, Any]) -> dict[str, Any]:
    semantic_score = semantic_eval.get("semantic_score")
    if semantic_score is None:
        semantic_score = 0.0 if semantic_eval.get("used") else None
    available_scores = [agent_behavior.get("score", 0.0), repair_effect.get("score", 0.0)]
    if semantic_score is not None:
        available_scores.append(_safe_float(semantic_score))
    return {
        "agent_behavior_score": round(_safe_float(agent_behavior.get("score")), 3),
        "repair_effect_score": round(_safe_float(repair_effect.get("score")), 3),
        "semantic_score": round(_safe_float(semantic_score), 3) if semantic_score is not None else None,
        "overall_score": round(mean(available_scores), 3) if available_scores else 0.0,
    }


def _semantic_eval_payload(
    case_id: str,
    advisor_report: dict[str, Any] | None,
    replay_report: dict[str, Any] | None,
    original_netlist: dict[str, Any] | None,
    corrected_netlist: dict[str, Any] | None,
    agent_behavior: dict[str, Any],
    repair_effect: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "deterministic_eval": {
            "agent_behavior": {
                "workflow_engine": agent_behavior.get("workflow_engine"),
                "llm_used": agent_behavior.get("llm_used"),
                "tool_names": agent_behavior.get("tool_names"),
                "selected_candidate_ids": agent_behavior.get("selected_candidate_ids"),
                "findings": agent_behavior.get("findings", []),
            },
            "repair_effect": {
                "status": repair_effect.get("status"),
                "approval_decision": repair_effect.get("approval_decision"),
                "candidate": repair_effect.get("candidate"),
                "before_metrics": repair_effect.get("before_metrics"),
                "after_metrics": repair_effect.get("after_metrics"),
                "improvement": repair_effect.get("improvement"),
                "export_success": repair_effect.get("export_success"),
                "findings": repair_effect.get("findings", []),
            },
        },
        "advisor_review": {
            "final_decision": advisor_report.get("final_decision") if isinstance(advisor_report, dict) else None,
            "reviewer": advisor_report.get("reviewer", {}) if isinstance(advisor_report, dict) else {},
            "human_review_dossier": advisor_report.get("human_review_dossier", {}) if isinstance(advisor_report, dict) else {},
        },
        "approval": replay_report.get("approval_decision", {}) if isinstance(replay_report, dict) else {},
        "before_netlist": _compact_netlist(original_netlist),
        "after_netlist": _compact_netlist(corrected_netlist),
    }


def _normalize_llm_semantic(result: dict[str, Any], backend: str, model: str | None) -> dict[str, Any]:
    content = result.get("content") if isinstance(result.get("content"), dict) else {}
    verdict = content.get("semantic_verdict") or content.get("verdict")
    if verdict not in {"pass", "warning", "fail", "inconclusive"}:
        verdict = "inconclusive" if result.get("used") else "not_run"
    score = content.get("semantic_score")
    if score is None:
        score = 0.5 if verdict == "inconclusive" else 0.0
    return {
        "used": bool(result.get("used")),
        "backend": result.get("backend", backend),
        "model": result.get("model", model),
        "base_url": result.get("base_url"),
        "error": result.get("error"),
        "semantic_verdict": verdict,
        "semantic_score": round(max(0.0, min(1.0, _safe_float(score, 0.5))), 3) if result.get("used") else None,
        "summary": content.get("summary"),
        "confirmed_by_artifacts": [str(item) for item in _as_list(content.get("confirmed_by_artifacts"))],
        "risks": [str(item) for item in _as_list(content.get("risks"))],
        "next_checks": [str(item) for item in _as_list(content.get("next_checks") or content.get("next_actions"))],
    }


def _run_semantic_eval(
    case_id: str,
    advisor_report: dict[str, Any] | None,
    replay_report: dict[str, Any] | None,
    original_netlist: dict[str, Any] | None,
    corrected_netlist: dict[str, Any] | None,
    agent_behavior: dict[str, Any],
    repair_effect: dict[str, Any],
    llm_backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> dict[str, Any]:
    if llm_backend == "rule":
        return {
            "used": False,
            "backend": "rule",
            "model": model,
            "base_url": base_url,
            "error": None,
            "semantic_verdict": "not_run",
            "semantic_score": None,
            "summary": "LLM semantic evaluation was not requested.",
            "confirmed_by_artifacts": [],
            "risks": [],
            "next_checks": [],
        }
    payload = _semantic_eval_payload(
        case_id,
        advisor_report,
        replay_report,
        original_netlist,
        corrected_netlist,
        agent_behavior,
        repair_effect,
    )
    result = complete_json(
        SEMANTIC_EVAL_SYSTEM_PROMPT,
        payload,
        backend=llm_backend,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        temperature=0.1,
    )
    return _normalize_llm_semantic(result, llm_backend, model)


def render_eval_report_markdown(report: dict[str, Any]) -> str:
    scores = report.get("scores", {})
    repair = report.get("repair_effect", {})
    semantic = report.get("semantic_eval", {})
    lines = [
        f"# Agent Eval Report: {report.get('case_id')}",
        "",
        f"- status: `{report.get('eval_status')}`",
        f"- strategy: `{report.get('strategy_name')}`",
        f"- overall score: `{scores.get('overall_score')}`",
        f"- agent behavior score: `{scores.get('agent_behavior_score')}`",
        f"- repair effect score: `{scores.get('repair_effect_score')}`",
        f"- semantic score: `{scores.get('semantic_score')}`",
        "",
        "## Repair Effect",
        "",
        f"- replay status: `{repair.get('status')}`",
        f"- approval decision: `{repair.get('approval_decision')}`",
        f"- candidate: `{repair.get('candidate', {}).get('candidate_id')}` / `{repair.get('candidate', {}).get('repair_type')}`",
        f"- export success: `{repair.get('export_success')}`",
        f"- before: `{repair.get('before_metrics')}`",
        f"- after: `{repair.get('after_metrics')}`",
        f"- improvement: `{repair.get('improvement')}`",
        "",
        "## Agent Behavior",
        "",
        f"- workflow: `{report.get('agent_behavior', {}).get('workflow_engine')}` / `{report.get('agent_behavior', {}).get('workflow_mode')}`",
        f"- backend: `{report.get('agent_behavior', {}).get('backend')}`",
        f"- LLM used: `{report.get('agent_behavior', {}).get('llm_used')}`",
        f"- tool names: `{report.get('agent_behavior', {}).get('tool_names')}`",
        f"- selected candidates: `{report.get('agent_behavior', {}).get('selected_candidate_ids')}`",
        "",
        "## Deterministic Findings",
        "",
    ]
    findings = report.get("deterministic_findings", [])
    if not findings:
        lines.append("- none")
    for finding in findings:
        lines.append(f"- `{finding.get('severity')}` `{finding.get('code')}`: {finding.get('message')}")
    lines.extend(
        [
            "",
            "## LLM Semantic Eval",
            "",
            f"- used: `{semantic.get('used')}`",
            f"- backend/model: `{semantic.get('backend')}` / `{semantic.get('model')}`",
            f"- verdict: `{semantic.get('semantic_verdict')}`",
            f"- summary: {semantic.get('summary')}",
        ]
    )
    if semantic.get("confirmed_by_artifacts"):
        lines.append("- confirmed:")
        for item in semantic.get("confirmed_by_artifacts", []):
            lines.append(f"  - {item}")
    if semantic.get("risks"):
        lines.append("- risks:")
        for item in semantic.get("risks", []):
            lines.append(f"  - {item}")
    if semantic.get("next_checks"):
        lines.append("- next checks:")
        for item in semantic.get("next_checks", []):
            lines.append(f"  - {item}")
    lines.extend(
        [
            "",
            "## Inputs",
            "",
        ]
    )
    for name, artifact in report.get("artifacts", {}).items():
        lines.append(f"- {name}: `{artifact.get('path')}` exists=`{artifact.get('exists')}` bytes=`{artifact.get('bytes')}`")
    return "\n".join(lines)


def run_single_case_eval(
    debug_dir: str | Path,
    advisor_dir: str | Path | None = None,
    advisor_report: str | Path | None = None,
    apply_dir: str | Path | None = None,
    apply_report: str | Path | None = None,
    output_dir: str | Path | None = None,
    strategy_name: str = "default",
    llm_backend: str = "rule",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    write_outputs: bool = True,
) -> dict[str, Any]:
    """Evaluate one debug case with optional LLM semantic review."""
    base_dir = Path(debug_dir)
    out_dir = Path(output_dir) if output_dir else base_dir / "agent_eval"
    advisor_doc, advisor_path = _load_advisor_report(
        base_dir,
        Path(advisor_dir) if advisor_dir else None,
        Path(advisor_report) if advisor_report else None,
    )
    replay_doc, replay_path = _load_replay_report(
        base_dir,
        Path(apply_dir) if apply_dir else None,
        Path(apply_report) if apply_report else None,
    )
    resolved_apply_dir = Path(apply_dir) if apply_dir else (Path(replay_path).parent if replay_path else None)
    corrected_paths = _extract_corrected_paths(replay_doc, resolved_apply_dir)

    original_netlist_path = base_dir / "netlist.json"
    corrected_netlist_path = corrected_paths["corrected_netlist"]
    original_netlist = _read_json(original_netlist_path)
    corrected_netlist = _read_json(corrected_netlist_path) if corrected_netlist_path else None
    original_netlist = original_netlist if isinstance(original_netlist, dict) else None
    corrected_netlist = corrected_netlist if isinstance(corrected_netlist, dict) else None

    agent_behavior = _agent_behavior(advisor_doc, replay_doc)
    repair_effect = _repair_effect(base_dir, replay_doc, original_netlist, corrected_netlist, corrected_paths)
    semantic_eval = _run_semantic_eval(
        advisor_doc.get("case_id") if isinstance(advisor_doc, dict) else base_dir.name,
        advisor_doc,
        replay_doc,
        original_netlist,
        corrected_netlist,
        agent_behavior,
        repair_effect,
        llm_backend,
        model,
        base_url,
        api_key,
        api_key_env,
    )
    deterministic_findings = [
        *agent_behavior.get("findings", []),
        *repair_effect.get("findings", []),
    ]
    scores = _score_summary(agent_behavior, repair_effect, semantic_eval)
    eval_status = _overall_status(deterministic_findings, repair_effect, semantic_eval)
    artifacts = {
        "debug_dir": {"exists": base_dir.exists(), "path": str(base_dir), "bytes": 0},
        "advisor_report": _artifact_status(Path(advisor_path) if advisor_path else None),
        "repair_replay_report": _artifact_status(Path(replay_path) if replay_path else None),
        "original_netlist": _artifact_status(original_netlist_path),
        "corrected_netlist": _artifact_status(corrected_netlist_path),
        "corrected_dxf": _artifact_status(corrected_paths.get("corrected_dxf")),
    }
    report = {
        "schema_version": EVAL_SCHEMA_VERSION,
        "case_id": advisor_doc.get("case_id") if isinstance(advisor_doc, dict) else base_dir.name,
        "strategy_name": strategy_name,
        "debug_dir": str(base_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eval_status": eval_status,
        "scores": scores,
        "agent_behavior": agent_behavior,
        "repair_effect": repair_effect,
        "semantic_eval": semantic_eval,
        "deterministic_findings": deterministic_findings,
        "artifacts": artifacts,
    }
    outputs = {}
    if write_outputs:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / EVAL_REPORT_JSON
        md_path = out_dir / EVAL_REPORT_MD
        _write_json(json_path, report)
        md_path.write_text(render_eval_report_markdown(report), encoding="utf-8")
        outputs = {
            "eval_report": str(json_path),
            "eval_report_markdown": str(md_path),
        }
    return {"report": report, "outputs": outputs}


def _case_dirs(cases_dir: Path, pattern: str) -> list[Path]:
    return sorted(
        [path for path in cases_dir.glob(pattern) if path.is_dir()],
        key=lambda path: path.name,
    )


def _status_counts(reports: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(report.get("eval_status") for report in reports))


def _finding_counts(reports: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for report in reports:
        for finding in report.get("deterministic_findings", []):
            counter[str(finding.get("code"))] += 1
        for risk in report.get("semantic_eval", {}).get("risks", []):
            counter[f"semantic:{risk}"] += 1
    return dict(counter.most_common())


def render_eval_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# Agent Eval Summary: {summary.get('strategy_name')}",
        "",
        f"- cases dir: `{summary.get('cases_dir')}`",
        f"- pattern: `{summary.get('pattern')}`",
        f"- total cases: `{summary.get('total_cases')}`",
        f"- status counts: `{summary.get('status_counts')}`",
        f"- average scores: `{summary.get('average_scores')}`",
        f"- repair applied: `{summary.get('repair_applied_count')}`",
        f"- repair improved: `{summary.get('repair_improved_count')}`",
        f"- LLM semantic eval used: `{summary.get('llm_semantic_eval_used_count')}`",
        "",
        "## Cases",
        "",
        "| case | status | overall | repair | semantic | candidate | single-pin delta |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: |",
    ]
    for case in summary.get("cases", []):
        lines.append(
            "| "
            f"{case.get('case_id')} | "
            f"{case.get('eval_status')} | "
            f"{case.get('scores', {}).get('overall_score')} | "
            f"{case.get('scores', {}).get('repair_effect_score')} | "
            f"{case.get('scores', {}).get('semantic_score')} | "
            f"{case.get('candidate_id')} | "
            f"{case.get('single_pin_delta')} |"
        )
    lines.extend(["", "## Common Findings", ""])
    if not summary.get("finding_counts"):
        lines.append("- none")
    for code, count in summary.get("finding_counts", {}).items():
        lines.append(f"- `{code}`: {count}")
    return "\n".join(lines)


def run_multi_case_eval(
    cases_dir: str | Path,
    pattern: str = "*",
    output_dir: str | Path | None = None,
    strategy_name: str = "default",
    llm_backend: str = "rule",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    max_cases: int | None = None,
) -> dict[str, Any]:
    """Evaluate many debug case directories and write an aggregate summary."""
    root = Path(cases_dir)
    out_dir = Path(output_dir) if output_dir else root / "agent_eval_summary"
    case_paths = _case_dirs(root, pattern)
    if max_cases:
        case_paths = case_paths[:max_cases]

    case_reports = []
    case_outputs = []
    for case_path in case_paths:
        case_out_dir = out_dir / "cases" / case_path.name
        result = run_single_case_eval(
            case_path,
            output_dir=case_out_dir,
            strategy_name=strategy_name,
            llm_backend=llm_backend,
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_key_env=api_key_env,
            write_outputs=True,
        )
        case_reports.append(result["report"])
        case_outputs.append(result["outputs"])

    score_keys = ["overall_score", "agent_behavior_score", "repair_effect_score", "semantic_score"]
    score_buckets: dict[str, list[float]] = defaultdict(list)
    for report in case_reports:
        for key in score_keys:
            value = report.get("scores", {}).get(key)
            if value is not None:
                score_buckets[key].append(_safe_float(value))

    compact_cases = []
    repair_applied_count = 0
    repair_improved_count = 0
    llm_used_count = 0
    for report in case_reports:
        repair = report.get("repair_effect", {})
        candidate = repair.get("candidate", {})
        improvement = repair.get("improvement", {})
        if repair.get("status") == "applied":
            repair_applied_count += 1
        if _safe_int(improvement.get("single_pin_delta")) > 0:
            repair_improved_count += 1
        if report.get("semantic_eval", {}).get("used"):
            llm_used_count += 1
        compact_cases.append(
            {
                "case_id": report.get("case_id"),
                "debug_dir": report.get("debug_dir"),
                "eval_status": report.get("eval_status"),
                "scores": report.get("scores", {}),
                "candidate_id": candidate.get("candidate_id"),
                "repair_type": candidate.get("repair_type"),
                "single_pin_delta": improvement.get("single_pin_delta"),
                "semantic_verdict": report.get("semantic_eval", {}).get("semantic_verdict"),
            }
        )

    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "strategy_name": strategy_name,
        "cases_dir": str(root),
        "pattern": pattern,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(case_reports),
        "status_counts": _status_counts(case_reports),
        "average_scores": {
            key: round(mean(values), 3) if values else None
            for key, values in score_buckets.items()
        },
        "repair_applied_count": repair_applied_count,
        "repair_improved_count": repair_improved_count,
        "llm_semantic_eval_used_count": llm_used_count,
        "finding_counts": _finding_counts(case_reports),
        "cases": compact_cases,
        "case_outputs": case_outputs,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / EVAL_SUMMARY_JSON
    md_path = out_dir / EVAL_SUMMARY_MD
    _write_json(json_path, summary)
    md_path.write_text(render_eval_summary_markdown(summary), encoding="utf-8")
    return {
        "summary": summary,
        "outputs": {
            "eval_summary": str(json_path),
            "eval_summary_markdown": str(md_path),
        },
    }
