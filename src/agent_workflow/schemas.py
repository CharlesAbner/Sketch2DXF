"""Pydantic schemas for the standalone artifact audit workflow."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Severity = Literal["error", "warning", "info"]
OverallStatus = Literal["pass", "needs_review", "fail"]
SuspectedStage = Literal[
    "artifact_loading",
    "proposal",
    "wire_evidence",
    "terminal_attachment",
    "supported_graph",
    "graph_node_merge",
    "topology_semantics",
    "capability_boundary",
    "export",
    "unknown",
]


class EvidenceItem(BaseModel):
    """A compact fact used to justify a diagnosis."""

    code: str
    message: str
    refs: dict[str, Any] = Field(default_factory=dict)


class SemanticRisk(BaseModel):
    """A topology-level risk, independent of visual extraction details."""

    risk_type: str
    severity: Severity
    message: str
    refs: dict[str, Any] = Field(default_factory=dict)


class TopologySemanticAudit(BaseModel):
    """Deterministic semantic checks on the recovered circuit topology."""

    has_power_source: bool
    has_ground: bool
    all_pins_connected: bool
    unmatched_pin_ids: list[str] = Field(default_factory=list)
    single_pin_nets: list[str] = Field(default_factory=list)
    zero_pin_nets: list[str] = Field(default_factory=list)
    connected_component_count: int = 0
    disconnected_component_groups: list[list[str]] = Field(default_factory=list)
    component_self_shorts: list[dict[str, Any]] = Field(default_factory=list)
    source_terminal_shorts: list[dict[str, Any]] = Field(default_factory=list)
    circuit_completeness: str
    semantic_risks: list[SemanticRisk] = Field(default_factory=list)


class StageDiagnosis(BaseModel):
    """A stage-local diagnosis produced from extracted facts."""

    suspected_stage: SuspectedStage
    issue_type: str
    severity: Severity
    confidence: float
    rationale: str
    evidence_codes: list[str] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    """A non-mutating next action for humans or future repair tools."""

    action_id: str
    action_type: str
    priority: Literal["high", "medium", "low"]
    description: str
    target_refs: dict[str, Any] = Field(default_factory=dict)
    requires_human_review: bool = True


class LlmAssessment(BaseModel):
    """Optional LLM-generated interpretation of deterministic facts."""

    used: bool = False
    backend: str = "rule"
    model: str | None = None
    base_url: str | None = None
    summary: str | None = None
    suspected_root_cause: str | None = None
    confirmed_by_artifacts: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    low_priority_notes: list[str] = Field(default_factory=list)
    reasoning_notes: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    error: str | None = None


class AgentAuditReport(BaseModel):
    """Standalone audit report retained for the current agent advisor."""

    schema_version: str = "3.0-agent-audit"
    case_id: str
    image_path: str | None = None
    debug_dir: str
    backend: str = "rule"
    workflow_engine: str = "local"
    llm_used: bool = False
    overall_status: OverallStatus
    confidence: float
    primary_issue: str
    suspected_stage: SuspectedStage
    known_stressors: list[str] = Field(default_factory=list)
    topology_semantic_audit: TopologySemanticAudit
    stage_diagnoses: list[StageDiagnosis] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    llm_assessment: LlmAssessment = Field(default_factory=LlmAssessment)
    artifacts_used: dict[str, str] = Field(default_factory=dict)
