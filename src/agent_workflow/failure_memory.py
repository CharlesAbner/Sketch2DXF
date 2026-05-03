"""Persistent failure memory for agent evaluation and future advisor runs."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MEMORY_SCHEMA_VERSION = "3.7-failure-memory"
MEMORY_OUTPUT_JSON = "failure_memory.json"
MEMORY_OUTPUT_MD = "failure_memory.md"


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _empty_memory() -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "created_at": _now(),
        "updated_at": _now(),
        "pattern_count": 0,
        "patterns": [],
        "case_index": {},
    }


def load_failure_memory(memory_file: str | Path | None) -> dict[str, Any]:
    """Load a failure memory file, returning an empty memory if missing."""
    if not memory_file:
        return _empty_memory()
    doc = _read_json(Path(memory_file))
    if not isinstance(doc, dict):
        return _empty_memory()
    doc.setdefault("schema_version", MEMORY_SCHEMA_VERSION)
    doc.setdefault("patterns", [])
    doc.setdefault("case_index", {})
    doc["pattern_count"] = len(doc.get("patterns", []))
    return doc


def _stable_pattern_id(kind: str, code: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in code.lower()).strip("_")
    return f"{kind}:{normalized or 'unknown'}"


def _case_summary_from_eval(eval_report: dict[str, Any]) -> dict[str, Any]:
    repair = eval_report.get("repair_effect", {})
    semantic = eval_report.get("semantic_eval", {})
    return {
        "case_id": eval_report.get("case_id"),
        "debug_dir": eval_report.get("debug_dir"),
        "eval_status": eval_report.get("eval_status"),
        "strategy_name": eval_report.get("strategy_name"),
        "scores": eval_report.get("scores", {}),
        "candidate_id": repair.get("candidate", {}).get("candidate_id"),
        "repair_type": repair.get("candidate", {}).get("repair_type"),
        "single_pin_delta": repair.get("improvement", {}).get("single_pin_delta"),
        "semantic_verdict": semantic.get("semantic_verdict"),
    }


def _patterns_from_eval(eval_report: dict[str, Any]) -> list[dict[str, Any]]:
    patterns = []
    case_summary = _case_summary_from_eval(eval_report)
    for finding in eval_report.get("deterministic_findings", []):
        code = str(finding.get("code") or "unknown_deterministic_finding")
        patterns.append(
            {
                "pattern_id": _stable_pattern_id("deterministic", code),
                "kind": "deterministic",
                "code": code,
                "severity": finding.get("severity", "warning"),
                "message": finding.get("message"),
                "case": case_summary,
                "suggested_actions": _suggest_actions_for_code(code),
            }
        )
    semantic = eval_report.get("semantic_eval", {})
    verdict = semantic.get("semantic_verdict")
    if verdict in {"warning", "fail", "inconclusive"}:
        semantic_code = f"semantic_{verdict}"
        patterns.append(
            {
                "pattern_id": _stable_pattern_id("semantic", semantic_code),
                "kind": "semantic",
                "code": semantic_code,
                "severity": "warning" if verdict != "fail" else "error",
                "message": semantic.get("summary"),
                "case": case_summary,
                "suggested_actions": semantic.get("next_checks", []),
            }
        )
    for idx, risk in enumerate(semantic.get("risks", []), start=1):
        code = f"semantic_risk:{risk}"
        patterns.append(
            {
                "pattern_id": _stable_pattern_id("semantic", code),
                "kind": "semantic",
                "code": code,
                "severity": "warning",
                "message": str(risk),
                "case": case_summary,
                "suggested_actions": semantic.get("next_checks", []) or [f"Review semantic risk #{idx}: {risk}"],
            }
        )
    if eval_report.get("eval_status") in {"fail", "pass_with_warnings", "needs_review"} and not patterns:
        code = f"eval_status:{eval_report.get('eval_status')}"
        patterns.append(
            {
                "pattern_id": _stable_pattern_id("eval", code),
                "kind": "eval_status",
                "code": code,
                "severity": "warning",
                "message": f"Eval status was {eval_report.get('eval_status')}.",
                "case": case_summary,
                "suggested_actions": ["Inspect agent eval report and compare before/after netlist."],
            }
        )
    return patterns


def _suggest_actions_for_code(code: str) -> list[str]:
    action_map = {
        "missing_advisor_report": ["Run agent advisor before eval summary."],
        "missing_replay_report": ["Run repair apply pending/accept before judging repair effect."],
        "applied_without_accept": ["Block apply artifacts unless approval_decision is accept."],
        "topology_mutated_in_place": ["Check apply stage; original topology must remain unchanged."],
        "corrected_export_failed": ["Inspect DXF exporter errors and corrected topology shape."],
        "component_count_changed": ["Verify repair did not drop or duplicate components."],
        "pin_count_changed": ["Verify repair did not drop or duplicate pins."],
        "zero_pin_regression": ["Review node merge; repair may have created empty nets."],
        "single_pin_regression": ["Review repair candidate ranking and unsupported evidence."],
        "single_pin_not_improved": ["Do not approve candidates that fail their target metric."],
        "two_pin_component_same_net": ["Check for shorted two-pin components after merge."],
        "all_pins_on_one_net": ["Reject repair; this likely shorted the circuit."],
        "dry_run_without_attachment_tool": ["Inspect terminal attachments before repair dry-run."],
    }
    return action_map.get(code, ["Review related artifacts and compare overlay/netlist before approval."])


def _merge_pattern(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    case = incoming.get("case", {})
    case_id = str(case.get("case_id") or "unknown")
    cases = existing.setdefault("cases", [])
    if case_id not in {str(item.get("case_id")) for item in cases}:
        cases.append(case)
    existing["count"] = int(existing.get("count", 0)) + 1
    existing["last_seen_at"] = _now()
    existing["last_message"] = incoming.get("message")
    existing["severity_counts"] = {
        **existing.get("severity_counts", {}),
        incoming.get("severity", "warning"): int(existing.get("severity_counts", {}).get(incoming.get("severity", "warning"), 0)) + 1,
    }
    suggested = existing.setdefault("suggested_actions", [])
    for action in _as_list(incoming.get("suggested_actions")):
        text = str(action)
        if text and text not in suggested:
            suggested.append(text)
    existing["example_case"] = case
    return existing


def update_failure_memory_from_eval_reports(
    eval_reports: list[dict[str, Any]],
    memory_file: str | Path | None = None,
    output_file: str | Path | None = None,
) -> dict[str, Any]:
    """Update memory from one or more agent eval reports."""
    target = Path(output_file or memory_file or Path("outputs") / "agent_memory" / MEMORY_OUTPUT_JSON)
    memory = load_failure_memory(memory_file or target)
    by_id = {str(pattern.get("pattern_id")): pattern for pattern in memory.get("patterns", [])}
    case_index = memory.setdefault("case_index", {})

    for report in eval_reports:
        case_summary = _case_summary_from_eval(report)
        if case_summary.get("case_id"):
            case_index[str(case_summary["case_id"])] = case_summary
        for incoming in _patterns_from_eval(report):
            pattern_id = str(incoming["pattern_id"])
            if pattern_id not in by_id:
                by_id[pattern_id] = {
                    "pattern_id": pattern_id,
                    "kind": incoming.get("kind"),
                    "code": incoming.get("code"),
                    "first_seen_at": _now(),
                    "last_seen_at": _now(),
                    "count": 0,
                    "cases": [],
                    "severity_counts": {},
                    "suggested_actions": [],
                }
            by_id[pattern_id] = _merge_pattern(by_id[pattern_id], incoming)

    memory["patterns"] = sorted(
        by_id.values(),
        key=lambda item: (-int(item.get("count", 0)), str(item.get("pattern_id"))),
    )
    memory["pattern_count"] = len(memory["patterns"])
    memory["updated_at"] = _now()
    _write_json(target, memory)
    md_path = target.with_suffix(".md")
    md_path.write_text(render_failure_memory_markdown(memory), encoding="utf-8")
    return {
        "memory": memory,
        "outputs": {
            "memory": str(target),
            "memory_markdown": str(md_path),
        },
    }


def _load_eval_reports_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    reports = []
    for output in summary.get("case_outputs", []):
        path = output.get("eval_report")
        if not path:
            continue
        doc = _read_json(Path(path))
        if isinstance(doc, dict):
            reports.append(doc)
    return reports


def collect_eval_reports(
    eval_report: str | Path | None = None,
    eval_summary: str | Path | None = None,
    eval_reports_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Collect eval reports from explicit report, summary, or recursive dir."""
    reports = []
    if eval_report:
        doc = _read_json(Path(eval_report))
        if isinstance(doc, dict):
            reports.append(doc)
    if eval_summary:
        summary = _read_json(Path(eval_summary))
        if isinstance(summary, dict):
            reports.extend(_load_eval_reports_from_summary(summary))
    if eval_reports_dir:
        for path in Path(eval_reports_dir).rglob("agent_eval_report.json"):
            doc = _read_json(path)
            if isinstance(doc, dict):
                reports.append(doc)

    deduped = []
    seen = set()
    for report in reports:
        key = (report.get("case_id"), report.get("strategy_name"), report.get("generated_at"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(report)
    return deduped


def _case_signals(debug_dir: Path, audit_report: dict[str, Any] | None = None) -> list[str]:
    signals = []
    case_summary = _read_json(debug_dir / "case_summary.json")
    if isinstance(case_summary, dict):
        overview = case_summary.get("issue_overview", {})
        signals.extend(str(code) for code in overview.get("risk_type_counts", {}).keys())
        signals.extend(str(code) for code in overview.get("repair_type_counts", {}).keys())
        for risk in case_summary.get("review_focus", {}).get("risks", []):
            if risk.get("code"):
                signals.append(str(risk["code"]))
    if isinstance(audit_report, dict):
        if audit_report.get("primary_issue"):
            signals.append(str(audit_report["primary_issue"]))
        semantic = audit_report.get("topology_semantic_audit", {})
        if semantic.get("single_pin_nets"):
            signals.append("single_pin_nets")
        if semantic.get("zero_pin_nets"):
            signals.append("zero_pin_nets")
        for item in audit_report.get("evidence", []):
            if item.get("code"):
                signals.append(str(item["code"]))
    return list(dict.fromkeys(signals))


def query_failure_memory(
    memory_file: str | Path | None,
    debug_dir: str | Path | None = None,
    audit_report: dict[str, Any] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Return compact memory patterns relevant to a case."""
    memory = load_failure_memory(memory_file)
    if not memory_file:
        return {
            "exists": False,
            "path": None,
            "matched_pattern_count": 0,
            "matched_patterns": [],
            "case_signals": [],
        }
    path = Path(memory_file)
    signals = _case_signals(Path(debug_dir), audit_report) if debug_dir else []
    signal_text = " ".join(signal.lower() for signal in signals)
    scored = []
    for pattern in memory.get("patterns", []):
        code = str(pattern.get("code", "")).lower()
        score = 0
        if code and code in signal_text:
            score += 3
        for signal in signals:
            if signal.lower() in code or code in signal.lower():
                score += 2
        score += min(3, int(pattern.get("count", 0)))
        if score > 0 or not signals:
            scored.append((score, pattern))
    scored.sort(key=lambda item: (-item[0], -int(item[1].get("count", 0)), str(item[1].get("pattern_id"))))
    matches = []
    for score, pattern in scored[: max(0, limit)]:
        matches.append(
            {
                "pattern_id": pattern.get("pattern_id"),
                "kind": pattern.get("kind"),
                "code": pattern.get("code"),
                "count": pattern.get("count"),
                "score": score,
                "last_message": pattern.get("last_message"),
                "suggested_actions": pattern.get("suggested_actions", [])[:5],
                "example_case": pattern.get("example_case"),
            }
        )
    return {
        "exists": path.exists(),
        "path": str(path),
        "schema_version": memory.get("schema_version"),
        "pattern_count": memory.get("pattern_count", len(memory.get("patterns", []))),
        "case_signals": signals,
        "matched_pattern_count": len(matches),
        "matched_patterns": matches,
    }


def render_failure_memory_markdown(memory: dict[str, Any]) -> str:
    lines = [
        "# Failure Memory",
        "",
        f"- schema: `{memory.get('schema_version')}`",
        f"- updated at: `{memory.get('updated_at')}`",
        f"- pattern count: `{len(memory.get('patterns', []))}`",
        f"- indexed cases: `{len(memory.get('case_index', {}))}`",
        "",
        "## Patterns",
        "",
    ]
    if not memory.get("patterns"):
        lines.append("- none")
    for pattern in memory.get("patterns", [])[:30]:
        severity_counts = pattern.get("severity_counts", {})
        lines.extend(
            [
                f"### {pattern.get('pattern_id')}",
                "",
                f"- kind/code: `{pattern.get('kind')}` / `{pattern.get('code')}`",
                f"- count: `{pattern.get('count')}`",
                f"- severity counts: `{severity_counts}`",
                f"- last message: {pattern.get('last_message')}",
                f"- example case: `{pattern.get('example_case', {}).get('case_id')}`",
                "- suggested actions:",
            ]
        )
        for action in pattern.get("suggested_actions", [])[:5]:
            lines.append(f"  - {action}")
        lines.append("")
    return "\n".join(lines)


def render_memory_query_markdown(query: dict[str, Any]) -> str:
    lines = [
        "# Failure Memory Query",
        "",
        f"- memory: `{query.get('path')}`",
        f"- exists: `{query.get('exists')}`",
        f"- case signals: `{query.get('case_signals')}`",
        f"- matched patterns: `{query.get('matched_pattern_count')}`",
        "",
    ]
    for pattern in query.get("matched_patterns", []):
        lines.extend(
            [
                f"## {pattern.get('pattern_id')}",
                "",
                f"- code: `{pattern.get('code')}`",
                f"- count: `{pattern.get('count')}`",
                f"- score: `{pattern.get('score')}`",
                f"- message: {pattern.get('last_message')}",
                "- suggested actions:",
            ]
        )
        for action in pattern.get("suggested_actions", [])[:5]:
            lines.append(f"  - {action}")
        lines.append("")
    return "\n".join(lines)


def summarize_memory(memory_file: str | Path | None) -> dict[str, Any]:
    """Return a compact summary for CLI output."""
    memory = load_failure_memory(memory_file)
    severity_counter: Counter[str] = Counter()
    for pattern in memory.get("patterns", []):
        severity_counter.update(pattern.get("severity_counts", {}))
    return {
        "schema_version": memory.get("schema_version"),
        "pattern_count": len(memory.get("patterns", [])),
        "case_count": len(memory.get("case_index", {})),
        "severity_counts": dict(severity_counter),
        "top_patterns": [
            {
                "pattern_id": pattern.get("pattern_id"),
                "code": pattern.get("code"),
                "count": pattern.get("count"),
            }
            for pattern in memory.get("patterns", [])[:8]
        ],
    }
