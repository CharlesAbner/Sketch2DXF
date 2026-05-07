"""Hypothesis-driven LangGraph tool advisor for Sketch2DXF agent 3.9."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.config import get_default_config
from src.agent_workflow.failure_memory import query_failure_memory
from src.agent_workflow.llm_client import complete_json
from src.agent_workflow.repair_dry_run import run_agent_repair_dry_run, run_granular_repair_dry_run
from src.agent_workflow.workflow import run_agent_audit_workflow


SCHEMA_VERSION = "3.9-hypothesis-tool-agent"
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
    "inspect_single_pin_nets",
    "inspect_terminal_attachments",
    "inspect_component_class_candidates",
    "inspect_component_terminal_axis",
    "inspect_gap_bridge_candidates",
    "inspect_single_pin_stub",
    "dry_run_merge_nodes",
    "dry_run_component_class_override",
    "dry_run_component_axis_flip",
    "dry_run_reattach_pin",
    "dry_run_gap_bridge_merge",
    "dry_run_single_pin_stub_bridge",
    "validate_candidate",
}

READ_ONLY_TOOL_NAMES = {
    "get_case_summary",
    "get_single_pin_nets",
    "get_terminal_attachments",
    "get_repair_candidates",
    "inspect_single_pin_nets",
    "inspect_terminal_attachments",
    "inspect_component_class_candidates",
    "inspect_component_terminal_axis",
    "inspect_gap_bridge_candidates",
    "inspect_single_pin_stub",
}

GRANULAR_DRY_RUN_TOOL_NAMES = {
    "dry_run_merge_nodes",
    "dry_run_component_class_override",
    "dry_run_component_axis_flip",
    "dry_run_reattach_pin",
    "dry_run_gap_bridge_merge",
    "dry_run_single_pin_stub_bridge",
}

APPLYABLE_REPAIR_TYPES = {
    "merge_nodes",
    "reattach_pin",
    "gap_bridge_merge",
    "single_pin_stub_bridge",
    "component_pin_axis_flip",
    "component_class_override",
}

GRANULAR_TOOL_TO_REPAIR_TYPE = {
    "dry_run_merge_nodes": "merge_nodes",
    "dry_run_component_class_override": "component_class_override",
    "dry_run_component_axis_flip": "component_pin_axis_flip",
    "dry_run_reattach_pin": "reattach_pin",
    "dry_run_gap_bridge_merge": "gap_bridge_merge",
    "dry_run_single_pin_stub_bridge": "single_pin_stub_bridge",
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
You receive neutral deterministic facts, weak anomaly signals, and any tool
results gathered so far. Choose the next safe tool call(s). Return JSON only.

The observation may include failure_memory: recurring failure patterns learned
from prior eval reports. Treat memory as context for what to inspect next, not
as proof that the current case has the same failure.

Available tool meanings:
- get_case_summary: read compact case-level quality/risk facts. The compact
  case summary is already present in observation, so call this only if the
  observation is missing or you need the raw compact artifact again.
- inspect_single_pin_nets: inspect nets with fewer than two pins and related pins.
- inspect_terminal_attachments: inspect terminal-to-evidence attachment candidates.
- inspect_component_class_candidates: inspect class alternatives for components.
- inspect_component_terminal_axis: inspect a component's current pin axis and
  attachment evidence.
- inspect_gap_bridge_candidates: inspect candidate evidence gaps between graph nodes.
- inspect_single_pin_stub: inspect single-pin terminal stubs and nearby
  supported nodes.
- dry_run_component_class_override: validate one class-change hypothesis.
- dry_run_component_axis_flip: validate one component-axis hypothesis.
- dry_run_reattach_pin: validate one pin reattachment hypothesis.
- dry_run_gap_bridge_merge: validate one gap-bridge merge hypothesis.
- dry_run_single_pin_stub_bridge: validate one single-pin stub bridge hypothesis.
- dry_run_merge_nodes: validate one node-merge hypothesis.
- validate_candidate: re-read validation/ranking details for a previously
  returned candidate.
- repair_dry_run: legacy bulk dry-run fallback. Prefer granular dry-run tools
  unless no granular tool covers the hypothesis.

Tool family guidance is included in tool_families. Use it to map the kind of
question you are asking to the right inspection/dry-run tools; do not treat it
as proof that a repair is correct.

Strict rules:
- You may only call tools listed in available_tools.
- Use facts and weak_signals as clues, not as final diagnoses.
- Form at least one explicit hypothesis before calling a dry-run tool.
- Do not recommend direct topology mutation.
- Prefer one focused next tool call. The workflow can call you again after the
  result comes back.
- Gather evidence before dry-run repair unless prior tool results already
  contain the exact target id and target value.
- Do not repeat tools listed in completed_tool_names.
- Tool arguments must be JSON objects.
- Every dry-run tool call must include a hypothesis_id in arguments.
- If open_questions remain and you do not call another tool, move each one to
  deferred_questions with a concrete artifact-based reason. Otherwise keep
  stop_decision=continue and call the next relevant inspection/dry-run tool.
- Defer a question only when no available tool family can reduce that
  uncertainty further.

Required JSON keys:
- tool_calls: list of {"tool_name": str, "reason": str, "arguments": object}
- planner_notes: list[str]
- stop_decision: one of continue, final_ready
- hypotheses: list of {"id": str, "claim": str, "supporting_facts": list[str],
  "uncertainty": str}
- open_questions: list[str]
- deferred_questions: list of {"question": str, "reason": str}
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
- Prefer candidates that are supported by an explicit hypothesis and a matching
  dry-run result.

Required JSON keys:
- final_decision: one of repair_candidate_ready_for_human_review,
  review_only_issue_for_human_review, needs_more_evidence, no_candidate_found,
  no_action
- repair_plan: object with {"plan_id": str, "status": str, "steps": list}.
  Each step should include step_id, candidate_id, repair_type, hypothesis_id,
  candidate_mode, expected_improvement, and depends_on.
- selected_hypothesis_ids: list[str]
- rationale: str
- confirmed_by_artifacts: list[str]
- risks: list[str]
- next_actions: list[str]
- hypothesis_assessment: list of {"hypothesis_id": str, "status": str,
  "reason": str}
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


def _argument_ids(arguments: dict[str, Any], plural_key: str, singular_key: str) -> list[str]:
    return _as_id_list(arguments.get(plural_key) or arguments.get(singular_key))


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    result = []
    for item in value:
        if isinstance(item, dict):
            result.append(item)
        else:
            result.append({"text": str(item)})
    return result


def _component_pin_groups(topology: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(topology, dict):
        return {}
    return {
        str(group.get("component_id")): group
        for group in topology.get("pins", [])
        if group.get("component_id")
    }


def _component_net_refs(netlist: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(netlist, dict):
        return {}
    return {
        str(component.get("component_id")): component
        for component in netlist.get("components", [])
        if component.get("component_id")
    }


def _class_name(value: Any) -> str:
    return str(value or "").lower()


def _is_power_class_name(class_name: str) -> bool:
    text = _class_name(class_name)
    return (
        text in {"power_source", "voltage_source", "voltage.ac", "voltage.dc", "voltage.battery", "battery", "source"}
        or "voltage" in text
        or "battery" in text
    )


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
    repair_type = candidate.get("repair_type") or candidate.get("issue_type")
    candidate_mode = candidate.get("candidate_mode") or (
        "applyable" if repair_type in APPLYABLE_REPAIR_TYPES else "review_only"
    )
    return {
        "candidate_id": candidate.get("candidate_id") or candidate.get("repair_candidate_id"),
        "repair_type": repair_type,
        "candidate_mode": candidate_mode,
        "hypothesis_id": candidate.get("hypothesis_id") or candidate.get("geometry", {}).get("hypothesis_id"),
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
        "target_component_id": candidate.get("target_component_id"),
        "geometry": candidate.get("geometry", {}),
        "refs": candidate.get("refs", {}),
        "evidence": candidate.get("evidence", {}),
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
    fallback_to_unfiltered = bool(active_filter and not filtered_candidates and candidates)
    if fallback_to_unfiltered:
        filtered_candidates = candidates
    top_candidates = [_candidate_snapshot(candidate) for candidate in filtered_candidates[:8]]
    applyable_candidates = [
        _candidate_snapshot(candidate)
        for candidate in filtered_candidates
        if candidate.get("candidate_mode") == "applyable"
    ][:8]
    review_only_candidates = [
        _candidate_snapshot(candidate)
        for candidate in filtered_candidates
        if candidate.get("candidate_mode") == "review_only"
    ][:8]
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
            "fallback_to_unfiltered": fallback_to_unfiltered,
        },
        "top_candidates": top_candidates,
        "candidate_groups": {
            "applyable": applyable_candidates,
            "review_only": review_only_candidates,
        },
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


def _inspect_component_class_candidates(debug_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    topology = _read_json(_artifact_path(debug_dir, "topology"))
    if not isinstance(topology, dict):
        return {"exists": False, "components": []}
    requested_ids = set(_argument_ids(arguments, "component_ids", "component_id"))
    components = []
    for component in topology.get("components", []):
        component_id = str(component.get("id"))
        if requested_ids and component_id not in requested_ids:
            continue
        alternatives = component.get("class_alternatives", []) or []
        if not requested_ids and not alternatives:
            continue
        components.append(
            {
                "component_id": component_id,
                "refdes": component.get("refdes"),
                "current_class_name": component.get("class_name"),
                "score": component.get("score"),
                "bbox": component.get("bbox"),
                "class_candidates": component.get("class_candidates", []),
                "class_alternatives": alternatives,
            }
        )
    return {
        "exists": True,
        "requested_component_ids": sorted(requested_ids),
        "component_count": len(components),
        "components": components[:12],
    }


def _inspect_component_terminal_axis(debug_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    topology = _read_json(_artifact_path(debug_dir, "topology"))
    attachments = _read_json(_artifact_path(debug_dir, "terminal_attachments"))
    if not isinstance(topology, dict):
        return {"exists": False, "components": []}
    requested_ids = set(_argument_ids(arguments, "component_ids", "component_id"))
    pin_groups = _component_pin_groups(topology)
    attachment_by_pin = {}
    if isinstance(attachments, dict):
        for attachment in attachments.get("attachments", []):
            if attachment.get("pin_id"):
                attachment_by_pin[str(attachment.get("pin_id"))] = _attachment_snapshot(attachment)
    components = []
    for component in topology.get("components", []):
        component_id = str(component.get("id"))
        if requested_ids and component_id not in requested_ids:
            continue
        pin_group = pin_groups.get(component_id, {})
        if not requested_ids and pin_group.get("axis_source") != "fallback":
            continue
        pin_ids = [str(pin.get("pin_id")) for pin in pin_group.get("pins", []) if pin.get("pin_id")]
        axis = str(pin_group.get("axis") or "")
        alternate_axis = "vertical" if axis == "horizontal" else "horizontal" if axis == "vertical" else None
        components.append(
            {
                "component_id": component_id,
                "refdes": component.get("refdes"),
                "class_name": component.get("class_name"),
                "bbox": component.get("bbox"),
                "pin_count": pin_group.get("pin_count"),
                "current_axis": axis,
                "axis_source": pin_group.get("axis_source"),
                "axis_confidence": pin_group.get("confidence"),
                "alternate_axis": alternate_axis,
                "axis_candidates": pin_group.get("axis_candidates", {}),
                "pins": pin_group.get("pins", []),
                "attachments": [
                    attachment_by_pin[pin_id]
                    for pin_id in pin_ids
                    if pin_id in attachment_by_pin
                ],
            }
        )
    return {
        "exists": True,
        "requested_component_ids": sorted(requested_ids),
        "component_count": len(components),
        "components": components[:12],
    }


def _inspect_gap_bridge_candidates(debug_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    doc = _read_json(_artifact_path(debug_dir, "repair_candidates"))
    if not isinstance(doc, dict):
        return {"exists": False, "bridge_candidate_count": 0, "candidates": []}
    requested_nodes = set(_as_id_list(arguments.get("node_ids") or arguments.get("node_id") or arguments.get("target_nodes")))
    candidates = []
    for candidate in doc.get("candidates", []):
        issue_type = str(candidate.get("issue_type") or "")
        if issue_type not in {"possible_gap_bridge", "unsupported_evidence_review"}:
            continue
        refs = candidate.get("refs", {})
        evidence = candidate.get("evidence", {})
        node_refs = {
            str(item)
            for item in [
                refs.get("node_id"),
                refs.get("from_node_id"),
                refs.get("to_node_id"),
            ]
            if item
        }
        if requested_nodes and node_refs and not requested_nodes.intersection(node_refs):
            continue
        candidates.append(
            {
                "repair_candidate_id": candidate.get("repair_candidate_id"),
                "issue_type": issue_type,
                "severity": candidate.get("severity"),
                "recommended_action": candidate.get("recommended_action"),
                "rationale": candidate.get("rationale"),
                "refs": refs,
                "evidence": evidence,
            }
        )
    return {
        "exists": True,
        "requested_node_ids": sorted(requested_nodes),
        "bridge_candidate_count": len(candidates),
        "candidates": candidates[:12],
    }


def _bbox_gap_summary(left: list[Any] | None, right: list[Any] | None) -> dict[str, Any]:
    if not left or not right or len(left) < 4 or len(right) < 4:
        return {"distance": None, "axis_aligned": False}
    l1, t1, r1, b1 = [float(value) for value in left[:4]]
    l2, t2, r2, b2 = [float(value) for value in right[:4]]
    dx = max(l1 - r2, l2 - r1, 0.0)
    dy = max(t1 - b2, t2 - b1, 0.0)
    return {
        "distance": round((dx * dx + dy * dy) ** 0.5, 3),
        "dx": round(dx, 3),
        "dy": round(dy, 3),
        "axis_aligned": dx == 0.0 or dy == 0.0,
    }


def _inspect_single_pin_stub(debug_dir: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    topology = _read_json(_artifact_path(debug_dir, "topology"))
    nodes_payload = _read_json(_artifact_path(debug_dir, "active_nodes"))
    attachments_payload = _read_json(_artifact_path(debug_dir, "terminal_attachments"))
    if not isinstance(topology, dict):
        return {"exists": False, "stub_count": 0, "stubs": []}
    nodes = {
        str(node.get("node_id")): node
        for node in (nodes_payload or {}).get("nodes", topology.get("nodes", []))
        if node.get("node_id")
    }
    attachments_by_pin = {}
    if isinstance(attachments_payload, dict):
        attachments_by_pin = {
            str(attachment.get("pin_id")): _attachment_snapshot(attachment)
            for attachment in attachments_payload.get("attachments", [])
            if attachment.get("pin_id")
        }
    requested_node_ids = set(_as_id_list(arguments.get("net_ids") or arguments.get("node_ids") or arguments.get("node_id")))
    requested_pin_ids = set(_argument_ids(arguments, "pin_ids", "pin_id"))
    stubs = []
    target_nodes = [
        node
        for node in nodes.values()
        if len(set(_as_id_list(node.get("pin_ids")))) >= 2
    ]
    for node_id, node in nodes.items():
        pin_ids = _as_id_list(node.get("pin_ids"))
        if len(set(pin_ids)) != 1:
            continue
        if requested_node_ids and node_id not in requested_node_ids:
            continue
        if requested_pin_ids and not requested_pin_ids.intersection(pin_ids):
            continue
        pin_id = pin_ids[0]
        nearby = []
        for target in target_nodes:
            target_id = str(target.get("node_id"))
            if target_id == node_id:
                continue
            gap = _bbox_gap_summary(node.get("bbox"), target.get("bbox"))
            distance = gap.get("distance")
            if distance is None or float(distance) > 120:
                continue
            nearby.append(
                {
                    "node_id": target_id,
                    "pin_count": len(set(_as_id_list(target.get("pin_ids")))),
                    "pin_ids": target.get("pin_ids", []),
                    "bbox": target.get("bbox"),
                    "bbox_gap": gap,
                    "raw_component_ids": target.get("raw_component_ids", []),
                    "segment_ids": target.get("segment_ids", []),
                }
            )
        nearby.sort(key=lambda item: float(item.get("bbox_gap", {}).get("distance") or 999999.0))
        stubs.append(
            {
                "node_id": node_id,
                "pin_id": pin_id,
                "pin_ids": pin_ids,
                "component_ids": node.get("component_ids", []),
                "bbox": node.get("bbox"),
                "raw_component_ids": node.get("raw_component_ids", []),
                "segment_ids": node.get("segment_ids", []),
                "attachment": attachments_by_pin.get(pin_id),
                "nearby_supported_nodes": nearby[:5],
            }
        )
    return {
        "exists": True,
        "requested_node_ids": sorted(requested_node_ids),
        "requested_pin_ids": sorted(requested_pin_ids),
        "stub_count": len(stubs),
        "stubs": stubs[:12],
    }


def _candidate_pool_from_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for result in tool_results:
        summary = result.get("result_summary", {})
        candidates.extend(summary.get("top_candidates", []))
        candidates.extend(summary.get("candidates", []))
        groups = summary.get("candidate_groups", {})
        if isinstance(groups, dict):
            candidates.extend(groups.get("applyable", []))
            candidates.extend(groups.get("review_only", []))
    deduped = []
    seen = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("repair_candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        deduped.append(candidate)
    return deduped


def _hypothesis_ids_from_candidate_ids(
    tool_results: list[dict[str, Any]],
    candidate_ids: list[str],
) -> list[str]:
    selected_set = set(_as_id_list(candidate_ids))
    if not selected_set:
        return []
    ids = []
    for candidate in _candidate_pool_from_tool_results(tool_results):
        candidate_id = str(candidate.get("candidate_id") or candidate.get("repair_candidate_id") or "")
        if candidate_id not in selected_set:
            continue
        hypothesis_id = candidate.get("hypothesis_id") or candidate.get("geometry", {}).get("hypothesis_id")
        ids.extend(_as_id_list(hypothesis_id))
    return _as_id_list(ids)


def _candidate_lookup_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(candidate.get("candidate_id") or candidate.get("repair_candidate_id")): candidate
        for candidate in _candidate_pool_from_tool_results(tool_results)
        if candidate.get("candidate_id") or candidate.get("repair_candidate_id")
    }


def _repair_plan_candidate_ids(repair_plan: dict[str, Any] | None) -> list[str]:
    if not isinstance(repair_plan, dict):
        return []
    ids = []
    for step in repair_plan.get("steps", []) or []:
        if isinstance(step, dict) and step.get("candidate_id"):
            ids.append(str(step.get("candidate_id")))
    return _as_id_list(ids)


def _candidate_plan_step(
    candidate: dict[str, Any],
    index: int,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or candidate.get("repair_candidate_id") or "")
    repair_type = str(candidate.get("repair_type") or candidate.get("issue_type") or "")
    hypothesis_id = (
        candidate.get("hypothesis_id")
        or candidate.get("geometry", {}).get("hypothesis_id")
        or candidate.get("evidence", {}).get("hypothesis_id")
    )
    return {
        "step_id": f"S{index}",
        "candidate_id": candidate_id,
        "repair_type": repair_type,
        "hypothesis_id": hypothesis_id,
        "candidate_mode": candidate.get("candidate_mode"),
        "expected_improvement": candidate.get("improved_metrics", []),
        "depends_on": depends_on or [],
        "candidate": candidate,
    }


def _repair_plan_from_candidate_ids(
    candidate_ids: list[str],
    candidate_lookup: dict[str, dict[str, Any]],
    status: str = "pending_human_review",
) -> dict[str, Any]:
    steps = []
    for candidate_id in _as_id_list(candidate_ids):
        candidate = candidate_lookup.get(candidate_id)
        if not candidate:
            continue
        steps.append(_candidate_plan_step(candidate, len(steps) + 1))
    return {
        "plan_id": "PLAN1",
        "status": status if steps else "no_repair_plan",
        "steps": steps,
    }


def _normalize_repair_plan(
    content: dict[str, Any],
    candidate_lookup: dict[str, dict[str, Any]],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    raw_plan = content.get("repair_plan")
    if isinstance(raw_plan, dict):
        raw_steps = raw_plan.get("steps", []) or []
        steps = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                continue
            candidate_id = str(raw_step.get("candidate_id") or "").strip()
            candidate = candidate_lookup.get(candidate_id)
            if not candidate:
                continue
            step = _candidate_plan_step(candidate, len(steps) + 1, _as_id_list(raw_step.get("depends_on")))
            if raw_step.get("step_id"):
                step["step_id"] = str(raw_step.get("step_id"))
            if raw_step.get("hypothesis_id") and not step.get("hypothesis_id"):
                step["hypothesis_id"] = str(raw_step.get("hypothesis_id"))
            if raw_step.get("expected_improvement"):
                step["expected_improvement"] = _as_id_list(raw_step.get("expected_improvement"))
            steps.append(step)
        if steps:
            return {
                "plan_id": str(raw_plan.get("plan_id") or "PLAN1"),
                "status": str(raw_plan.get("status") or "pending_human_review"),
                "steps": steps,
            }

    fallback_plan = fallback.get("repair_plan")
    if isinstance(fallback_plan, dict):
        return fallback_plan
    return {"plan_id": "PLAN1", "status": "no_repair_plan", "steps": []}


def _validate_candidate_from_tool_results(
    tool_results: list[dict[str, Any]],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = str(arguments.get("candidate_id") or "").strip()
    candidates = _candidate_pool_from_tool_results(tool_results)
    selected = None
    if candidate_id:
        for candidate in candidates:
            if str(candidate.get("candidate_id") or candidate.get("repair_candidate_id")) == candidate_id:
                selected = candidate
                break
    elif candidates:
        selected = candidates[0]
    if not selected:
        return {
            "exists": False,
            "candidate_id": candidate_id,
            "error": "Candidate was not found in prior tool results.",
        }
    return {
        "exists": True,
        "candidate": selected,
        "validation": selected.get("validation", {}),
        "validation_result": selected.get("validation_result"),
        "ranking": selected.get("ranking", {}),
        "recommendation": selected.get("recommendation"),
        "candidate_mode": selected.get("candidate_mode"),
    }


def _available_tools() -> list[dict[str, Any]]:
    return [
        {
            "tool_name": "get_case_summary",
            "kind": "read_only",
            "description": "Read compact case-level quality, risk, and artifact summary.",
        },
        {
            "tool_name": "inspect_single_pin_nets",
            "kind": "read_only",
            "description": "Inspect single-pin nets and map them to pin/component identifiers.",
        },
        {
            "tool_name": "inspect_terminal_attachments",
            "kind": "read_only",
            "description": "Inspect terminal corridor attachment evidence for target pins or nets.",
        },
        {
            "tool_name": "inspect_component_class_candidates",
            "kind": "read_only",
            "description": "Inspect selected component classes and YOLO class alternatives.",
        },
        {
            "tool_name": "inspect_component_terminal_axis",
            "kind": "read_only",
            "description": "Inspect current terminal axis, pin positions, and attachment scores for a component.",
        },
        {
            "tool_name": "inspect_gap_bridge_candidates",
            "kind": "read_only",
            "description": "Inspect possible gap bridge candidates without merging nodes.",
        },
        {
            "tool_name": "inspect_single_pin_stub",
            "kind": "read_only",
            "description": "Inspect one-pin terminal stubs and nearby supported node evidence.",
        },
        {
            "tool_name": "dry_run_component_class_override",
            "kind": "dry_run",
            "description": "Validate one component class override hypothesis without mutating outputs.",
        },
        {
            "tool_name": "dry_run_component_axis_flip",
            "kind": "dry_run",
            "description": "Validate one component terminal axis flip hypothesis without mutating outputs.",
        },
        {
            "tool_name": "dry_run_reattach_pin",
            "kind": "dry_run",
            "description": "Validate one pin reattachment hypothesis without mutating outputs.",
        },
        {
            "tool_name": "dry_run_gap_bridge_merge",
            "kind": "dry_run",
            "description": "Validate one gap-bridge merge hypothesis without mutating outputs.",
        },
        {
            "tool_name": "dry_run_single_pin_stub_bridge",
            "kind": "dry_run",
            "description": "Validate one single-pin stub bridge hypothesis without mutating outputs.",
        },
        {
            "tool_name": "dry_run_merge_nodes",
            "kind": "dry_run",
            "description": "Validate one node merge hypothesis without mutating outputs.",
        },
        {
            "tool_name": "validate_candidate",
            "kind": "read_only",
            "description": "Read validation/ranking details for a previously returned candidate.",
        },
        {
            "tool_name": "repair_dry_run",
            "kind": "dry_run_fallback",
            "description": "Legacy bulk dry-run fallback for batch/eval or uncovered hypotheses.",
        },
    ]


def _available_tool_families() -> list[dict[str, Any]]:
    return [
        {
            "family": "terminal_axis_or_pin_orientation",
            "when_to_consider": [
                "a component has unmatched pins",
                "terminal attachment evidence is weak for the current pin axis",
                "another component axis may explain both pins better",
            ],
            "typical_sequence": [
                "inspect_component_terminal_axis",
                "dry_run_component_axis_flip",
            ],
            "related_tools": [
                "inspect_terminal_attachments",
                "dry_run_reattach_pin",
            ],
        },
        {
            "family": "single_pin_terminal_stub",
            "when_to_consider": [
                "a net has exactly one attached pin",
                "an open question mentions an isolated pin, one-pin stub, or nearby supported node",
                "a terminal appears to stop near a valid wire node but is not electrically merged",
            ],
            "typical_sequence": [
                "inspect_single_pin_stub",
                "dry_run_single_pin_stub_bridge",
            ],
            "related_tools": [
                "inspect_single_pin_nets",
                "inspect_terminal_attachments",
                "dry_run_merge_nodes",
            ],
        },
        {
            "family": "component_class_ambiguity",
            "when_to_consider": [
                "component class confidence is ambiguous",
                "the topology has no plausible source while a source-like symbol was detected as a passive component",
            ],
            "typical_sequence": [
                "inspect_component_class_candidates",
                "dry_run_component_class_override",
            ],
            "related_tools": [
                "get_case_summary",
            ],
        },
        {
            "family": "gap_between_supported_nodes",
            "when_to_consider": [
                "two supported graph nodes are near each other but remain separate",
                "wire evidence has a short geometric gap",
            ],
            "typical_sequence": [
                "inspect_gap_bridge_candidates",
                "dry_run_gap_bridge_merge",
            ],
            "related_tools": [
                "dry_run_merge_nodes",
            ],
        },
    ]


def _weak_signals_from_artifacts(
    case_summary: dict[str, Any] | None,
    audit_report: dict[str, Any] | None,
    repair_candidates: dict[str, Any] | None,
    topology: dict[str, Any] | None,
    netlist: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    seen = set()

    def add(code: str, severity: str = "info", refs: dict[str, Any] | None = None, detail: str | None = None) -> None:
        key = (code, json.dumps(refs or {}, sort_keys=True, ensure_ascii=False))
        if key in seen:
            return
        seen.add(key)
        signals.append(
            {
                "code": code,
                "severity": severity,
                "refs": refs or {},
                "detail": detail,
            }
        )

    components = topology.get("components", []) if isinstance(topology, dict) else []
    if components and not any(_is_power_class_name(component.get("class_name")) for component in components):
        add("missing_power_source", "info", detail="No recovered component has a power-source class.")
    for component in components:
        alternatives = component.get("class_alternatives", []) or []
        if alternatives:
            severity = "warning" if any(_is_power_class_name(item.get("class_name")) for item in alternatives) else "info"
            add(
                "component_class_ambiguity",
                severity,
                refs={"component_id": component.get("id")},
                detail="The detector produced overlapping class alternatives for this component.",
            )

    pins = topology.get("pins", []) if isinstance(topology, dict) else []
    for group in pins:
        if group.get("axis_source") == "fallback":
            add(
                "component_axis_from_fallback",
                "info",
                refs={"component_id": group.get("component_id"), "axis": group.get("axis")},
                detail="Terminal axis was selected by fallback rather than direct wire evidence.",
            )

    netlist_nets = netlist.get("nets", []) if isinstance(netlist, dict) else []
    single_pin_nets = [net.get("net_id") for net in netlist_nets if int(net.get("pin_count", 0) or 0) == 1]
    if single_pin_nets:
        add("single_pin_net", "warning", refs={"net_ids": single_pin_nets})

    if isinstance(case_summary, dict):
        review_focus = case_summary.get("review_focus", {})
        for risk in review_focus.get("risks", [])[:8]:
            add(
                str(risk.get("code") or "case_summary_risk"),
                str(risk.get("severity") or "info"),
                risk.get("refs", {}),
                str(risk.get("message") or ""),
            )

    if isinstance(repair_candidates, dict):
        for candidate in repair_candidates.get("candidates", [])[:12]:
            add(
                str(candidate.get("issue_type") or "repair_candidate"),
                str(candidate.get("severity") or "info"),
                candidate.get("refs", {}),
                str(candidate.get("rationale") or ""),
            )

    if isinstance(audit_report, dict):
        for item in audit_report.get("evidence", [])[:12]:
            add(
                str(item.get("code") or "audit_evidence"),
                str(item.get("severity") or "info"),
                item.get("refs", {}),
                str(item.get("message") or ""),
            )
    return signals[:20]


def _neutral_component_facts(topology: dict[str, Any] | None, netlist: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(topology, dict):
        return []
    pin_groups = _component_pin_groups(topology)
    net_refs = _component_net_refs(netlist)
    components = []
    for component in topology.get("components", [])[:16]:
        component_id = str(component.get("id"))
        pin_group = pin_groups.get(component_id, {})
        component_net = net_refs.get(component_id, {})
        class_candidates = [
            {
                "class_name": item.get("class_name"),
                "score": item.get("score"),
                "candidate_status": item.get("candidate_status"),
            }
            for item in component.get("class_candidates", [])[:5]
        ]
        components.append(
            {
                "component_id": component_id,
                "refdes": component.get("refdes"),
                "class_name": component.get("class_name"),
                "score": component.get("score"),
                "bbox": component.get("bbox"),
                "class_candidates": class_candidates,
                "class_alternative_count": len(component.get("class_alternatives", []) or []),
                "pin_count": pin_group.get("pin_count"),
                "axis": pin_group.get("axis"),
                "axis_source": pin_group.get("axis_source"),
                "axis_confidence": pin_group.get("confidence"),
                "pins": [
                    {
                        "pin_id": pin.get("pin_id"),
                        "side": pin.get("side"),
                        "x": pin.get("x"),
                        "y": pin.get("y"),
                    }
                    for pin in pin_group.get("pins", [])[:4]
                ],
                "net_ids": component_net.get("net_ids", []),
            }
        )
    return components


def _neutral_facts(
    case_summary: dict[str, Any] | None,
    topology: dict[str, Any] | None,
    netlist: dict[str, Any] | None,
    audit_inputs: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = case_summary.get("summary", {}) if isinstance(case_summary, dict) else {}
    components = topology.get("components", []) if isinstance(topology, dict) else []
    netlist_nets = netlist.get("nets", []) if isinstance(netlist, dict) else []
    all_pin_ids = []
    connected_pin_ids = set()
    if isinstance(topology, dict):
        for group in topology.get("pins", []):
            for pin in group.get("pins", []):
                if pin.get("pin_id"):
                    all_pin_ids.append(str(pin.get("pin_id")))
        for connection in topology.get("connections", []):
            if connection.get("pin_id"):
                connected_pin_ids.add(str(connection.get("pin_id")))
    return {
        "quality": {
            "review_status": case_summary.get("review_status") if isinstance(case_summary, dict) else None,
            "quality_label": summary.get("quality_label"),
            "export_success": summary.get("export_success"),
            "selected_node_source": summary.get("selected_node_source"),
            "fallback_used": summary.get("fallback_used"),
        },
        "counts": {
            "component_count": len(components) or summary.get("component_count"),
            "pin_count": len(all_pin_ids) or summary.get("pin_count"),
            "connection_count": len(topology.get("connections", [])) if isinstance(topology, dict) else summary.get("connection_count"),
            "node_count": len(topology.get("nodes", [])) if isinstance(topology, dict) else summary.get("node_count"),
            "net_count": len(netlist_nets) or summary.get("net_count"),
            "power_source_count": sum(1 for component in components if _is_power_class_name(component.get("class_name"))),
            "unmatched_pin_count": len([pin_id for pin_id in all_pin_ids if pin_id not in connected_pin_ids]),
            "single_pin_net_count": len([net for net in netlist_nets if int(net.get("pin_count", 0) or 0) == 1]),
        },
        "components": _neutral_component_facts(topology, netlist),
        "single_pin_net_ids": [
            net.get("net_id")
            for net in netlist_nets
            if int(net.get("pin_count", 0) or 0) == 1
        ],
        "audit_input_summary": audit_inputs.get("summary", {}) if isinstance(audit_inputs, dict) else {},
    }


def _build_observation(
    debug_dir: Path,
    audit_report: dict[str, Any] | None,
    memory_file: str | None = None,
    memory_limit: int = 5,
) -> dict[str, Any]:
    case_summary = _read_json(_artifact_path(debug_dir, "case_summary"))
    topology = _read_json(_artifact_path(debug_dir, "topology"))
    netlist = _read_json(_artifact_path(debug_dir, "netlist"))
    audit_inputs = _read_json(debug_dir / "audit_inputs.json")
    repair_candidates = _read_json(_artifact_path(debug_dir, "repair_candidates"))
    return {
        "case_summary": _compact_case_summary(case_summary if isinstance(case_summary, dict) else None),
        "facts": _neutral_facts(
            case_summary if isinstance(case_summary, dict) else None,
            topology if isinstance(topology, dict) else None,
            netlist if isinstance(netlist, dict) else None,
            audit_inputs if isinstance(audit_inputs, dict) else None,
        ),
        "weak_signals": _weak_signals_from_artifacts(
            case_summary if isinstance(case_summary, dict) else None,
            audit_report,
            repair_candidates if isinstance(repair_candidates, dict) else None,
            topology if isinstance(topology, dict) else None,
            netlist if isinstance(netlist, dict) else None,
        ),
        "audit_context": {
            "exists": isinstance(audit_report, dict),
            "overall_status": audit_report.get("overall_status") if isinstance(audit_report, dict) else None,
            "confidence": audit_report.get("confidence") if isinstance(audit_report, dict) else None,
        },
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


def _audit_has_class_or_power_issue(audit_report: dict[str, Any] | None) -> bool:
    if not audit_report:
        return False
    semantic = audit_report.get("topology_semantic_audit", {})
    if semantic.get("circuit_completeness") == "passive_or_missing_power_source":
        return True
    for item in audit_report.get("evidence", []):
        if item.get("code") in {"missing_power_source", "component_class_ambiguity"}:
            return True
    for action in audit_report.get("recommended_actions", []):
        if action.get("action_type") in {
            "confirm_missing_power_source",
            "review_component_class_alternative",
        }:
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
        if not completed_tool_names.intersection({"get_single_pin_nets", "inspect_single_pin_nets"}):
            required.append("inspect_single_pin_nets")
        if not completed_tool_names.intersection({"get_terminal_attachments", "inspect_terminal_attachments"}):
            required.append("inspect_terminal_attachments")
    if (
        _audit_has_terminal_attachment_issue(audit_report)
        and not completed_tool_names.intersection({"get_terminal_attachments", "inspect_terminal_attachments"})
    ):
        required.append("inspect_terminal_attachments")
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
                    "inspect_single_pin_nets",
                    "Audit found single-pin nets; inspect affected nets and pins.",
                    {"net_ids": single_pin_net_ids},
                )
            if "inspect_terminal_attachments" not in completed_tool_names:
                add_tool(
                    "inspect_terminal_attachments",
                    "Inspect terminal attachment evidence around the single-pin nets.",
                    {"net_ids": single_pin_net_ids},
                )

        if _audit_has_terminal_attachment_issue(audit_report):
            pin_ids = _pin_ids_from_audit(audit_report)
            notes.append(
                f"Terminal attachment issue targets pins: {', '.join(pin_ids) if pin_ids else 'all relevant pins'}."
            )
            add_tool(
                "inspect_terminal_attachments",
                "Audit points to terminal attachment confidence; inspect target pin evidence.",
                {"pin_ids": pin_ids},
            )

        if _audit_has_unsupported_evidence(audit_report):
            notes.append("Unsupported evidence should be reviewed before accepting a topology repair.")
            add_tool(
                "get_repair_candidates",
                "Audit mentions unsupported evidence; read deterministic review candidates.",
            )

        if _audit_has_class_or_power_issue(audit_report):
            notes.append("Class/power-source semantics should be checked against component class alternatives.")
            add_tool(
                "get_repair_candidates",
                "Audit mentions a passive-or-missing-power-source case; inspect class ambiguity candidates.",
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
            repair_arguments["allow_bulk_fallback"] = True
            repair_arguments["fallback_reason"] = "rule planner fallback"
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
        "deferred_questions": [],
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
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        if tool_name == "repair_dry_run" and not arguments.get("allow_bulk_fallback"):
            feedback.append(
                "Blocked legacy repair_dry_run. Use a granular dry-run tool first, "
                "or set allow_bulk_fallback=true with a reason if no granular tool applies."
            )
            continue
        if tool_name == "repair_dry_run" and prerequisites:
            feedback.append(
                "Blocked repair_dry_run until prerequisite evidence tools complete: "
                + ", ".join(prerequisites)
                + "."
            )
            continue
        if tool_name in GRANULAR_DRY_RUN_TOOL_NAMES and not arguments.get("hypothesis_id"):
            feedback.append(
                f"Blocked {tool_name}; every granular dry-run call must include hypothesis_id."
            )
            continue
        guarded.append(call)
    return guarded[:max_tool_calls_per_step], feedback


def _planner_self_consistency_feedback(
    tool_calls: list[dict[str, Any]],
    open_questions: list[str],
    deferred_questions: list[dict[str, Any]],
    completed_tool_names: set[str],
) -> list[str]:
    if tool_calls or not open_questions:
        return []

    feedback = []
    deferred_text = " ".join(
        str(item.get("question") or item.get("text") or item)
        for item in deferred_questions
        if isinstance(item, dict)
    ).lower()
    unresolved = [
        question
        for question in open_questions
        if str(question).strip() and str(question).strip().lower() not in deferred_text
    ]
    if unresolved:
        feedback.append(
            "Planner left open_questions but selected no tool calls. Call the next relevant "
            "tool, or move each unresolved question to deferred_questions with a concrete "
            "artifact-based reason."
        )

    question_text = " ".join(open_questions).lower()
    single_pin_terms = ("single-pin", "single pin", "one-pin", "stub", "isolated pin")
    if (
        any(term in question_text for term in single_pin_terms)
        and not completed_tool_names.intersection(
            {"inspect_single_pin_stub", "dry_run_single_pin_stub_bridge"}
        )
    ):
        feedback.append(
            "A single-pin/stub question remains unresolved. Use the single_pin_terminal_stub "
            "tool family, typically inspect_single_pin_stub before "
            "dry_run_single_pin_stub_bridge, or explicitly defer it with evidence."
        )
    return feedback


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
        "tool_families": _available_tool_families(),
        "observation": observation,
        "audit_context": observation.get("audit_context", {}),
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
    hypotheses = _as_dict_list(content.get("hypotheses"))
    open_questions = _as_string_list(content.get("open_questions"))
    deferred_questions = _as_dict_list(content.get("deferred_questions"))
    tool_calls = _normalize_tool_calls(
        content.get("tool_calls"),
        completed_tool_names=completed_tool_names,
        limit=max_tool_calls_per_step,
    )
    if len(hypotheses) == 1 and hypotheses[0].get("id"):
        for call in tool_calls:
            if call.get("tool_name") in GRANULAR_DRY_RUN_TOOL_NAMES:
                arguments = dict(call.get("arguments", {}))
                if not arguments.get("hypothesis_id"):
                    arguments["hypothesis_id"] = hypotheses[0]["id"]
                    call["arguments"] = arguments
    tool_calls, guard_feedback = _guard_planned_tool_calls(
        tool_calls,
        audit_report,
        completed_tool_names,
        max_tool_calls_per_step,
    )
    guard_feedback.extend(
        _planner_self_consistency_feedback(
            tool_calls,
            open_questions,
            deferred_questions,
            completed_tool_names,
        )
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
        "hypotheses": hypotheses,
        "open_questions": open_questions,
        "deferred_questions": deferred_questions,
        "guardrail_feedback": guard_feedback,
        "llm_result": llm_result,
    }


def _execute_named_tool(
    debug_dir: Path,
    output_dir: Path,
    audit_report: dict[str, Any] | None,
    call: dict[str, Any],
    index: int,
    prior_tool_results: list[dict[str, Any]] | None = None,
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

    if tool_name in {"get_single_pin_nets", "inspect_single_pin_nets"}:
        net_ids = _as_id_list(arguments.get("net_ids")) or _single_pin_net_ids_from_audit(audit_report)
        return {
            **base_result,
            "result_summary": _single_pin_net_summary(debug_dir, audit_report, net_ids),
        }

    if tool_name in {"get_terminal_attachments", "inspect_terminal_attachments"}:
        pin_ids = _argument_ids(arguments, "pin_ids", "pin_id")
        net_ids = _argument_ids(arguments, "net_ids", "net_id")
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

    if tool_name == "inspect_component_class_candidates":
        return {
            **base_result,
            "result_summary": _inspect_component_class_candidates(debug_dir, arguments),
        }

    if tool_name == "inspect_component_terminal_axis":
        return {
            **base_result,
            "result_summary": _inspect_component_terminal_axis(debug_dir, arguments),
        }

    if tool_name == "inspect_gap_bridge_candidates":
        return {
            **base_result,
            "result_summary": _inspect_gap_bridge_candidates(debug_dir, arguments),
        }

    if tool_name == "inspect_single_pin_stub":
        return {
            **base_result,
            "result_summary": _inspect_single_pin_stub(debug_dir, arguments),
        }

    if tool_name == "validate_candidate":
        return {
            **base_result,
            "result_summary": _validate_candidate_from_tool_results(prior_tool_results or [], arguments),
        }

    if tool_name in GRANULAR_DRY_RUN_TOOL_NAMES:
        tool_output_dir = output_dir / "tool_calls" / f"{index:02d}_{tool_name}"
        tool_result = run_granular_repair_dry_run(
            debug_dir,
            tool_name,
            arguments=arguments,
            output_dir=tool_output_dir,
        )
        repair_report = tool_result.get("report", {})
        hypothesis_id = arguments.get("hypothesis_id")
        if hypothesis_id:
            for candidate in repair_report.get("repair_candidates", []):
                candidate["hypothesis_id"] = hypothesis_id
                candidate.setdefault("geometry", {})["hypothesis_id"] = hypothesis_id
            for group in repair_report.get("candidate_groups", {}).values():
                if isinstance(group, list):
                    for candidate in group:
                        candidate["hypothesis_id"] = hypothesis_id
                        candidate.setdefault("geometry", {})["hypothesis_id"] = hypothesis_id
        return {
            **base_result,
            "outputs": tool_result.get("outputs", {}),
            "result_summary": _compact_repair_report(
                repair_report,
                target_net_ids=_as_id_list(
                    arguments.get("net_ids")
                    or arguments.get("net_id")
                    or arguments.get("node_ids")
                    or arguments.get("node_id")
                ),
                target_pin_ids=_argument_ids(arguments, "pin_ids", "pin_id"),
                repair_type=GRANULAR_TOOL_TO_REPAIR_TYPE.get(str(tool_name)),
            ),
        }

    tool_output_dir = output_dir / "tool_calls" / f"{index:02d}_repair_dry_run"
    tool_result = run_agent_repair_dry_run(debug_dir, output_dir=tool_output_dir)
    repair_report = tool_result.get("report", {})
    return {
        **base_result,
        "outputs": tool_result.get("outputs", {}),
        "result_summary": _compact_repair_report(
            repair_report,
            target_net_ids=_argument_ids(arguments, "net_ids", "net_id"),
            target_pin_ids=_argument_ids(arguments, "pin_ids", "pin_id"),
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
        if tool_name in {"get_single_pin_nets", "inspect_single_pin_nets"} and summary.get("exists"):
            before_nets = len(net_ids)
            before_pins = len(pin_ids)
            net_ids.extend(_as_id_list(summary.get("requested_net_ids")))
            net_ids.extend(
                _as_id_list([item.get("net_id") for item in summary.get("nets", []) if item.get("net_id")])
            )
            pin_ids.extend(_as_id_list(summary.get("related_pin_ids")))
            if len(net_ids) > before_nets or len(pin_ids) > before_pins:
                sources.append("get_single_pin_nets")
        elif tool_name in {"get_terminal_attachments", "inspect_terminal_attachments"} and summary.get("exists"):
            before_pins = len(pin_ids)
            pin_ids.extend(_as_id_list(summary.get("requested_pin_ids")))
            if len(pin_ids) > before_pins:
                sources.append("get_terminal_attachments")
        elif tool_name == "inspect_single_pin_stub" and summary.get("exists"):
            before_nets = len(net_ids)
            before_pins = len(pin_ids)
            for stub in summary.get("stubs", []):
                net_ids.extend(_as_id_list(stub.get("node_id")))
                pin_ids.extend(_as_id_list(stub.get("pin_id")))
            if len(net_ids) > before_nets or len(pin_ids) > before_pins:
                sources.append("inspect_single_pin_stub")

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

    if tool_name in {"get_terminal_attachments", "inspect_terminal_attachments"}:
        if not _argument_ids(arguments, "pin_ids", "pin_id") and not _argument_ids(arguments, "net_ids", "net_id"):
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

    if tool_name == "dry_run_component_class_override":
        if not arguments.get("component_id") or not (
            arguments.get("target_class") or arguments.get("alternate_class_name")
        ):
            for result in reversed(tool_results):
                if result.get("tool_name") != "inspect_component_class_candidates":
                    continue
                components = result.get("result_summary", {}).get("components", [])
                if len(components) != 1:
                    continue
                component = components[0]
                alternatives = component.get("class_alternatives", [])
                if not alternatives:
                    continue
                if not arguments.get("component_id"):
                    arguments["component_id"] = component.get("component_id")
                    added["component_id"] = component.get("component_id")
                if not arguments.get("target_class") and not arguments.get("alternate_class_name"):
                    arguments["target_class"] = alternatives[0].get("class_name")
                    added["target_class"] = alternatives[0].get("class_name")
                break

    if tool_name == "dry_run_component_axis_flip":
        if not arguments.get("component_id") or not (
            arguments.get("target_axis") or arguments.get("alternate_axis")
        ):
            for result in reversed(tool_results):
                if result.get("tool_name") != "inspect_component_terminal_axis":
                    continue
                components = result.get("result_summary", {}).get("components", [])
                if len(components) != 1:
                    continue
                component = components[0]
                if not arguments.get("component_id"):
                    arguments["component_id"] = component.get("component_id")
                    added["component_id"] = component.get("component_id")
                if not arguments.get("target_axis") and not arguments.get("alternate_axis"):
                    arguments["target_axis"] = component.get("alternate_axis")
                    added["target_axis"] = component.get("alternate_axis")
                break

    if tool_name == "dry_run_single_pin_stub_bridge":
        if not arguments.get("pin_id") or not arguments.get("target_node_id"):
            for result in reversed(tool_results):
                if result.get("tool_name") != "inspect_single_pin_stub":
                    continue
                stubs = result.get("result_summary", {}).get("stubs", [])
                if not stubs:
                    continue
                stub = stubs[0]
                nearby = stub.get("nearby_supported_nodes", [])
                if not nearby:
                    continue
                if not arguments.get("pin_id"):
                    arguments["pin_id"] = stub.get("pin_id")
                    added["pin_id"] = stub.get("pin_id")
                if not arguments.get("target_node_id"):
                    arguments["target_node_id"] = nearby[0].get("node_id")
                    added["target_node_id"] = nearby[0].get("node_id")
                if not arguments.get("node_ids"):
                    arguments["node_ids"] = [stub.get("node_id"), nearby[0].get("node_id")]
                    added["node_ids"] = arguments["node_ids"]
                break

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
    prior_tool_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    results = []
    prior_tool_results = prior_tool_results or []
    for index, call in enumerate(tool_calls, start=start_index):
        try:
            result = _execute_named_tool(
                debug_dir,
                output_dir,
                audit_report,
                call,
                index,
                prior_tool_results=prior_tool_results + results,
            )
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


def _merge_hypotheses(hypotheses: list[Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, item in enumerate(_as_dict_list(hypotheses), start=1):
        hypothesis_id = str(item.get("id") or item.get("hypothesis_id") or f"H{index}")
        if hypothesis_id not in merged:
            order.append(hypothesis_id)
        merged[hypothesis_id] = {
            **merged.get(hypothesis_id, {}),
            **item,
            "id": hypothesis_id,
        }
    return [merged[hypothesis_id] for hypothesis_id in order]


def _aggregate_planner_steps(planner_steps: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
    tool_calls = []
    notes = []
    llm_used = False
    llm_results = []
    planner_kinds = []
    hypotheses = []
    open_questions = []
    deferred_questions = []
    guardrail_feedback = []
    for step in planner_steps:
        planner_kinds.append(str(step.get("planner_kind")))
        tool_calls.extend(step.get("tool_calls", []))
        notes.extend(step.get("planner_notes", []))
        hypotheses.extend(step.get("hypotheses", []))
        open_questions.extend(step.get("open_questions", []))
        deferred_questions.extend(step.get("deferred_questions", []))
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
        "hypotheses": _merge_hypotheses(hypotheses),
        "hypothesis_history": _as_dict_list(hypotheses),
        "open_questions": _as_string_list(open_questions),
        "deferred_questions": _as_dict_list(deferred_questions),
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
            prior_tool_results=tool_results,
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
    return any(
        result.get("tool_name") == "repair_dry_run"
        or result.get("tool_name") in GRANULAR_DRY_RUN_TOOL_NAMES
        for result in tool_results
    )


def _candidate_is_applyable(candidate: dict[str, Any] | None) -> bool:
    return isinstance(candidate, dict) and candidate.get("repair_type") in APPLYABLE_REPAIR_TYPES


def _critic_review(
    audit_report: dict[str, Any] | None,
    planner: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    called_names = {call.get("tool_name") for call in planner.get("tool_calls", [])}
    result_names = {result.get("tool_name") for result in tool_results if result.get("status") == "completed"}
    issues = []
    notes = []
    dry_run_names = set(GRANULAR_DRY_RUN_TOOL_NAMES) | {"repair_dry_run"}

    if _audit_has_actionable_issues(audit_report) and not called_names.intersection(dry_run_names):
        issues.append("Actionable audit issues exist but no dry-run validation tool was planned.")
    if (
        _single_pin_net_ids_from_audit(audit_report)
        and not called_names.intersection(
            {
                "inspect_single_pin_nets",
                "get_single_pin_nets",
                "inspect_single_pin_stub",
                "dry_run_merge_nodes",
                "dry_run_single_pin_stub_bridge",
            }
        )
        and not _top_candidate(tool_results)
    ):
        issues.append("Single-pin nets exist but no single-pin/stub inspection or merge dry-run was planned.")
    if (
        _audit_has_terminal_attachment_issue(audit_report)
        and not called_names.intersection(
            {
                "inspect_terminal_attachments",
                "get_terminal_attachments",
                "inspect_component_terminal_axis",
                "dry_run_component_axis_flip",
                "dry_run_reattach_pin",
            }
        )
        and not _top_candidate(tool_results)
    ):
        issues.append("Terminal attachment signals exist but no terminal inspection or terminal repair dry-run was planned.")
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
    candidate_lookup = _candidate_lookup_from_tool_results(tool_results)
    if critic.get("issues"):
        decision = "needs_more_evidence"
        selected_ids = []
        rationale = "Guardrail critic found issues in the planned or executed tool sequence."
    elif not planner.get("tool_calls") or (
        not repair_called and not _audit_has_actionable_issues(audit_report)
    ):
        decision = "no_action"
        selected_ids = []
        rationale = "No actionable audit issue required repair dry-run."
    elif repair_called and not top:
        decision = "no_candidate_found"
        selected_ids = []
        rationale = "Repair dry-run returned no actionable ranked candidates."
    elif top and top.get("recommendation") == "accept_for_human_review":
        decision = (
            "repair_candidate_ready_for_human_review"
            if _candidate_is_applyable(top)
            else "review_only_issue_for_human_review"
        )
        selected_ids = [str(top.get("candidate_id"))]
        rationale = f"Top candidate {top.get('candidate_id')} is validated and ranked for human review."
    else:
        decision = "needs_more_evidence"
        selected_ids = [str(top.get("candidate_id"))] if top and top.get("candidate_id") else []
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
        "repair_plan": _repair_plan_from_candidate_ids(selected_ids, candidate_lookup),
        "selected_hypothesis_ids": [],
        "rationale": rationale,
        "confirmed_by_artifacts": facts,
        "risks": critic.get("issues", []),
        "next_actions": [
            "Open the dry-run report and confirm the selected candidate before any topology correction."
        ] if selected_ids else ["No topology correction is recommended by this advisor run."],
        "hypothesis_assessment": [],
        "llm_result": {
            "used": False,
            "error": None,
        },
    }


def _normalize_review_content(
    content: dict[str, Any],
    known_ids: set[str],
    fallback: dict[str, Any],
    candidate_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    allowed_decisions = {
        "repair_candidate_ready_for_human_review",
        "review_only_issue_for_human_review",
        "needs_more_evidence",
        "no_candidate_found",
        "no_action",
    }
    decision = str(content.get("final_decision") or fallback["final_decision"])
    if decision == "candidate_ready_for_human_review":
        decision = str(fallback.get("final_decision") or "review_only_issue_for_human_review")
    if decision not in allowed_decisions:
        decision = fallback["final_decision"]
    if (
        fallback.get("final_decision") in {
            "repair_candidate_ready_for_human_review",
            "review_only_issue_for_human_review",
        }
        and decision in {"no_action", "no_candidate_found"}
    ):
        decision = fallback["final_decision"]
    repair_plan = _normalize_repair_plan(content, candidate_lookup, fallback)
    if decision in {
        "repair_candidate_ready_for_human_review",
        "review_only_issue_for_human_review",
    } and not _repair_plan_candidate_ids(repair_plan):
        repair_plan = fallback.get("repair_plan", {"plan_id": "PLAN1", "status": "no_repair_plan", "steps": []})
    return {
        "final_decision": decision,
        "repair_plan": repair_plan,
        "selected_hypothesis_ids": _as_id_list(content.get("selected_hypothesis_ids")),
        "rationale": str(content.get("rationale") or fallback["rationale"]),
        "confirmed_by_artifacts": _as_string_list(
            content.get("confirmed_by_artifacts") or fallback["confirmed_by_artifacts"]
        ),
        "risks": _as_string_list(content.get("risks") or fallback["risks"]),
        "next_actions": _as_string_list(content.get("next_actions") or fallback["next_actions"]),
        "hypothesis_assessment": _as_dict_list(content.get("hypothesis_assessment")),
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
        "audit_context": observation.get("audit_context", {}),
        "planner": {
            "planner_kind": planner.get("planner_kind"),
            "tool_calls": planner.get("tool_calls", []),
            "planner_notes": planner.get("planner_notes", []),
            "hypotheses": planner.get("hypotheses", []),
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
    candidate_lookup = _candidate_lookup_from_tool_results(tool_results)
    normalized = _normalize_review_content(content, _known_candidate_ids(tool_results), fallback, candidate_lookup)
    if not normalized.get("selected_hypothesis_ids"):
        normalized["selected_hypothesis_ids"] = _hypothesis_ids_from_candidate_ids(
            tool_results,
            _repair_plan_candidate_ids(normalized.get("repair_plan")),
        )
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
                    "repair_plan_step_count": len(
                        state.get("reviewer", {}).get("repair_plan", {}).get("steps", [])
                    ),
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
        if result.get("tool_name") in {"get_single_pin_nets", "inspect_single_pin_nets"} and summary.get("exists"):
            single_pin_summary = summary
        if result.get("tool_name") in {"get_terminal_attachments", "inspect_terminal_attachments"} and summary.get("exists"):
            for attachment in summary.get("attachments", []):
                pin_id = attachment.get("pin_id")
                if pin_id:
                    attachments_by_pin[str(pin_id)] = attachment
        if (
            result.get("tool_name") == "repair_dry_run"
            or result.get("tool_name") in GRANULAR_DRY_RUN_TOOL_NAMES
        ) and summary.get("exists"):
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

    repair_plan = reviewer.get("repair_plan", {"plan_id": "PLAN1", "status": "no_repair_plan", "steps": []})
    selected_ids = set(_repair_plan_candidate_ids(repair_plan))
    selected_candidates = [
        candidate for candidate in candidates if str(candidate.get("candidate_id")) in selected_ids
    ]
    selected_by_id = {str(candidate.get("candidate_id")): candidate for candidate in selected_candidates}
    plan_steps = []
    for step in repair_plan.get("steps", []) if isinstance(repair_plan, dict) else []:
        if not isinstance(step, dict):
            continue
        candidate_id = str(step.get("candidate_id") or "")
        plan_steps.append(
            {
                **{key: value for key, value in step.items() if key != "candidate"},
                "candidate": selected_by_id.get(candidate_id) or step.get("candidate"),
            }
        )
    applyable_candidates = [
        candidate for candidate in candidates if candidate.get("candidate_mode") == "applyable"
    ]
    review_only_candidates = [
        candidate for candidate in candidates if candidate.get("candidate_mode") == "review_only"
    ]

    return {
        "schema_version": "3.4-human-review-dossier",
        "case_id": audit_report.get("case_id") if audit_report else None,
        "primary_issue": audit_report.get("primary_issue") if audit_report else None,
        "suspected_stage": audit_report.get("suspected_stage") if audit_report else None,
        "final_decision": reviewer.get("final_decision"),
        "repair_plan": {
            **repair_plan,
            "steps": plan_steps,
        },
        "plain_language_summary": (
            "The advisor prepared a dry-run repair plan that should be reviewed by a human."
            if selected_candidates
            else "The advisor did not prepare a repair plan for this run."
        ),
        "nets": nets,
        "selected_candidates": selected_candidates,
        "candidate_groups": {
            "applyable": applyable_candidates[:8],
            "review_only": review_only_candidates[:8],
        },
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
        f"- repair plan: `{dossier.get('repair_plan', {}).get('plan_id')}` "
        f"status=`{dossier.get('repair_plan', {}).get('status')}`",
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

    lines.extend(["", "## Repair Plan", ""])
    plan_steps = dossier.get("repair_plan", {}).get("steps", [])
    if not plan_steps:
        lines.append("- No repair plan.")
    for step in plan_steps:
        candidate = step.get("candidate", {}) if isinstance(step, dict) else {}
        lines.append(
            f"- `{step.get('step_id')}` candidate=`{step.get('candidate_id')}` / `{step.get('repair_type')}` "
            f"depends_on=`{step.get('depends_on', [])}`"
        )
        lines.append(
            f"  - recommendation=`{candidate.get('recommendation')}`, "
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

    groups = dossier.get("candidate_groups", {})
    if groups:
        lines.extend(["", "## Candidate Groups", ""])
        lines.append(f"- applyable: `{len(groups.get('applyable', []))}`")
        for candidate in groups.get("applyable", [])[:5]:
            lines.append(
                f"  - `{candidate.get('candidate_id')}` / `{candidate.get('repair_type')}` "
                f"ranking_score=`{candidate.get('ranking_score')}`"
            )
        lines.append(f"- review_only: `{len(groups.get('review_only', []))}`")
        for candidate in groups.get("review_only", [])[:5]:
            lines.append(
                f"  - `{candidate.get('candidate_id')}` / `{candidate.get('repair_type')}` "
                f"ranking_score=`{candidate.get('ranking_score')}`"
            )

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
        f"- repair plan: `{report.get('repair_plan', {}).get('plan_id')}` "
        f"status=`{report.get('repair_plan', {}).get('status')}`",
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
    if planner.get("hypotheses"):
        lines.append("- hypotheses:")
        for hypothesis in planner.get("hypotheses", [])[:8]:
            if isinstance(hypothesis, dict):
                lines.append(
                    f"  - `{hypothesis.get('id')}`: {hypothesis.get('claim')} "
                    f"(uncertainty=`{hypothesis.get('uncertainty')}`)"
                )
            else:
                lines.append(f"  - {hypothesis}")
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
            for question in step.get("open_questions", [])[:3]:
                lines.append(f"    - open question: {question}")
            for item in step.get("deferred_questions", [])[:3]:
                if isinstance(item, dict):
                    lines.append(
                        f"    - deferred: {item.get('question') or item.get('text')} "
                        f"({item.get('reason')})"
                    )
                else:
                    lines.append(f"    - deferred: {item}")
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
        if tool_name in {"get_terminal_attachments", "inspect_terminal_attachments"}:
            metric_label = "attachments"
            metric_count = summary.get("attachment_count", len(summary.get("attachments", [])))
        elif tool_name in {"get_single_pin_nets", "inspect_single_pin_nets"}:
            metric_label = "single_pin_nets"
            metric_count = summary.get("single_pin_net_count", len(summary.get("nets", [])))
        elif tool_name == "get_repair_candidates":
            metric_label = "review_candidates"
            metric_count = summary.get("summary", {}).get("candidate_count", len(summary.get("candidates", [])))
        elif tool_name == "inspect_component_class_candidates":
            metric_label = "components"
            metric_count = summary.get("component_count", len(summary.get("components", [])))
        elif tool_name == "inspect_component_terminal_axis":
            metric_label = "components"
            metric_count = summary.get("component_count", len(summary.get("components", [])))
        elif tool_name == "inspect_gap_bridge_candidates":
            metric_label = "bridge_candidates"
            metric_count = summary.get("bridge_candidate_count", len(summary.get("candidates", [])))
        elif tool_name == "inspect_single_pin_stub":
            metric_label = "stubs"
            metric_count = summary.get("stub_count", len(summary.get("stubs", [])))
        elif tool_name == "validate_candidate":
            metric_label = "candidate"
            metric_count = 1 if summary.get("exists") else 0
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
                f"mode=`{candidate.get('candidate_mode')}` "
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
    if reviewer.get("repair_plan"):
        plan = reviewer.get("repair_plan", {})
        lines.append(f"- repair plan: `{plan.get('plan_id')}` status=`{plan.get('status')}`")
        for step in plan.get("steps", [])[:8]:
            lines.append(
                f"  - `{step.get('step_id')}` candidate=`{step.get('candidate_id')}` "
                f"type=`{step.get('repair_type')}` depends_on=`{step.get('depends_on', [])}`"
            )
    if reviewer.get("selected_hypothesis_ids"):
        lines.append(f"- selected hypotheses: `{reviewer.get('selected_hypothesis_ids')}`")
    lines.append(f"- rationale: {reviewer.get('rationale')}")
    if reviewer.get("hypothesis_assessment"):
        lines.append("- hypothesis assessment:")
        for item in reviewer.get("hypothesis_assessment", [])[:8]:
            lines.append(
                f"  - `{item.get('hypothesis_id')}` status=`{item.get('status')}`: {item.get('reason')}"
            )
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
        for step in dossier.get("repair_plan", {}).get("steps", [])[:3]:
            candidate = step.get("candidate", {}) if isinstance(step, dict) else {}
            lines.append(
                f"- plan step `{step.get('step_id')}` candidate `{step.get('candidate_id')}` targets nodes "
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
    repair_plan = reviewer.get("repair_plan", {"plan_id": "PLAN1", "status": "no_repair_plan", "steps": []})
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
        "repair_plan": _scrub_secrets(repair_plan),
        "selected_hypothesis_ids": reviewer.get("selected_hypothesis_ids", []),
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
            prior_tool_results=list(state.get("tool_results", [])),
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
                "repair_plan_step_count": len(
                    state.get("reviewer", {}).get("repair_plan", {}).get("steps", [])
                ),
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
    """Run the hypothesis-driven agent repair advisor workflow."""
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
