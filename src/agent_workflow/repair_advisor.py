"""LangGraph-native tool state machine advisor for Sketch2DXF agent 3.4."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.config import get_default_config
from src.agent_workflow.failure_memory import query_failure_memory
from src.agent_workflow.llm_client import complete_json
from src.agent_workflow.repair_dry_run import run_agent_repair_dry_run
from src.agent_workflow.workflow import run_agent_audit_workflow


SCHEMA_VERSION = "3.4-langgraph-native-tool-state-machine"
ADVISOR_OUTPUT_JSON = "agent_repair_advisor_report.json"
ADVISOR_OUTPUT_MD = "agent_repair_advisor_report.md"
DOSSIER_OUTPUT_JSON = "agent_human_review_dossier.json"
DOSSIER_OUTPUT_MD = "agent_human_review_dossier.md"
_AGENT_DEFAULTS = get_default_config().get("agent", {})
DEFAULT_MAX_AGENT_TOOL_STEPS = int(_AGENT_DEFAULTS.get("max_agent_tool_steps", 6))
DEFAULT_MAX_TOOL_CALLS_PER_STEP = int(_AGENT_DEFAULTS.get("max_tool_calls_per_step", 3))

ALLOWED_TOOL_NAMES = {
    "get_case_summary",
    "get_single_pin_nets",
    "get_terminal_attachments",
    "get_repair_candidates",
    "repair_dry_run",
}

ARTIFACT_FILES = {
    "case_summary": "case_summary.json",
    "terminal_attachments": "terminal_attachments.json",
    "repair_candidates": "repair_candidates.json",
    "evidence_graph": "evidence_graph.json",
    "supported_graph": "supported_graph.json",
    "graph_nodes_dry_run": "graph_nodes_dry_run.json",
    "topology": "topology.json",
    "netlist": "netlist.json",
    "active_nodes": "nodes.json",
}


PLANNER_SYSTEM_PROMPT = """You are the planner in a Sketch2DXF repair advisor.
You receive deterministic audit facts, a compact observation of local artifacts,
and any tool results gathered so far. Choose the next safe tool call(s). Return
JSON only.

The observation may include failure_memory: recurring failure patterns learned
from prior eval reports. Treat memory as context for what to inspect next, not
as proof that the current case has the same failure.

Available tool meanings:
- get_case_summary: read compact case-level quality/risk facts. The compact
  case summary is already present in observation, so call this only if the
  observation is missing or you need the raw compact artifact again.
- get_single_pin_nets: inspect nets with fewer than two pins and related pins.
- get_terminal_attachments: inspect terminal-to-evidence attachment candidates.
- get_repair_candidates: read deterministic repair/review candidates.
- repair_dry_run: generate validated/ranked dry-run repair candidates. It does
  not modify topology, netlist, or DXF.

Strict rules:
- You may only call tools listed in available_tools.
- You may choose no repair tool if the audit has no actionable issue.
- Do not recommend direct topology mutation.
- Prefer one focused next tool call. The workflow can call you again after the
  result comes back.
- Gather evidence before dry-run repair.
- Do not repeat tools listed in completed_tool_names.
- Tool arguments must be JSON objects.

Required JSON keys:
- tool_calls: list of {"tool_name": str, "reason": str, "arguments": object}
- planner_notes: list[str]
- stop_decision: one of continue, final_ready
- hypotheses: list[str]
- open_questions: list[str]
"""


REVIEWER_SYSTEM_PROMPT = """You are the reviewer in a Sketch2DXF repair advisor.
You receive deterministic audit facts, tool results, and critic guardrails.
Return JSON only.

Strict rules:
- Do not inspect images.
- Do not invent new measurements.
- Do not recommend directly mutating topology.
- If a candidate is useful, recommend human review/confirmation only.
- confirmed_by_artifacts must contain only facts present in the payload.

Required JSON keys:
- final_decision: one of candidate_ready_for_human_review, needs_more_evidence,
  no_candidate_found, no_action
