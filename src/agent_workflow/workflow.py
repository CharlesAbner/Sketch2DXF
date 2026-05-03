"""Standalone artifact audit workflow used by the current agent advisor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.agent_workflow.artifact_loader import load_case_artifacts
from src.agent_workflow.fact_extractor import extract_case_facts
from src.agent_workflow.llm_client import enhance_with_llm
from src.agent_workflow.report_writer import write_agent_audit_outputs
from src.agent_workflow.rule_diagnoser import (
    build_recommended_actions,
    build_stage_diagnoses,
    choose_overall_status,
)
from src.agent_workflow.schemas import AgentAuditReport
from src.agent_workflow.topology_semantics import build_topology_semantic_audit


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


def _report_without_llm(report: AgentAuditReport) -> dict[str, Any]:
    data = report.dict()
    data["llm_assessment"] = {"used": False, "backend": report.backend}
    return data


def _build_report(
    loaded: dict[str, Any],
    facts: dict[str, Any],
    backend: str,
    workflow_engine: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> AgentAuditReport:
    semantic_audit = build_topology_semantic_audit(facts)
    diagnoses = build_stage_diagnoses(facts, semantic_audit)
    actions = build_recommended_actions(facts, semantic_audit, diagnoses)
    overall_status, primary_issue, suspected_stage, confidence = choose_overall_status(
        facts,
        semantic_audit,
        diagnoses,
    )

    report = AgentAuditReport(
        case_id=str(loaded.get("case_id")),
        image_path=loaded.get("image_path"),
        debug_dir=str(loaded.get("debug_dir")),
        backend=backend,
        workflow_engine=workflow_engine,
        llm_used=False,
        overall_status=overall_status,  # type: ignore[arg-type]
        confidence=round(float(confidence), 3),
        primary_issue=primary_issue,
        suspected_stage=suspected_stage,  # type: ignore[arg-type]
        known_stressors=list(loaded.get("known_stressors", [])),
        topology_semantic_audit=semantic_audit,
        stage_diagnoses=diagnoses,
        evidence=facts.get("evidence_items", []),
        recommended_actions=actions,
        artifacts_used=loaded.get("artifact_paths", {}),
    )

    llm_assessment = enhance_with_llm(
        facts,
        _report_without_llm(report),
        backend=backend,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )
    report.llm_assessment = llm_assessment
    report.llm_used = bool(llm_assessment.used)
    return report


def _run_local(
    debug_dir: str | Path,
    backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
) -> tuple[AgentAuditReport, dict[str, Any]]:
    loaded = load_case_artifacts(debug_dir)
    facts = extract_case_facts(loaded)
    report = _build_report(
        loaded,
        facts,
        backend=backend,
        workflow_engine="local",
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
    )
    return report, {"loaded": loaded, "facts": facts}


def _run_langgraph(
    debug_dir: str | Path,
    backend: str,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    api_key_env: str | None,
    allow_fallback: bool,
) -> tuple[AgentAuditReport, dict[str, Any]]:
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except Exception as exc:
        if not allow_fallback:
            raise RuntimeError(
                "LangGraph is required for --workflow-engine langgraph. "
                "Install langgraph or use --workflow-engine auto/local."
            ) from exc
        return _run_local(debug_dir, backend, model, base_url, api_key, api_key_env)

    def load_node(state: dict[str, Any]) -> dict[str, Any]:
        state["loaded"] = load_case_artifacts(state["debug_dir"])
        return state

    def facts_node(state: dict[str, Any]) -> dict[str, Any]:
        state["facts"] = extract_case_facts(state["loaded"])
        return state

    def report_node(state: dict[str, Any]) -> dict[str, Any]:
        state["report"] = _build_report(
            state["loaded"],
            state["facts"],
            backend=state["backend"],
            workflow_engine="langgraph",
            model=state.get("model"),
            base_url=state.get("base_url"),
            api_key=state.get("api_key"),
            api_key_env=state.get("api_key_env"),
        )
        return state

    graph = StateGraph(dict)
    graph.add_node("load_artifacts", load_node)
    graph.add_node("extract_facts", facts_node)
    graph.add_node("build_report", report_node)
    graph.set_entry_point("load_artifacts")
    graph.add_edge("load_artifacts", "extract_facts")
    graph.add_edge("extract_facts", "build_report")
    graph.add_edge("build_report", END)
    app = graph.compile()
    state = app.invoke(
        {
            "debug_dir": str(debug_dir),
            "backend": backend,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "api_key_env": api_key_env,
        }
    )
    return state["report"], state


def run_agent_audit_workflow(
    debug_dir: str | Path,
    output_dir: str | Path | None = None,
    backend: str = "rule",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_env: str | None = None,
    workflow_engine: str = "auto",
) -> dict[str, Any]:
    """
    Run the standalone audit workflow and write JSON/Markdown reports.

    `backend=rule` is fully offline. LLM backends use the OpenAI-compatible
    chat-completions SDK path and never write the API key to report files.
    """
    if workflow_engine not in {"auto", "local", "langgraph"}:
        raise ValueError(f"Unknown workflow_engine: {workflow_engine}")

    if workflow_engine == "local":
        report, state = _run_local(debug_dir, backend, model, base_url, api_key, api_key_env)
    else:
        report, state = _run_langgraph(
            debug_dir,
            backend,
            model,
            base_url,
            api_key,
            api_key_env,
            allow_fallback=(workflow_engine == "auto"),
        )
        if workflow_engine == "auto" and report.workflow_engine == "local":
            report.workflow_engine = "local"

    out_dir = Path(output_dir) if output_dir else Path(debug_dir)
    outputs = write_agent_audit_outputs(report, out_dir)
    return {
        "report": report.dict(),
        "outputs": outputs,
        "state": _scrub_secrets(_model_to_dict(state)),
    }