- selected_candidate_ids: list[str]
- rationale: str
- confirmed_by_artifacts: list[str]
- risks: list[str]
- next_actions: list[str]
"""


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _model_to_dict(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.dict()
    if isinstance(value, list):
        return [_model_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: _model_to_dict(item) for key, item in value.items()}
    return value


def _scrub_secrets(value: Any) -> Any:
    if isinstance(value, list):
        return [_scrub_secrets(item) for item in value]
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            if "api_key" in str(key).lower():
                scrubbed[key] = "<redacted>" if item else None
            else:
                scrubbed[key] = _scrub_secrets(item)
        return scrubbed
    return value


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item) for item in value]


def _as_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    result = []
    seen = set()
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _artifact_path(debug_dir: Path, artifact_name: str) -> Path:
    return debug_dir / ARTIFACT_FILES[artifact_name]


def _artifact_presence(debug_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "exists": _artifact_path(debug_dir, name).exists(),
            "path": str(_artifact_path(debug_dir, name)),
        }
        for name in ARTIFACT_FILES
    }


def _compact_audit(audit_report: dict[str, Any] | None) -> dict[str, Any]:
    if not audit_report:
        return {
            "exists": False,
        }
    return {
        "exists": True,
        "case_id": audit_report.get("case_id"),
        "overall_status": audit_report.get("overall_status"),
        "primary_issue": audit_report.get("primary_issue"),
        "suspected_stage": audit_report.get("suspected_stage"),
        "confidence": audit_report.get("confidence"),
        "known_stressors": audit_report.get("known_stressors", []),
        "semantic_audit": audit_report.get("topology_semantic_audit", {}),
        "stage_diagnoses": audit_report.get("stage_diagnoses", []),
        "evidence": audit_report.get("evidence", []),
        "recommended_actions": audit_report.get("recommended_actions", []),
    }


def _compact_case_summary(case_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not case_summary:
        return {
            "exists": False,
        }
    review_focus = case_summary.get("review_focus", {})
    return {
        "exists": True,
        "schema_version": case_summary.get("schema_version"),
        "case_id": case_summary.get("case_id"),
        "image_path": case_summary.get("image_path"),
        "review_status": case_summary.get("review_status"),
        "agent_ready": case_summary.get("agent_ready"),
        "topology_mutated_by_repair": case_summary.get("topology_mutated_by_repair"),
        "summary": case_summary.get("summary", {}),
        "issue_overview": case_summary.get("issue_overview", {}),
        "risks": review_focus.get("risks", [])[:8],
        "repair_candidates": review_focus.get("repair_candidates", [])[:8],
        "artifacts": case_summary.get("artifacts", {}),
    }


def _candidate_snapshot(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id") or candidate.get("repair_candidate_id"),
        "repair_type": candidate.get("repair_type") or candidate.get("issue_type"),
        "summary": candidate.get("summary") or candidate.get("rationale"),
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
        "ranking_score": candidate.get("ranking_score"),
        "validation_result": candidate.get("validation_result"),
        "recommendation": candidate.get("recommendation") or candidate.get("recommended_action"),
        "risk_level": candidate.get("risk_level") or candidate.get("severity"),
        "status": candidate.get("status"),
        "target_nodes": candidate.get("target_nodes", []),
        "target_pins": candidate.get("target_pins", []),
        "refs": candidate.get("refs", {}),
        "improved_metrics": candidate.get("improved_metrics", []),
        "regressed_metrics": candidate.get("regressed_metrics", []),
        "blocking_issues": candidate.get("blocking_issues", []),
        "reasons": candidate.get("reasons", [])[:5],
    }


def _candidate_matches_filter(
    candidate: dict[str, Any],
    target_net_ids: list[str],
    target_pin_ids: list[str],
    repair_type: str | None,
) -> bool:
    if repair_type:
        candidate_type = str(candidate.get("repair_type") or "")
        if repair_type == "merge" and candidate_type != "merge_nodes":
            return False
        if repair_type not in {"merge"} and candidate_type != repair_type:
            return False
    if target_net_ids:
        target_nodes = {str(item) for item in candidate.get("target_nodes", [])}
        if not target_nodes.intersection(target_net_ids):
            return False
    if target_pin_ids:
        pins = {str(item) for item in candidate.get("target_pins", [])}
        if not pins.intersection(target_pin_ids):
            return False
    return True


def _compact_repair_report(
    repair_report: dict[str, Any] | None,
    target_net_ids: list[str] | None = None,
    target_pin_ids: list[str] | None = None,
    repair_type: str | None = None,
) -> dict[str, Any]:
    if not repair_report:
        return {
            "exists": False,
        }
    candidates = repair_report.get("repair_candidates", [])
    filtered_candidates = candidates
    active_filter = bool(target_net_ids or target_pin_ids or repair_type)
    if active_filter:
        filtered_candidates = [
            candidate
            for candidate in candidates
            if _candidate_matches_filter(
                candidate,
                target_net_ids or [],
                target_pin_ids or [],
                repair_type,
            )
        ]
    return {
        "exists": True,
        "schema_version": repair_report.get("schema_version"),
        "case_id": repair_report.get("case_id"),
        "topology_mutated": repair_report.get("topology_mutated"),
        "selected_repair_tools": repair_report.get("selected_repair_tools", []),
        "validation_summary": repair_report.get("validation_summary", {}),
        "recommended_next_step": repair_report.get("recommended_next_step"),
        "filter": {
            "active": active_filter,
            "target_net_ids": target_net_ids or [],
            "target_pin_ids": target_pin_ids or [],
            "repair_type": repair_type,
            "unfiltered_candidate_count": len(candidates),
            "filtered_candidate_count": len(filtered_candidates),
        },
        "top_candidates": [_candidate_snapshot(candidate) for candidate in filtered_candidates[:8]],
        "protected_outputs": repair_report.get("protected_outputs", []),
    }


def _compact_repair_candidates_doc(doc: dict[str, Any] | None) -> dict[str, Any]:
    if not doc:
        return {
            "exists": False,
        }
    candidates = doc.get("candidates", [])
    return {
        "exists": True,
        "schema_version": doc.get("schema_version"),
        "topology_mutated": doc.get("topology_mutated"),
        "summary": doc.get("summary", {}),
        "candidates": [_candidate_snapshot(candidate) for candidate in candidates[:12]],
        "config": doc.get("config", {}),
    }


def _single_pin_net_ids_from_audit(audit_report: dict[str, Any] | None) -> list[str]:
    if not audit_report:
        return []
    ids = []
    semantic = audit_report.get("topology_semantic_audit", {})
    ids.extend(_as_id_list(semantic.get("single_pin_nets")))
    for item in audit_report.get("evidence", []):
        refs = item.get("refs", {})
        ids.extend(_as_id_list(refs.get("net_ids")))
        ids.extend(_as_id_list(refs.get("net_id")))
    for action in audit_report.get("recommended_actions", []):
        refs = action.get("target_refs", {})
        ids.extend(_as_id_list(refs.get("net_ids")))
        ids.extend(_as_id_list(refs.get("net_id")))
    return _as_id_list(ids)


def _pin_ids_from_audit(audit_report: dict[str, Any] | None) -> list[str]:
    if not audit_report:
        return []
    ids = []
    for item in audit_report.get("evidence", []):
        refs = item.get("refs", {})
        ids.extend(_as_id_list(refs.get("pin_ids")))
        ids.extend(_as_id_list(refs.get("pin_id")))
    for action in audit_report.get("recommended_actions", []):
        refs = action.get("target_refs", {})
        ids.extend(_as_id_list(refs.get("pin_ids")))
        ids.extend(_as_id_list(refs.get("pin_id")))
    return _as_id_list(ids)


def _pin_ids_for_net_ids(debug_dir: Path, net_ids: list[str]) -> list[str]:
    net_id_set = set(net_ids)
    if not net_id_set:
        return []
    netlist = _read_json(_artifact_path(debug_dir, "netlist"))
    if not isinstance(netlist, dict):
        return []
    pin_ids = []
    for component in netlist.get("components", []):
        for pin in component.get("pins", []):
            if pin.get("net_id") in net_id_set and pin.get("pin_id"):
                pin_ids.append(str(pin["pin_id"]))
    return _as_id_list(pin_ids)


def _single_pin_net_summary(
    debug_dir: Path,
    audit_report: dict[str, Any] | None,
    requested_net_ids: list[str] | None = None,
) -> dict[str, Any]:
    net_ids = requested_net_ids or _single_pin_net_ids_from_audit(audit_report)
    net_id_set = set(net_ids)
    netlist = _read_json(_artifact_path(debug_dir, "netlist"))
    if not isinstance(netlist, dict):
        return {
            "exists": False,
            "net_ids": net_ids,
            "nets": [],
            "related_pin_ids": [],
        }

    nets = []
    for net in netlist.get("nets", []):
        net_id = str(net.get("net_id"))
        if net_id_set and net_id not in net_id_set:
            continue
        if not net_id_set and int(net.get("pin_count") or 0) >= 2:
            continue
        nets.append(
            {
                "net_id": net_id,
                "pin_count": net.get("pin_count"),
                "pin_refs": net.get("pin_refs", []),
                "component_refs": net.get("component_refs", []),
            }
        )

    related_pin_ids = _pin_ids_for_net_ids(debug_dir, [item["net_id"] for item in nets])
    pin_details = []
    related_set = set(related_pin_ids)
    for component in netlist.get("components", []):
        for pin in component.get("pins", []):
            if pin.get("pin_id") in related_set:
                pin_details.append(
                    {
                        "pin_id": pin.get("pin_id"),
                        "pin_ref": pin.get("pin_ref"),
                        "component_id": component.get("component_id"),
                        "refdes": component.get("refdes"),
                        "class_name": component.get("class_name"),
                        "side": pin.get("side"),
                        "net_id": pin.get("net_id"),
                    }
                )

    return {
        "exists": True,
        "requested_net_ids": net_ids,
        "single_pin_net_count": len(nets),
        "nets": nets,
        "related_pin_ids": related_pin_ids,
        "pin_details": pin_details,
    }


def _attachment_snapshot(attachment: dict[str, Any], candidate_limit: int = 3) -> dict[str, Any]:
    return {
        "pin_id": attachment.get("pin_id"),
        "component_id": attachment.get("component_id"),
        "side": attachment.get("side"),
        "axis": attachment.get("axis"),
        "candidate_count": attachment.get("candidate_count"),
        "best_attachment_id": attachment.get("best_attachment_id"),
        "best_raw_component_id": attachment.get("best_raw_component_id"),
        "best_evidence_kind": attachment.get("best_evidence_kind"),
        "best_evidence_id": attachment.get("best_evidence_id"),
        "best_attachment_score": attachment.get("best_attachment_score"),
        "candidates": [
            {
                "attachment_id": candidate.get("attachment_id"),
                "evidence_kind": candidate.get("evidence_kind"),
                "evidence_id": candidate.get("evidence_id"),
                "raw_component_id": candidate.get("raw_component_id"),
                "attachment_type": candidate.get("attachment_type"),
                "in_corridor": candidate.get("in_corridor"),
                "distance": candidate.get("distance"),
                "forward_distance": candidate.get("forward_distance"),
                "lateral_distance": candidate.get("lateral_distance"),
                "attachment_score": candidate.get("attachment_score"),
                "evidence_score": candidate.get("evidence_score"),
                "keep_reasons": candidate.get("keep_reasons", []),
            }
            for candidate in attachment.get("candidates", [])[:candidate_limit]
        ],
    }


def _compact_terminal_attachments(
    doc: dict[str, Any] | None,
    pin_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not doc:
        return {
            "exists": False,
        }
    pin_id_set = set(pin_ids or [])
    attachments = []
    for attachment in doc.get("attachments", []):
        if pin_id_set and attachment.get("pin_id") not in pin_id_set:
            continue
        attachments.append(_attachment_snapshot(attachment))
    return {
        "exists": True,
        "requested_pin_ids": pin_ids or [],
        "attachment_count": len(attachments),
        "attachments": attachments[:16],
    }


def _available_tools() -> list[dict[str, Any]]:
    return [
        {
            "tool_name": "get_case_summary",
            "kind": "read_only",
            "description": "Read compact case-level quality, risk, and artifact summary.",
        },
        {
            "tool_name": "get_single_pin_nets",
            "kind": "read_only",
            "description": "Inspect single-pin nets and map them to pin/component identifiers.",
        },
        {
            "tool_name": "get_terminal_attachments",
            "kind": "read_only",
            "description": "Inspect terminal corridor attachment evidence for target pins or nets.",
        },
        {
            "tool_name": "get_repair_candidates",
            "kind": "read_only",
            "description": "Read deterministic review/repair candidates generated by the core pipeline.",
        },
        {
            "tool_name": "repair_dry_run",
            "kind": "dry_run",
            "description": "Generate, validate, and rank possible repairs without mutating topology outputs.",
        },
    ]


def _build_observation(
    debug_dir: Path,
    audit_report: dict[str, Any] | None,
    memory_file: str | None = None,
    memory_limit: int = 5,
) -> dict[str, Any]:
    case_summary = _read_json(_artifact_path(debug_dir, "case_summary"))
    return {
        "case_summary": _compact_case_summary(case_summary if isinstance(case_summary, dict) else None),
        "audit": _compact_audit(audit_report),
        "artifact_presence": _artifact_presence(debug_dir),
        "available_tools": _available_tools(),
        "failure_memory": query_failure_memory(
            memory_file,
            debug_dir=debug_dir,
            audit_report=audit_report,
            limit=memory_limit,
        ),
    }


def _audit_has_actionable_issues(audit_report: dict[str, Any] | None) -> bool:
    if not audit_report:
        return False
    if audit_report.get("overall_status") in {"needs_review", "fail"}:
        return True
    semantic = audit_report.get("topology_semantic_audit", {})
    if semantic.get("single_pin_nets") or semantic.get("zero_pin_nets"):
        return True
    return bool(audit_report.get("recommended_actions"))


def _audit_has_unsupported_evidence(audit_report: dict[str, Any] | None) -> bool:
    if not audit_report:
        return False
    for item in audit_report.get("evidence", []):
        if item.get("code") == "unsupported_evidence":
            return True
    for action in audit_report.get("recommended_actions", []):
        if action.get("action_type") == "review_unsupported_evidence":
            return True
    return False


def _audit_has_terminal_attachment_issue(audit_report: dict[str, Any] | None) -> bool:
    if not audit_report:
        return False
    if audit_report.get("suspected_stage") == "terminal_attachment":
        return True
    for item in audit_report.get("evidence", []):
        if item.get("code") in {"low_confidence_matches", "ambiguous_pin_match"}:
            return True
    for action in audit_report.get("recommended_actions", []):
        if action.get("action_type") == "inspect_terminal_attachments":
            return True
    return False


def _positive_int_or_default(value: int | None, default: int) -> int:
    if value is None:
        return default
    return max(1, int(value))


def _needs_repair_prerequisite_tools(
    audit_report: dict[str, Any] | None,
    completed_tool_names: set[str],
) -> list[str]:
    required = []
    if _single_pin_net_ids_from_audit(audit_report):
        if "get_single_pin_nets" not in completed_tool_names:
            required.append("get_single_pin_nets")
        if "get_terminal_attachments" not in completed_tool_names:
            required.append("get_terminal_attachments")
    if _audit_has_terminal_attachment_issue(audit_report) and "get_terminal_attachments" not in completed_tool_names:
        required.append("get_terminal_attachments")
    return _as_id_list(required)


def _rule_planner(
    audit_report: dict[str, Any] | None,
    observation: dict[str, Any],
    completed_tool_names: set[str] | None = None,
    planner_feedback: list[str] | None = None,
    max_tool_calls_per_step: int = DEFAULT_MAX_TOOL_CALLS_PER_STEP,
    reason: str | None = None,
) -> dict[str, Any]:
    audit = _compact_audit(audit_report)
    completed_tool_names = completed_tool_names or set()
    tool_calls: list[dict[str, Any]] = []
    notes: list[str] = []

    def add_tool(tool_name: str, tool_reason: str, arguments: dict[str, Any] | None = None) -> None:
        if tool_name not in ALLOWED_TOOL_NAMES:
            return
        if tool_name in completed_tool_names:
            return
        if any(call.get("tool_name") == tool_name for call in tool_calls):
            return
        tool_calls.append(
            {
                "tool_name": tool_name,
                "reason": tool_reason,
                "arguments": arguments or {},
            }
        )

    if not audit.get("exists"):
        if not observation.get("case_summary", {}).get("exists"):
            add_tool("get_case_summary", "Audit is missing; read compact case summary if available.")
        notes.append("No audit report is available; only observation tools are safe.")
    elif audit.get("primary_issue") == "insufficient_artifacts":
        notes.append("Audit reports insufficient artifacts, so repair tools should not run yet.")
    else:
        single_pin_net_ids = _single_pin_net_ids_from_audit(audit_report)
        if single_pin_net_ids:
            notes.append(f"Single-pin nets: {', '.join(single_pin_net_ids)}.")
            if "get_single_pin_nets" not in completed_tool_names:
                add_tool(
                    "get_single_pin_nets",
                    "Audit found single-pin nets; inspect affected nets and pins.",
                    {"net_ids": single_pin_net_ids},
                )
            if "get_terminal_attachments" not in completed_tool_names:
                add_tool(
                    "get_terminal_attachments",
                    "Inspect terminal attachment evidence around the single-pin nets.",
                    {"net_ids": single_pin_net_ids},
                )

        if _audit_has_terminal_attachment_issue(audit_report):
            pin_ids = _pin_ids_from_audit(audit_report)
            notes.append(
                f"Terminal attachment issue targets pins: {', '.join(pin_ids) if pin_ids else 'all relevant pins'}."
            )
            add_tool(
                "get_terminal_attachments",
                "Audit points to terminal attachment confidence; inspect target pin evidence.",
                {"pin_ids": pin_ids},
            )

        if _audit_has_unsupported_evidence(audit_report):
            notes.append("Unsupported evidence should be reviewed before accepting a topology repair.")
            add_tool(
                "get_repair_candidates",
                "Audit mentions unsupported evidence; read deterministic review candidates.",
            )

        if (
            _audit_has_actionable_issues(audit_report)
            and not _needs_repair_prerequisite_tools(audit_report, completed_tool_names)
            and "repair_dry_run" not in completed_tool_names
        ):
            repair_arguments: dict[str, Any] = {}
            if single_pin_net_ids:
                repair_arguments["net_ids"] = single_pin_net_ids
                repair_arguments["repair_type"] = "merge"
            add_tool(
                "repair_dry_run",
                "Actionable audit issues exist; generate non-mutating repair candidates.",
                repair_arguments,
            )
            notes.append(f"Audit status is {audit.get('overall_status')}.")

    case_status = observation.get("case_summary", {}).get("review_status")
    if case_status and case_status != "pass":
        notes.append(f"Case summary review_status is {case_status}.")
    if planner_feedback:
        notes.extend(planner_feedback[-3:])
    if reason:
        notes.append(reason)
    if not notes:
        notes.append("No actionable repair tool call selected.")

    return {
        "planner_kind": "rule",
        "llm_used": False,
        "tool_calls": tool_calls[:max_tool_calls_per_step],
        "planner_notes": notes,
        "stop_decision": "continue" if tool_calls else "final_ready",
        "hypotheses": [],
        "open_questions": [],
        "guardrail_feedback": [],
        "llm_result": {
            "used": False,
            "error": None,
        },
    }


def _normalize_tool_calls(
    raw_calls: Any,
    completed_tool_names: set[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_calls, list):
        return []
    completed_tool_names = completed_tool_names or set()
    result = []
    seen = set()
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name", "")).strip()
        if tool_name not in ALLOWED_TOOL_NAMES or tool_name in seen or tool_name in completed_tool_names:
            continue
        arguments = item.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        seen.add(tool_name)
        result.append(
            {
                "tool_name": tool_name,
                "reason": str(item.get("reason") or "Planner selected this tool."),
                "arguments": arguments,
            }
        )
        if limit and len(result) >= limit:
            break
    return result


def _guard_planned_tool_calls(
    proposed: list[dict[str, Any]],
    audit_report: dict[str, Any] | None,
    completed_tool_names: set[str],
    max_tool_calls_per_step: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    guarded = []
    feedback = []
    prerequisites = _needs_repair_prerequisite_tools(audit_report, completed_tool_names)
    for call in proposed:
        tool_name = call.get("tool_name")
        if tool_name == "repair_dry_run" and prerequisites:
            feedback.append(
                "Blocked repair_dry_run until prerequisite evidence tools complete: "
                + ", ".join(prerequisites)
                + "."
            )
            continue
        guarded.append(call)
    return guarded[:max_tool_calls_per_step], feedback


def _plan_tool_calls(
    audit_report: dict[str, Any] | None,
    observation: dict[str, Any],
    tool_results: list[dict[str, Any]],
    completed_tool_names: set[str],
    planner_feedback: list[str],
    iteration: int,
    max_tool_calls_per_step: int,
    backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> dict[str, Any]:
    if backend in {"rule", "mock"}:
        planner = _rule_planner(
            audit_report,
            observation,
            completed_tool_names,
            planner_feedback,
            max_tool_calls_per_step,
            reason="Mock backend requested rule-constrained tool planning." if backend == "mock" else None,
        )
        planner["planner_kind"] = backend
        planner["iteration"] = iteration
        planner["llm_used"] = backend == "mock"
        if backend == "mock":
            planner["llm_result"] = {
                "used": True,
                "backend": "mock",
                "model": "mock",
                "error": None,
            }
        return planner

    payload = {
        "available_tools": _available_tools(),
        "observation": observation,
        "audit_report": _compact_audit(audit_report),
        "completed_tool_names": sorted(completed_tool_names),
        "prior_tool_results": tool_results,
        "planner_feedback": planner_feedback,
        "iteration": iteration,
    }
    llm_result = complete_json(
        PLANNER_SYSTEM_PROMPT,
        payload,
        backend=backend,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )
    if not llm_result.get("used") or llm_result.get("error"):
        fallback = _rule_planner(
            audit_report,
            observation,
            completed_tool_names,
            planner_feedback,
            max_tool_calls_per_step,
            reason="LLM planner unavailable; used rule fallback.",
        )
        fallback["planner_kind"] = "rule_fallback"
        fallback["iteration"] = iteration
        fallback["llm_result"] = llm_result
        return fallback

    content = llm_result.get("content", {}) if isinstance(llm_result.get("content"), dict) else {}
    tool_calls = _normalize_tool_calls(
        content.get("tool_calls"),
        completed_tool_names=completed_tool_names,
        limit=max_tool_calls_per_step,
    )
    tool_calls, guard_feedback = _guard_planned_tool_calls(
        tool_calls,
        audit_report,
        completed_tool_names,
        max_tool_calls_per_step,
    )
    planner_notes = content.get("planner_notes", [])
    if not isinstance(planner_notes, list):
        planner_notes = [str(planner_notes)]
    planner_notes.extend(guard_feedback)
    stop_decision = str(content.get("stop_decision") or ("continue" if tool_calls else "final_ready"))
    if stop_decision not in {"continue", "final_ready"}:
        stop_decision = "continue" if tool_calls else "final_ready"
    return {
        "planner_kind": "llm",
        "iteration": iteration,
        "llm_used": True,
        "tool_calls": tool_calls,
        "planner_notes": planner_notes,
        "stop_decision": stop_decision,
        "hypotheses": _as_string_list(content.get("hypotheses")),
        "open_questions": _as_string_list(content.get("open_questions")),
        "guardrail_feedback": guard_feedback,
        "llm_result": llm_result,
    }


def _execute_named_tool(
    debug_dir: Path,
    output_dir: Path,
    audit_report: dict[str, Any] | None,
    call: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    tool_name = call.get("tool_name")
    arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    base_result = {
        "tool_call_id": f"TC{index}",
        "tool_name": tool_name,
        "status": "completed",
        "reason": call.get("reason"),
        "arguments": arguments,
        "argument_grounding": call.get(
            "argument_grounding",
            {
                "applied": False,
                "source": None,
                "added_arguments": {},
            },
        ),
    }
    if tool_name not in ALLOWED_TOOL_NAMES:
        return {
            **base_result,
            "status": "skipped",
            "result_summary": {
                "exists": False,
                "error": "Tool is not allowed.",
            },
        }

    if tool_name == "get_case_summary":
        doc = _read_json(_artifact_path(debug_dir, "case_summary"))
        return {
            **base_result,
            "result_summary": _compact_case_summary(doc if isinstance(doc, dict) else None),
        }

    if tool_name == "get_single_pin_nets":
        net_ids = _as_id_list(arguments.get("net_ids")) or _single_pin_net_ids_from_audit(audit_report)
        return {
            **base_result,
            "result_summary": _single_pin_net_summary(debug_dir, audit_report, net_ids),
        }

    if tool_name == "get_terminal_attachments":
        pin_ids = _as_id_list(arguments.get("pin_ids"))
        net_ids = _as_id_list(arguments.get("net_ids"))
        if not pin_ids and net_ids:
            pin_ids = _pin_ids_for_net_ids(debug_dir, net_ids)
        doc = _read_json(_artifact_path(debug_dir, "terminal_attachments"))
        return {
            **base_result,
            "result_summary": _compact_terminal_attachments(doc if isinstance(doc, dict) else None, pin_ids),
        }

    if tool_name == "get_repair_candidates":
        doc = _read_json(_artifact_path(debug_dir, "repair_candidates"))
        return {
            **base_result,
            "result_summary": _compact_repair_candidates_doc(doc if isinstance(doc, dict) else None),
        }

    tool_output_dir = output_dir / "tool_calls" / f"{index:02d}_repair_dry_run"
    tool_result = run_agent_repair_dry_run(debug_dir, output_dir=tool_output_dir)
    repair_report = tool_result.get("report", {})
    return {
        **base_result,
        "outputs": tool_result.get("outputs", {}),
        "result_summary": _compact_repair_report(
            repair_report,
            target_net_ids=_as_id_list(arguments.get("net_ids")),
            target_pin_ids=_as_id_list(arguments.get("pin_ids")),
            repair_type=str(arguments.get("repair_type")) if arguments.get("repair_type") else None,
        ),
    }


def _target_context_from_tool_results(
    audit_report: dict[str, Any] | None,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    net_ids = []
    pin_ids = []
    sources = []

    for result in tool_results:
        summary = result.get("result_summary", {})
        tool_name = result.get("tool_name")
        if tool_name == "get_single_pin_nets" and summary.get("exists"):
            before_nets = len(net_ids)
            before_pins = len(pin_ids)
            net_ids.extend(_as_id_list(summary.get("requested_net_ids")))
            net_ids.extend(
                _as_id_list([item.get("net_id") for item in summary.get("nets", []) if item.get("net_id")])
            )
            pin_ids.extend(_as_id_list(summary.get("related_pin_ids")))
            if len(net_ids) > before_nets or len(pin_ids) > before_pins:
                sources.append("get_single_pin_nets")
        elif tool_name == "get_terminal_attachments" and summary.get("exists"):
            before_pins = len(pin_ids)
            pin_ids.extend(_as_id_list(summary.get("requested_pin_ids")))
            if len(pin_ids) > before_pins:
                sources.append("get_terminal_attachments")

    if not net_ids:
        audit_net_ids = _single_pin_net_ids_from_audit(audit_report)
        if audit_net_ids:
            net_ids.extend(audit_net_ids)
            sources.append("audit_single_pin_nets")
    if not pin_ids and net_ids:
        # Pin ids are resolved by the concrete tool when needed; keep this context compact.
        sources.append("net_ids_only")

    repair_type = None
    if net_ids and _single_pin_net_ids_from_audit(audit_report):
        repair_type = "merge"

    return {
        "net_ids": _as_id_list(net_ids),
        "pin_ids": _as_id_list(pin_ids),
        "repair_type": repair_type,
        "sources": _as_id_list(sources),
    }


def _ground_tool_call_arguments(
    call: dict[str, Any],
    audit_report: dict[str, Any] | None,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    grounded = dict(call)
    arguments = dict(call.get("arguments") if isinstance(call.get("arguments"), dict) else {})
    added: dict[str, Any] = {}
    context = _target_context_from_tool_results(audit_report, tool_results)
    tool_name = grounded.get("tool_name")

    if tool_name == "get_terminal_attachments":
        if not _as_id_list(arguments.get("pin_ids")) and not _as_id_list(arguments.get("net_ids")):
            if context["net_ids"]:
                arguments["net_ids"] = context["net_ids"]
                added["net_ids"] = context["net_ids"]
            elif context["pin_ids"]:
                arguments["pin_ids"] = context["pin_ids"]
                added["pin_ids"] = context["pin_ids"]

    if tool_name == "repair_dry_run":
        if not _as_id_list(arguments.get("net_ids")) and not _as_id_list(arguments.get("pin_ids")):
            if context["net_ids"]:
                arguments["net_ids"] = context["net_ids"]
                added["net_ids"] = context["net_ids"]
            elif context["pin_ids"]:
                arguments["pin_ids"] = context["pin_ids"]
                added["pin_ids"] = context["pin_ids"]
        if not arguments.get("repair_type") and context.get("repair_type"):
            arguments["repair_type"] = context["repair_type"]
            added["repair_type"] = context["repair_type"]

    grounding = {
        "applied": bool(added),
        "source": ("prior_tool_results" if tool_results else "audit_context") if added else None,
        "source_tools": context["sources"] if added else [],
        "added_arguments": added,
    }
    grounded["arguments"] = arguments
    grounded["argument_grounding"] = grounding
    return grounded


def _invoke_tools(
    debug_dir: Path,
    output_dir: Path,
    audit_report: dict[str, Any] | None,
    tool_calls: list[dict[str, Any]],
    start_index: int = 1,
    planner_iteration: int | None = None,
) -> list[dict[str, Any]]:
    results = []
    for index, call in enumerate(tool_calls, start=start_index):
        try:
            result = _execute_named_tool(debug_dir, output_dir, audit_report, call, index)
            result["planner_iteration"] = planner_iteration
            results.append(result)
        except Exception as exc:  # Keep the agent trace inspectable on tool failure.
            results.append(
                {
                    "tool_call_id": f"TC{index}",
                    "tool_name": call.get("tool_name"),
                    "planner_iteration": planner_iteration,
                    "status": "failed",
                    "reason": call.get("reason"),
                    "arguments": call.get("arguments", {}),
                    "result_summary": {
                        "exists": False,
                        "error": str(exc),
                    },
                }
            )
    return results


def _completed_tool_names(tool_results: list[dict[str, Any]]) -> set[str]:
    return {
        str(result.get("tool_name"))
        for result in tool_results
        if result.get("status") == "completed" and result.get("tool_name")
    }


def _aggregate_planner_steps(planner_steps: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
    tool_calls = []
    notes = []
    llm_used = False
    llm_results = []
    planner_kinds = []
    hypotheses = []
    open_questions = []
    guardrail_feedback = []
    for step in planner_steps:
        planner_kinds.append(str(step.get("planner_kind")))
        tool_calls.extend(step.get("tool_calls", []))
        notes.extend(step.get("planner_notes", []))
        hypotheses.extend(step.get("hypotheses", []))
        open_questions.extend(step.get("open_questions", []))
        guardrail_feedback.extend(step.get("guardrail_feedback", []))
        llm_used = bool(llm_used or step.get("llm_used"))
        if step.get("llm_result"):
            llm_results.append(step.get("llm_result"))
    if not planner_steps:
        planner_kinds.append("none")
    return {
        "planner_kind": "+".join(dict.fromkeys(planner_kinds)),
        "llm_used": llm_used,
        "tool_calls": tool_calls,
        "planner_notes": notes,
        "planner_steps": planner_steps,
        "stop_reason": stop_reason,
        "hypotheses": _as_string_list(hypotheses),
        "open_questions": _as_string_list(open_questions),
        "guardrail_feedback": _as_string_list(guardrail_feedback),
        "llm_result": llm_results[-1] if llm_results else {"used": False, "error": None},
        "llm_results": llm_results,
    }


def _record_graph_event(
    state: dict[str, Any],
    step: str,
    status: str = "completed",
    summary: dict[str, Any] | None = None,
) -> None:
    trace = list(state.get("graph_trace", []))
    trace.append(
        {
            "step": step,
            "status": status,
            "summary": summary or {},
        }
    )
    state["graph_trace"] = trace


def _run_planner_tool_loop(
    debug_dir: Path,
    output_dir: Path,
    audit_report: dict[str, Any] | None,
    observation: dict[str, Any],
    max_agent_tool_steps: int,
    max_tool_calls_per_step: int,
    backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    planner_steps: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    planner_feedback: list[str] = []
    stop_reason = "max_steps_reached"

    for iteration in range(1, max_agent_tool_steps + 1):
        planner = _plan_tool_calls(
            audit_report,
            observation,
            tool_results,
            _completed_tool_names(tool_results),
            planner_feedback,
            iteration,
            max_tool_calls_per_step,
            backend,
            model,
            base_url,
            api_key,
            api_key_env,
        )
        planner_feedback.extend(planner.get("guardrail_feedback", []))
        raw_calls = planner.get("tool_calls", [])
        grounded_calls = [
            _ground_tool_call_arguments(call, audit_report, tool_results)
            for call in raw_calls
        ]
        planner["raw_tool_calls"] = raw_calls
        planner["tool_calls"] = grounded_calls
        planner_steps.append(planner)
        calls = planner.get("tool_calls", [])
        if not calls:
            if planner.get("guardrail_feedback") and iteration < max_agent_tool_steps:
                continue
            stop_reason = (
                "planner_declared_final_ready"
                if planner.get("stop_decision") == "final_ready"
                else "planner_selected_no_more_tools"
            )
            break

        new_results = _invoke_tools(
            debug_dir,
            output_dir,
            audit_report,
            calls,
            start_index=len(tool_results) + 1,
            planner_iteration=iteration,
        )
        tool_results.extend(new_results)
        if any(result.get("tool_name") == "repair_dry_run" for result in new_results):
            stop_reason = "repair_dry_run_completed"
            break

    return _aggregate_planner_steps(planner_steps, stop_reason), tool_results


def _known_candidate_ids(tool_results: list[dict[str, Any]]) -> set[str]:
    ids = set()
    for result in tool_results:
        for key in ("top_candidates", "candidates"):
            for candidate in result.get("result_summary", {}).get(key, []):
                if candidate.get("candidate_id"):
                    ids.add(str(candidate.get("candidate_id")))
    return ids


def _top_candidate(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for result in tool_results:
        candidates.extend(result.get("result_summary", {}).get("top_candidates", []))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: int(item.get("rank") or 999999))[0]


def _repair_dry_run_called(tool_results: list[dict[str, Any]]) -> bool:
    return any(result.get("tool_name") == "repair_dry_run" for result in tool_results)


def _critic_review(
    audit_report: dict[str, Any] | None,
    planner: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    called_names = {call.get("tool_name") for call in planner.get("tool_calls", [])}
    result_names = {result.get("tool_name") for result in tool_results if result.get("status") == "completed"}
    issues = []
    notes = []

    if _audit_has_actionable_issues(audit_report) and "repair_dry_run" not in called_names:
        issues.append("Actionable audit issues exist but repair_dry_run was not planned.")
    if _single_pin_net_ids_from_audit(audit_report) and "get_single_pin_nets" not in called_names:
        issues.append("Single-pin nets exist but get_single_pin_nets was not planned.")
    if _audit_has_terminal_attachment_issue(audit_report) and "get_terminal_attachments" not in called_names:
        issues.append("Terminal attachment issue exists but get_terminal_attachments was not planned.")
    if called_names - result_names:
        issues.append(f"Some planned tools did not complete: {', '.join(sorted(called_names - result_names))}.")

    for result in tool_results:
        if result.get("status") == "failed":
            issues.append(f"Tool {result.get('tool_name')} failed: {result.get('result_summary', {}).get('error')}")
        summary = result.get("result_summary", {})
        if summary.get("topology_mutated"):
            issues.append(f"Tool {result.get('tool_name')} reports topology_mutated=true.")

    if not issues:
        notes.append("All selected tools completed and no topology mutation was reported.")
    if _top_candidate(tool_results):
        notes.append("A ranked dry-run repair candidate is available for reviewer assessment.")
    elif _repair_dry_run_called(tool_results):
        notes.append("Repair dry-run completed but did not produce an accepted top candidate.")

    return {
        "critic_kind": "rule_guardrail",
        "llm_used": False,
        "guardrail_status": "pass" if not issues else "warning",
        "continue_recommended": False,
        "issues": issues,
        "notes": notes,
    }


def _rule_review(
    audit_report: dict[str, Any] | None,
    planner: dict[str, Any],
    tool_results: list[dict[str, Any]],
    critic: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:
    top = _top_candidate(tool_results)
    repair_called = _repair_dry_run_called(tool_results)
    if critic.get("issues"):
        decision = "needs_more_evidence"
        selected = []
        rationale = "Guardrail critic found issues in the planned or executed tool sequence."
    elif not planner.get("tool_calls") or (
        not repair_called and not _audit_has_actionable_issues(audit_report)
    ):
        decision = "no_action"
        selected = []
        rationale = "No actionable audit issue required repair dry-run."
    elif repair_called and not top:
        decision = "no_candidate_found"
        selected = []
        rationale = "Repair dry-run returned no actionable ranked candidates."
    elif top and top.get("recommendation") == "accept_for_human_review":
        decision = "candidate_ready_for_human_review"
        selected = [str(top.get("candidate_id"))]
        rationale = f"Top candidate {top.get('candidate_id')} is validated and ranked for human review."
    else:
        decision = "needs_more_evidence"
        selected = [str(top.get("candidate_id"))] if top and top.get("candidate_id") else []
        rationale = f"Top candidate {top.get('candidate_id') if top else None} needs more evidence."

    facts = [
        f"Planner called {len(planner.get('tool_calls', []))} tool(s).",
        "All repair tools are dry-run only.",
        f"Critic guardrail status is {critic.get('guardrail_status')}.",
    ]
    if top:
        facts.append(
            f"Top candidate {top.get('candidate_id')} has recommendation {top.get('recommendation')}."
        )
    if reason:
        facts.append(reason)

    return {
        "reviewer_kind": "rule",
        "llm_used": False,
        "final_decision": decision,
        "selected_candidate_ids": selected,
        "rationale": rationale,
        "confirmed_by_artifacts": facts,
        "risks": critic.get("issues", []),
        "next_actions": [
            "Open the dry-run report and confirm the selected candidate before any topology correction."
        ] if selected else ["No topology correction is recommended by this advisor run."],
        "llm_result": {
            "used": False,
            "error": None,
        },
    }


def _normalize_review_content(
    content: dict[str, Any],
    known_ids: set[str],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    allowed_decisions = {
        "candidate_ready_for_human_review",
        "needs_more_evidence",
        "no_candidate_found",
        "no_action",
    }
    decision = str(content.get("final_decision") or fallback["final_decision"])
    if decision not in allowed_decisions:
        decision = fallback["final_decision"]
    if (
        fallback.get("final_decision") == "candidate_ready_for_human_review"
        and decision in {"no_action", "no_candidate_found"}
    ):
        decision = fallback["final_decision"]
    selected = [
        str(candidate_id)
        for candidate_id in content.get("selected_candidate_ids", [])
        if str(candidate_id) in known_ids
    ]
    if decision == "candidate_ready_for_human_review" and not selected:
        selected = list(fallback.get("selected_candidate_ids", []))
    return {
        "final_decision": decision,
        "selected_candidate_ids": selected,
        "rationale": str(content.get("rationale") or fallback["rationale"]),
        "confirmed_by_artifacts": _as_string_list(
            content.get("confirmed_by_artifacts") or fallback["confirmed_by_artifacts"]
        ),
        "risks": _as_string_list(content.get("risks") or fallback["risks"]),
        "next_actions": _as_string_list(content.get("next_actions") or fallback["next_actions"]),
    }


def _review_tool_results(
    audit_report: dict[str, Any] | None,
    observation: dict[str, Any],
    planner: dict[str, Any],
    tool_results: list[dict[str, Any]],
    critic: dict[str, Any],
    backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> dict[str, Any]:
    if backend in {"rule", "mock"}:
        review = _rule_review(
            audit_report,
            planner,
            tool_results,
            critic,
            reason="Mock backend used rule-constrained review." if backend == "mock" else None,
        )
        review["reviewer_kind"] = backend
        review["llm_used"] = backend == "mock"
        if backend == "mock":
            review["llm_result"] = {
                "used": True,
                "backend": "mock",
                "model": "mock",
                "error": None,
            }
        return review

    fallback = _rule_review(audit_report, planner, tool_results, critic)
    payload = {
        "observation": observation,
        "audit_report": _compact_audit(audit_report),
        "planner": {
            "planner_kind": planner.get("planner_kind"),
            "tool_calls": planner.get("tool_calls", []),
            "planner_notes": planner.get("planner_notes", []),
        },
        "tool_results": tool_results,
        "critic": critic,
        "deterministic_fallback_review": {
            key: value for key, value in fallback.items() if key != "llm_result"
        },
    }
    llm_result = complete_json(
        REVIEWER_SYSTEM_PROMPT,
        payload,
        backend=backend,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )
    content = llm_result.get("content", {}) if isinstance(llm_result.get("content"), dict) else {}
    if not llm_result.get("used") or llm_result.get("error"):
        fallback["reviewer_kind"] = "rule_fallback"
        fallback["llm_result"] = llm_result
        return fallback
    normalized = _normalize_review_content(content, _known_candidate_ids(tool_results), fallback)
    return {
        "reviewer_kind": "llm",
        "llm_used": True,
        **normalized,
        "llm_result": llm_result,
    }


def _ensure_audit_report(
    debug_dir: Path,
    audit_backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
    refresh_audit: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    audit_path = debug_dir / "agent_audit_report.json"
    if audit_path.exists() and not refresh_audit:
        return _read_json(audit_path), {
            "tool_name": "agent_audit",
            "status": "loaded_existing",
            "outputs": {
                "json": str(audit_path),
                "markdown": str(debug_dir / "agent_audit_report.md"),
            },
        }
    result = run_agent_audit_workflow(
        debug_dir,
        output_dir=debug_dir,
        backend=audit_backend,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        workflow_engine="local",
    )
    return result["report"], {
        "tool_name": "agent_audit",
        "status": "generated",
        "outputs": result.get("outputs", {}),
    }


def _build_agent_trace(state: dict[str, Any]) -> list[dict[str, Any]]:
    if state.get("graph_trace"):
        return list(state.get("graph_trace", []))

    trace = [
        {
            "step": "audit_tool",
            "status": state.get("audit_tool", {}).get("status"),
            "summary": {
                "case_id": state.get("audit_report", {}).get("case_id") if state.get("audit_report") else None,
                "overall_status": state.get("audit_report", {}).get("overall_status") if state.get("audit_report") else None,
                "primary_issue": state.get("audit_report", {}).get("primary_issue") if state.get("audit_report") else None,
            },
        },
        {
            "step": "observe",
            "status": "completed",
            "summary": {
                "case_id": state.get("observation", {}).get("case_summary", {}).get("case_id"),
                "review_status": state.get("observation", {}).get("case_summary", {}).get("review_status"),
                "artifact_count": sum(
                    1
                    for item in state.get("observation", {}).get("artifact_presence", {}).values()
                    if item.get("exists")
                ),
            },
        },
    ]
    planner_steps = state.get("planner", {}).get("planner_steps") or [state.get("planner", {})]
    for planner_step in planner_steps:
        iteration = planner_step.get("iteration")
        trace.append(
            {
                "step": "planner",
                "status": "completed",
                "summary": {
                    "iteration": iteration,
                    "planner_kind": planner_step.get("planner_kind"),
                    "stop_decision": planner_step.get("stop_decision"),
                    "tool_calls": [
                        call.get("tool_name") for call in planner_step.get("tool_calls", [])
                    ],
                    "guardrail_feedback": planner_step.get("guardrail_feedback", []),
                    "open_questions": planner_step.get("open_questions", [])[:3],
                },
            }
        )
        for result in state.get("tool_results", []):
            if result.get("planner_iteration") != iteration:
                continue
            trace.append(
                {
                    "step": "tool_executor",
                    "status": result.get("status"),
                    "summary": {
                        "iteration": iteration,
                        "tool_call_id": result.get("tool_call_id"),
                        "tool_name": result.get("tool_name"),
                        "result_exists": result.get("result_summary", {}).get("exists"),
                    },
                }
            )
    unassigned_results = [
        result for result in state.get("tool_results", []) if result.get("planner_iteration") is None
    ]
    for result in unassigned_results:
        trace.append(
            {
                "step": "tool_executor",
                "status": result.get("status"),
                "summary": {
                    "tool_call_id": result.get("tool_call_id"),
                    "tool_name": result.get("tool_name"),
                    "result_exists": result.get("result_summary", {}).get("exists"),
                },
            }
        )
    trace.extend(
        [
            {
                "step": "critic",
                "status": state.get("critic", {}).get("guardrail_status"),
                "summary": {
                    "issue_count": len(state.get("critic", {}).get("issues", [])),
                    "notes": state.get("critic", {}).get("notes", [])[:3],
                },
            },
            {
                "step": "reviewer",
                "status": "completed",
                "summary": {
                    "reviewer_kind": state.get("reviewer", {}).get("reviewer_kind"),
                    "final_decision": state.get("reviewer", {}).get("final_decision"),
                    "selected_candidate_ids": state.get("reviewer", {}).get("selected_candidate_ids", []),
                },
            },
        ]
    )
    return trace


def _build_human_review_dossier(
    audit_report: dict[str, Any] | None,
    tool_results: list[dict[str, Any]],
    reviewer: dict[str, Any],
) -> dict[str, Any]:
    single_pin_summary: dict[str, Any] = {}
    attachments_by_pin: dict[str, dict[str, Any]] = {}
    candidates = []

    for result in tool_results:
        summary = result.get("result_summary", {})
        if result.get("tool_name") == "get_single_pin_nets" and summary.get("exists"):
            single_pin_summary = summary
        if result.get("tool_name") == "get_terminal_attachments" and summary.get("exists"):
            for attachment in summary.get("attachments", []):
                pin_id = attachment.get("pin_id")
                if pin_id:
                    attachments_by_pin[str(pin_id)] = attachment
        if result.get("tool_name") == "repair_dry_run" and summary.get("exists"):
            candidates.extend(summary.get("top_candidates", []))

    pin_details_by_net: dict[str, list[dict[str, Any]]] = {}
    for pin in single_pin_summary.get("pin_details", []):
        net_id = pin.get("net_id")
        if net_id:
            pin_details_by_net.setdefault(str(net_id), []).append(pin)

    nets = []
    for net in single_pin_summary.get("nets", []):
        net_id = str(net.get("net_id"))
        pin_details = pin_details_by_net.get(net_id, [])
        net_attachments = []
        for pin in pin_details:
            attachment = attachments_by_pin.get(str(pin.get("pin_id")))
            if attachment:
                net_attachments.append(
                    {
                        "pin_id": pin.get("pin_id"),
                        "best_raw_component_id": attachment.get("best_raw_component_id"),
                        "best_evidence_kind": attachment.get("best_evidence_kind"),
                        "best_evidence_id": attachment.get("best_evidence_id"),
                        "best_attachment_score": attachment.get("best_attachment_score"),
                        "candidate_count": attachment.get("candidate_count"),
                    }
                )
        nets.append(
            {
                "net_id": net_id,
                "pin_count": net.get("pin_count"),
                "pin_refs": net.get("pin_refs", []),
                "component_refs": net.get("component_refs", []),
                "pin_details": pin_details,
                "attachments": net_attachments,
            }
        )

    selected_ids = set(_as_id_list(reviewer.get("selected_candidate_ids")))
    selected_candidates = [
        candidate for candidate in candidates if str(candidate.get("candidate_id")) in selected_ids
    ]
    if not selected_candidates and candidates:
        selected_candidates = candidates[:1]

    return {
        "schema_version": "3.4-human-review-dossier",
        "case_id": audit_report.get("case_id") if audit_report else None,
        "primary_issue": audit_report.get("primary_issue") if audit_report else None,
        "suspected_stage": audit_report.get("suspected_stage") if audit_report else None,
        "final_decision": reviewer.get("final_decision"),
        "selected_candidate_ids": reviewer.get("selected_candidate_ids", []),
        "plain_language_summary": (
            "The advisor found single-pin nets and a dry-run merge candidate that should be reviewed by a human."
            if selected_candidates
            else "The advisor did not find a selected repair candidate for this run."
        ),
        "nets": nets,
        "selected_candidates": selected_candidates,
        "confidence_cues": [
            "single-pin nets are measured from netlist pin counts",
            "terminal attachment scores come from terminal-to-evidence corridor matching",
            "repair candidates are dry-run only and do not mutate topology/netlist/DXF",
            "candidate recommendation is generated by deterministic validation/ranking, then reviewed by the LLM",
        ],
        "human_checklist": [
            "Open 13_overlay.png and 12_active_nodes.png for the same debug run.",
            "Locate each listed net ID and confirm its component pin label.",
            "Check whether the selected candidate action matches the visible circuit intent.",
            "Only apply a topology correction after confirming the dry-run candidate.",
        ],
    }


def render_human_review_dossier_markdown(dossier: dict[str, Any]) -> str:
    lines = [
        f"# Human Review Dossier: {dossier.get('case_id')}",
        "",
        f"- final decision: `{dossier.get('final_decision')}`",
        f"- selected candidates: `{dossier.get('selected_candidate_ids')}`",
        f"- primary issue: `{dossier.get('primary_issue')}`",
        f"- suspected stage: `{dossier.get('suspected_stage')}`",
        "",
        "## What The Net IDs Mean",
        "",
    ]
    if not dossier.get("nets"):
        lines.append("- No focused net summary was produced.")
    for net in dossier.get("nets", []):
        lines.append(
            f"- `{net.get('net_id')}`: pin_count=`{net.get('pin_count')}`, "
            f"pins=`{net.get('pin_refs')}`, components=`{net.get('component_refs')}`"
        )
        for pin in net.get("pin_details", []):
            lines.append(
                f"  - pin `{pin.get('pin_id')}` = `{pin.get('pin_ref')}` "
                f"on `{pin.get('refdes')}` / `{pin.get('class_name')}`, side=`{pin.get('side')}`"
            )
        for attachment in net.get("attachments", []):
            lines.append(
                f"  - attachment for `{attachment.get('pin_id')}`: "
                f"{attachment.get('best_evidence_kind')} `{attachment.get('best_evidence_id')}`, "
                f"raw_component=`{attachment.get('best_raw_component_id')}`, "
                f"score=`{attachment.get('best_attachment_score')}`"
            )

    lines.extend(["", "## Selected Candidate", ""])
    if not dossier.get("selected_candidates"):
        lines.append("- No selected candidate.")
    for candidate in dossier.get("selected_candidates", []):
        lines.append(
            f"- `{candidate.get('candidate_id')}` / `{candidate.get('repair_type')}`: "
            f"recommendation=`{candidate.get('recommendation')}`, "
            f"validation=`{candidate.get('validation_result')}`, "
            f"ranking_score=`{candidate.get('ranking_score')}`"
        )
        lines.append(f"  - target nodes: `{candidate.get('target_nodes', [])}`")
        lines.append(f"  - target pins: `{candidate.get('target_pins', [])}`")
        if candidate.get("improved_metrics"):
            lines.append(f"  - improved metrics: `{candidate.get('improved_metrics')}`")
        if candidate.get("regressed_metrics"):
            lines.append(f"  - regressed metrics: `{candidate.get('regressed_metrics')}`")
        if candidate.get("reasons"):
            lines.append("  - reasons:")
            for reason in candidate.get("reasons", [])[:6]:
                lines.append(f"    - {reason}")

    lines.extend(["", "## Confidence Cues", ""])
    for cue in dossier.get("confidence_cues", []):
        lines.append(f"- {cue}")
    lines.extend(["", "## Human Checklist", ""])
    for item in dossier.get("human_checklist", []):
        lines.append(f"- {item}")
    return "\n".join(lines)


def render_repair_advisor_markdown(report: dict[str, Any]) -> str:
    planner = report.get("planner", {})
    critic = report.get("critic", {})
    reviewer = report.get("reviewer", {})
    lines = [
        f"# Agent Repair Advisor Report: {report.get('case_id')}",
        "",
        "## Summary",
        "",
        f"- schema: `{report.get('schema_version')}`",
        f"- workflow engine: `{report.get('workflow_engine')}`",
        f"- backend: `{report.get('backend')}`",
        f"- LLM used: `{report.get('llm_used')}`",
        f"- final decision: `{report.get('final_decision')}`",
        f"- selected candidates: `{report.get('selected_candidate_ids')}`",
        f"- topology mutated: `{report.get('topology_mutated')}`",
        "",
        "## Agent Trace",
        "",
    ]
    for step in report.get("agent_trace", []):
        lines.append(f"- `{step.get('step')}` status=`{step.get('status')}` summary=`{step.get('summary')}`")

    lines.extend(["", "## Planner", ""])
    lines.append(f"- planner kind: `{planner.get('planner_kind')}`")
    lines.append(f"- stop reason: `{planner.get('stop_reason')}`")
    if planner.get("planner_steps"):
        lines.append("- planner iterations:")
        for step in planner.get("planner_steps", []):
            step_calls = [call.get("tool_name") for call in step.get("tool_calls", [])]
            lines.append(
                f"  - iteration `{step.get('iteration')}` kind=`{step.get('planner_kind')}` "
                f"stop=`{step.get('stop_decision')}` calls=`{step_calls}`"
            )
            for feedback in step.get("guardrail_feedback", []):
                lines.append(f"    - guardrail: {feedback}")
    for call in planner.get("tool_calls", []):
        lines.append(
            f"- tool call: `{call.get('tool_name')}` args=`{call.get('arguments', {})}` - {call.get('reason')}"
        )
        if call.get("argument_grounding", {}).get("applied"):
            lines.append(
                f"  - grounded args from `{call.get('argument_grounding', {}).get('source')}`: "
                f"`{call.get('argument_grounding', {}).get('added_arguments')}`"
            )
    if not planner.get("tool_calls"):
        lines.append("- tool call: none")
    for note in planner.get("planner_notes", [])[:8]:
        lines.append(f"- planner note: {note}")

    lines.extend(["", "## Tool Results", ""])
    for result in report.get("tool_results", []):
        summary = result.get("result_summary", {})
        tool_name = result.get("tool_name")
        if tool_name == "get_terminal_attachments":
            metric_label = "attachments"
            metric_count = summary.get("attachment_count", len(summary.get("attachments", [])))
        elif tool_name == "get_single_pin_nets":
            metric_label = "single_pin_nets"
            metric_count = summary.get("single_pin_net_count", len(summary.get("nets", [])))
        elif tool_name == "get_repair_candidates":
            metric_label = "review_candidates"
            metric_count = summary.get("summary", {}).get("candidate_count", len(summary.get("candidates", [])))
        else:
            metric_label = "candidates"
            metric_count = (
                summary.get("validation_summary", {}).get("candidate_count")
                or summary.get("summary", {}).get("candidate_count")
                or len(summary.get("top_candidates", []))
                or len(summary.get("candidates", []))
            )
        lines.append(
            f"- `{result.get('tool_call_id')}` / `{result.get('tool_name')}` "
            f"status=`{result.get('status')}` {metric_label}=`{metric_count}`"
        )
        if result.get("argument_grounding", {}).get("applied"):
            lines.append(
                f"  - grounded args: `{result.get('argument_grounding', {}).get('added_arguments')}`"
            )
        for candidate in summary.get("top_candidates", [])[:5]:
            lines.append(
                f"  - `{candidate.get('candidate_id')}` / `{candidate.get('repair_type')}` "
                f"rank=`{candidate.get('rank')}` ranking_score=`{candidate.get('ranking_score')}` "
                f"validation=`{candidate.get('validation_result')}` recommendation=`{candidate.get('recommendation')}`"
            )

    lines.extend(["", "## Critic", ""])
    lines.append(f"- critic kind: `{critic.get('critic_kind')}`")
    lines.append(f"- guardrail status: `{critic.get('guardrail_status')}`")
    for issue in critic.get("issues", []):
        lines.append(f"- critic issue: {issue}")
    for note in critic.get("notes", []):
        lines.append(f"- critic note: {note}")

    lines.extend(["", "## Reviewer", ""])
    lines.append(f"- reviewer kind: `{reviewer.get('reviewer_kind')}`")
    lines.append(f"- rationale: {reviewer.get('rationale')}")
    if reviewer.get("confirmed_by_artifacts"):
        lines.append("- confirmed by artifacts:")
        for item in reviewer.get("confirmed_by_artifacts", []):
            lines.append(f"  - {item}")
    if reviewer.get("risks"):
        lines.append("- risks:")
        for item in reviewer.get("risks", []):
            lines.append(f"  - {item}")
    if reviewer.get("next_actions"):
        lines.append("- next actions:")
        for item in reviewer.get("next_actions", []):
            lines.append(f"  - {item}")
    dossier = report.get("human_review_dossier", {})
    if dossier:
        lines.extend(["", "## Human Review Dossier Preview", ""])
        for net in dossier.get("nets", [])[:4]:
            lines.append(
                f"- `{net.get('net_id')}` means pins `{net.get('pin_refs')}` "
                f"on components `{net.get('component_refs')}`"
            )
        for candidate in dossier.get("selected_candidates", [])[:3]:
            lines.append(
                f"- selected `{candidate.get('candidate_id')}` targets nodes "
                f"`{candidate.get('target_nodes', [])}` and pins `{candidate.get('target_pins', [])}`"
            )
    if planner.get("llm_result", {}).get("error") or reviewer.get("llm_result", {}).get("error"):
        lines.extend(["", "## LLM Errors", ""])
        if planner.get("llm_result", {}).get("error"):
            lines.append(f"- planner: {planner.get('llm_result', {}).get('error')}")
        if reviewer.get("llm_result", {}).get("error"):
            lines.append(f"- reviewer: {reviewer.get('llm_result', {}).get('error')}")
    return "\n".join(lines)


def _write_outputs(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / ADVISOR_OUTPUT_JSON
    md_path = output_dir / ADVISOR_OUTPUT_MD
    dossier_json_path = output_dir / DOSSIER_OUTPUT_JSON
    dossier_md_path = output_dir / DOSSIER_OUTPUT_MD
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_repair_advisor_markdown(report), encoding="utf-8")
    dossier = report.get("human_review_dossier", {})
    dossier_json_path.write_text(json.dumps(dossier, ensure_ascii=False, indent=2), encoding="utf-8")
    dossier_md_path.write_text(render_human_review_dossier_markdown(dossier), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "human_review_dossier_json": str(dossier_json_path),
        "human_review_dossier_markdown": str(dossier_md_path),
    }


def _build_report(state: dict[str, Any]) -> dict[str, Any]:
    audit_report = state.get("audit_report")
    reviewer = state.get("reviewer", {})
    trace = _build_agent_trace(state)
    human_review_dossier = _build_human_review_dossier(
        audit_report,
        state.get("tool_results", []),
        reviewer,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": audit_report.get("case_id") if audit_report else Path(state["debug_dir"]).name,
        "debug_dir": str(state["debug_dir"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow_engine": state.get("workflow_engine"),
        "backend": state.get("backend"),
        "audit_backend": state.get("audit_backend"),
        "model": state.get("model"),
        "base_url": state.get("base_url"),
        "agent_loop_config": {
            "max_agent_tool_steps": state.get("max_agent_tool_steps"),
            "max_tool_calls_per_step": state.get("max_tool_calls_per_step"),
            "memory_file": state.get("memory_file"),
            "memory_limit": state.get("memory_limit"),
        },
        "workflow_mode": state.get("workflow_mode") or (
            "langgraph_native_state_machine" if state.get("graph_trace") else "local_loop"
        ),
        "workflow_topology": state.get("workflow_topology", []),
        "llm_used": bool(state.get("planner", {}).get("llm_used") or reviewer.get("llm_used")),
        "topology_mutated": False,
        "available_tools": _available_tools(),
        "audit_tool": state.get("audit_tool"),
        "observation": _scrub_secrets(state.get("observation", {})),
        "planner": _scrub_secrets(state.get("planner", {})),
        "tool_results": _scrub_secrets(state.get("tool_results", [])),
        "critic": _scrub_secrets(state.get("critic", {})),
        "reviewer": _scrub_secrets(reviewer),
        "agent_trace": _scrub_secrets(trace),
        "human_review_dossier": _scrub_secrets(human_review_dossier),
        "final_decision": reviewer.get("final_decision"),
        "selected_candidate_ids": reviewer.get("selected_candidate_ids", []),
    }


def _run_local(
    debug_dir: Path,
    output_dir: Path,
    backend: str,
    audit_backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
    refresh_audit: bool,
    max_agent_tool_steps: int,
    max_tool_calls_per_step: int,
    memory_file: str | None,
    memory_limit: int,
) -> dict[str, Any]:
    audit_report, audit_tool = _ensure_audit_report(
        debug_dir,
        audit_backend,
        model,
        base_url,
        api_key,
        api_key_env,
        refresh_audit,
    )
    observation = _build_observation(debug_dir, audit_report, memory_file, memory_limit)
    planner, tool_results = _run_planner_tool_loop(
        debug_dir,
        output_dir,
        audit_report,
        observation,
        max_agent_tool_steps,
        max_tool_calls_per_step,
        backend,
        model,
        base_url,
        api_key,
        api_key_env,
    )
    critic = _critic_review(audit_report, planner, tool_results)
    reviewer = _review_tool_results(
        audit_report,
        observation,
        planner,
        tool_results,
        critic,
        backend,
        model,
        base_url,
        api_key,
        api_key_env,
    )
    return {
        "debug_dir": str(debug_dir),
        "output_dir": str(output_dir),
        "workflow_engine": "local",
        "workflow_mode": "local_loop",
        "workflow_topology": [
            "audit_tool",
            "observe",
            "planner_tool_loop",
            "critic",
            "reviewer",
        ],
        "backend": backend,
        "audit_backend": audit_backend,
        "model": model,
        "base_url": base_url,
        "max_agent_tool_steps": max_agent_tool_steps,
        "max_tool_calls_per_step": max_tool_calls_per_step,
        "memory_file": memory_file,
        "memory_limit": memory_limit,
        "audit_report": audit_report,
        "audit_tool": audit_tool,
        "observation": observation,
        "planner": planner,
        "tool_results": tool_results,
        "critic": critic,
        "reviewer": reviewer,
    }


def _run_langgraph(
    debug_dir: Path,
    output_dir: Path,
    backend: str,
    audit_backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
    refresh_audit: bool,
    max_agent_tool_steps: int,
    max_tool_calls_per_step: int,
    memory_file: str | None,
    memory_limit: int,
    allow_fallback: bool,
) -> dict[str, Any]:
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except Exception as exc:
        if not allow_fallback:
            raise RuntimeError(
                "LangGraph is required for --workflow-engine langgraph. "
                "Install langgraph or use --workflow-engine auto/local."
            ) from exc
        return _run_local(
            debug_dir,
            output_dir,
            backend,
            audit_backend,
            model,
            base_url,
            api_key,
            api_key_env,
            refresh_audit,
            max_agent_tool_steps,
            max_tool_calls_per_step,
            memory_file,
            memory_limit,
        )

    def audit_node(state: dict[str, Any]) -> dict[str, Any]:
        audit_report, audit_tool = _ensure_audit_report(
            Path(state["debug_dir"]),
            state["audit_backend"],
            state.get("model"),
            state.get("base_url"),
            state.get("api_key"),
            state.get("api_key_env"),
            bool(state.get("refresh_audit")),
        )
        state["audit_report"] = audit_report
        state["audit_tool"] = audit_tool
        _record_graph_event(
            state,
            "audit_tool",
            status=audit_tool.get("status"),
            summary={
                "case_id": audit_report.get("case_id") if audit_report else None,
                "overall_status": audit_report.get("overall_status") if audit_report else None,
                "primary_issue": audit_report.get("primary_issue") if audit_report else None,
            },
        )
        return state

    def observe_node(state: dict[str, Any]) -> dict[str, Any]:
        state["observation"] = _build_observation(
            Path(state["debug_dir"]),
            state.get("audit_report"),
            state.get("memory_file"),
            int(state.get("memory_limit") or 5),
        )
        _record_graph_event(
            state,
            "observe",
            summary={
                "case_id": state.get("observation", {}).get("case_summary", {}).get("case_id"),
                "review_status": state.get("observation", {}).get("case_summary", {}).get("review_status"),
                "memory_matches": state.get("observation", {}).get("failure_memory", {}).get("matched_pattern_count"),
                "artifact_count": sum(
                    1
                    for item in state.get("observation", {}).get("artifact_presence", {}).values()
                    if item.get("exists")
                ),
            },
        )
        return state

    def plan_next_action_node(state: dict[str, Any]) -> dict[str, Any]:
        iteration = int(state.get("current_iteration") or 0) + 1
        state["current_iteration"] = iteration
        tool_results = list(state.get("tool_results", []))
        planner_feedback = list(state.get("planner_feedback", []))
        planner = _plan_tool_calls(
            state.get("audit_report"),
            state.get("observation", {}),
            tool_results,
            _completed_tool_names(tool_results),
            planner_feedback,
            iteration,
            int(state.get("max_tool_calls_per_step") or DEFAULT_MAX_TOOL_CALLS_PER_STEP),
            state["backend"],
            state.get("model"),
            state.get("base_url"),
            state.get("api_key"),
            state.get("api_key_env"),
        )
        planner_feedback.extend(planner.get("guardrail_feedback", []))
        raw_calls = planner.get("tool_calls", [])
        grounded_calls = [
            _ground_tool_call_arguments(call, state.get("audit_report"), tool_results)
            for call in raw_calls
        ]
        planner["raw_tool_calls"] = raw_calls
        planner["tool_calls"] = grounded_calls
        planner_steps = list(state.get("planner_steps", []))
        planner_steps.append(planner)
        state["planner_steps"] = planner_steps
        state["planner_feedback"] = planner_feedback
        state["pending_tool_calls"] = grounded_calls
        _record_graph_event(
            state,
            "plan_next_action",
            summary={
                "iteration": iteration,
                "planner_kind": planner.get("planner_kind"),
                "stop_decision": planner.get("stop_decision"),
                "tool_calls": [call.get("tool_name") for call in grounded_calls],
                "guardrail_feedback": planner.get("guardrail_feedback", []),
                "completed_tool_names": sorted(_completed_tool_names(tool_results)),
            },
        )
        return state

    def execute_tool_node(state: dict[str, Any]) -> dict[str, Any]:
        calls = list(state.get("pending_tool_calls", []))
        if not calls:
            state["pending_tool_results"] = []
            _record_graph_event(
                state,
                "execute_tool",
                status="skipped",
                summary={
                    "iteration": state.get("current_iteration"),
                    "reason": "planner_selected_no_tool",
                },
            )
            return state
        new_results = _invoke_tools(
            Path(state["debug_dir"]),
            Path(state["output_dir"]),
            state.get("audit_report"),
            calls,
            start_index=len(state.get("tool_results", [])) + 1,
            planner_iteration=int(state.get("current_iteration") or 0),
        )
        state["pending_tool_results"] = new_results
        _record_graph_event(
            state,
            "execute_tool",
            summary={
                "iteration": state.get("current_iteration"),
                "tool_results": [
                    {
                        "tool_call_id": result.get("tool_call_id"),
                        "tool_name": result.get("tool_name"),
                        "status": result.get("status"),
                    }
                    for result in new_results
                ],
            },
        )
        return state

    def update_state_node(state: dict[str, Any]) -> dict[str, Any]:
        prior_results = list(state.get("tool_results", []))
        new_results = list(state.get("pending_tool_results", []))
        state["tool_results"] = prior_results + new_results
        state["last_tool_results"] = new_results
        state["pending_tool_results"] = []
        state["pending_tool_calls"] = []
        _record_graph_event(
            state,
            "update_state",
            summary={
                "iteration": state.get("current_iteration"),
                "new_tool_result_count": len(new_results),
                "total_tool_result_count": len(state.get("tool_results", [])),
                "completed_tool_names": sorted(_completed_tool_names(state.get("tool_results", []))),
            },
        )
        return state

    def decide_continue_node(state: dict[str, Any]) -> dict[str, Any]:
        iteration = int(state.get("current_iteration") or 0)
        max_steps = int(state.get("max_agent_tool_steps") or DEFAULT_MAX_AGENT_TOOL_STEPS)
        planner_steps = list(state.get("planner_steps", []))
        last_planner = planner_steps[-1] if planner_steps else {}
        last_results = list(state.get("last_tool_results", []))
        route = "plan_next_action"
        stop_reason = None

        if any(result.get("tool_name") == "repair_dry_run" for result in last_results):
            route = "critic"
            stop_reason = "repair_dry_run_completed"
        elif iteration >= max_steps:
            route = "critic"
            stop_reason = "max_steps_reached"
        elif not last_planner.get("tool_calls"):
            if last_planner.get("guardrail_feedback"):
                route = "plan_next_action"
            else:
                route = "critic"
                stop_reason = (
                    "planner_declared_final_ready"
                    if last_planner.get("stop_decision") == "final_ready"
                    else "planner_selected_no_more_tools"
                )

        state["next_route"] = route
        if stop_reason:
            state["stop_reason"] = stop_reason
        _record_graph_event(
            state,
            "decide_continue",
            summary={
                "iteration": iteration,
                "route": route,
            "stop_reason": stop_reason,
                "max_agent_tool_steps": max_steps,
            },
        )
        return state

    def critic_node(state: dict[str, Any]) -> dict[str, Any]:
        state["planner"] = _aggregate_planner_steps(
            list(state.get("planner_steps", [])),
            str(state.get("stop_reason") or "max_steps_reached"),
        )
        state["critic"] = _critic_review(
            state.get("audit_report"),
            state.get("planner", {}),
            state.get("tool_results", []),
        )
        _record_graph_event(
            state,
            "critic",
            status=state.get("critic", {}).get("guardrail_status", "completed"),
            summary={
                "issue_count": len(state.get("critic", {}).get("issues", [])),
                "notes": state.get("critic", {}).get("notes", [])[:3],
            },
        )
        return state

    def reviewer_node(state: dict[str, Any]) -> dict[str, Any]:
        state["reviewer"] = _review_tool_results(
            state.get("audit_report"),
            state.get("observation", {}),
            state.get("planner", {}),
            state.get("tool_results", []),
            state.get("critic", {}),
            state["backend"],
            state.get("model"),
            state.get("base_url"),
            state.get("api_key"),
            state.get("api_key_env"),
        )
        _record_graph_event(
            state,
            "reviewer",
            summary={
                "reviewer_kind": state.get("reviewer", {}).get("reviewer_kind"),
                "final_decision": state.get("reviewer", {}).get("final_decision"),
                "selected_candidate_ids": state.get("reviewer", {}).get("selected_candidate_ids", []),
            },
        )
        return state

    def route_after_decision(state: dict[str, Any]) -> str:
        return "plan_next_action" if state.get("next_route") == "plan_next_action" else "critic"

    graph = StateGraph(dict)
    graph.add_node("audit_tool", audit_node)
    graph.add_node("observe", observe_node)
    graph.add_node("plan_next_action", plan_next_action_node)
    graph.add_node("execute_tool", execute_tool_node)
    graph.add_node("update_state", update_state_node)
    graph.add_node("decide_continue", decide_continue_node)
    graph.add_node("critic", critic_node)
    graph.add_node("reviewer", reviewer_node)
    graph.set_entry_point("audit_tool")
    graph.add_edge("audit_tool", "observe")
    graph.add_edge("observe", "plan_next_action")
    graph.add_edge("plan_next_action", "execute_tool")
    graph.add_edge("execute_tool", "update_state")
    graph.add_edge("update_state", "decide_continue")
    graph.add_conditional_edges(
        "decide_continue",
        route_after_decision,
        {
            "plan_next_action": "plan_next_action",
            "critic": "critic",
        },
    )
    graph.add_edge("critic", "reviewer")
    graph.add_edge("reviewer", END)
    app = graph.compile()
    state = app.invoke(
        {
            "debug_dir": str(debug_dir),
            "output_dir": str(output_dir),
            "backend": backend,
            "audit_backend": audit_backend,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "api_key_env": api_key_env,
            "refresh_audit": refresh_audit,
            "max_agent_tool_steps": max_agent_tool_steps,
            "max_tool_calls_per_step": max_tool_calls_per_step,
            "memory_file": memory_file,
            "memory_limit": memory_limit,
            "workflow_engine": "langgraph",
            "workflow_mode": "langgraph_native_state_machine",
            "workflow_topology": [
                "audit_tool",
                "observe",
                "plan_next_action",
                "execute_tool",
                "update_state",
                "decide_continue",
                "critic",
                "reviewer",
            ],
            "planner_steps": [],
            "tool_results": [],
            "planner_feedback": [],
            "pending_tool_calls": [],
            "pending_tool_results": [],
            "last_tool_results": [],
            "current_iteration": 0,
            "stop_reason": None,
            "graph_trace": [],
        }
    )
    state["workflow_engine"] = "langgraph"
    return state


def run_agent_repair_advisor_workflow(
    debug_dir: str | Path,
    output_dir: str | Path | None = None,
    backend: str = "rule",
    audit_backend: str = "rule",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    workflow_engine: str = "auto",
    refresh_audit: bool = False,
    max_agent_tool_steps: int | None = None,
    max_tool_calls_per_step: int | None = None,
    memory_file: str | Path | None = None,
    memory_limit: int = 5,
) -> dict[str, Any]:
    """Run the agent 3.4 LangGraph-native tool state machine advisor workflow."""
    if backend not in {"rule", "mock", "openai", "deepseek", "custom"}:
        raise ValueError(f"Unknown backend: {backend}")
    if audit_backend not in {"rule", "mock", "openai", "deepseek", "custom"}:
        raise ValueError(f"Unknown audit_backend: {audit_backend}")
    if workflow_engine not in {"auto", "local", "langgraph"}:
        raise ValueError(f"Unknown workflow_engine: {workflow_engine}")

    base_dir = Path(debug_dir)
    out_dir = Path(output_dir) if output_dir else base_dir
    resolved_max_agent_tool_steps = _positive_int_or_default(
        max_agent_tool_steps,
        DEFAULT_MAX_AGENT_TOOL_STEPS,
    )
    resolved_max_tool_calls_per_step = _positive_int_or_default(
        max_tool_calls_per_step,
        DEFAULT_MAX_TOOL_CALLS_PER_STEP,
    )
    resolved_memory_file = str(memory_file) if memory_file else None
    resolved_memory_limit = max(0, int(memory_limit or 0))
    if workflow_engine == "local":
        state = _run_local(
            base_dir,
            out_dir,
            backend,
            audit_backend,
            model,
            base_url,
            api_key,
            api_key_env,
            refresh_audit,
            resolved_max_agent_tool_steps,
            resolved_max_tool_calls_per_step,
            resolved_memory_file,
            resolved_memory_limit,
        )
    else:
        state = _run_langgraph(
            base_dir,
            out_dir,
            backend,
            audit_backend,
            model,
            base_url,
            api_key,
            api_key_env,
            refresh_audit,
            resolved_max_agent_tool_steps,
            resolved_max_tool_calls_per_step,
            resolved_memory_file,
            resolved_memory_limit,
            allow_fallback=(workflow_engine == "auto"),
        )
        if workflow_engine == "auto" and state.get("workflow_engine") != "langgraph":
            state["workflow_engine"] = "local"

    report = _build_report(state)
    outputs = _write_outputs(report, out_dir)
    return {
        "report": report,
        "outputs": outputs,
        "state": _scrub_secrets(_model_to_dict(state)),
    }
