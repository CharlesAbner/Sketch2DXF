"""Topology-level semantic checks for artifact-driven agent workflows."""

from __future__ import annotations

from typing import Any

from src.agent_workflow.schemas import SemanticRisk, TopologySemanticAudit


def _risk(risk_type: str, severity: str, message: str, refs: dict | None = None) -> SemanticRisk:
    return SemanticRisk(
        risk_type=risk_type,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        refs=refs or {},
    )


def _circuit_completeness(facts: dict[str, Any], risks: list[SemanticRisk]) -> str:
    if not facts.get("core_artifacts_ok", True):
        return "insufficient_artifacts"
    if any(risk.severity == "error" for risk in risks):
        return "invalid_or_incomplete_topology"
    if facts.get("single_pin_nets"):
        return "suspicious_open_circuit"
    if int(facts.get("connected_component_count", 0)) > 1:
        return "disconnected_subcircuits"
    if not facts.get("has_power_source"):
        return "passive_or_missing_power_source"
    if facts.get("has_power_source") and not facts.get("unmatched_pin_ids"):
        return "complete_powered_circuit"
    return "review_required"


def build_topology_semantic_audit(facts: dict[str, Any]) -> TopologySemanticAudit:
    """Build deterministic topology semantic findings."""
    risks: list[SemanticRisk] = []
    unmatched_pin_ids = facts.get("unmatched_pin_ids", [])
    single_pin_nets = facts.get("single_pin_nets", [])
    zero_pin_nets = facts.get("zero_pin_nets", [])
    connected_groups = facts.get("connected_component_groups", [])
    self_shorts = facts.get("component_self_shorts", [])
    source_shorts = facts.get("source_terminal_shorts", [])

    if not facts.get("core_artifacts_ok", True):
        risks.append(
            _risk(
                "insufficient_artifacts",
                "error",
                "Required debug artifacts are missing; the audit cannot trust topology-level conclusions.",
                {"missing_required_artifacts": facts.get("missing_required_artifacts", [])},
            )
        )

    if unmatched_pin_ids:
        risks.append(
            _risk(
                "unmatched_pins",
                "error",
                f"{len(unmatched_pin_ids)} terminal(s) have no recovered net connection.",
                {"pin_ids": unmatched_pin_ids},
            )
        )
    if single_pin_nets:
        risks.append(
            _risk(
                "single_pin_nets",
                "warning",
                f"{len(single_pin_nets)} net(s) contain only one terminal.",
                {"net_ids": single_pin_nets},
            )
        )
    if zero_pin_nets:
        risks.append(
            _risk(
                "zero_pin_nets",
                "error",
                f"{len(zero_pin_nets)} net(s) contain no terminals.",
                {"net_ids": zero_pin_nets},
            )
        )
    if len(connected_groups) > 1:
        risks.append(
            _risk(
                "disconnected_topology",
                "warning",
                f"The recovered circuit contains {len(connected_groups)} disconnected component groups.",
                {"component_groups": connected_groups},
            )
        )
    if self_shorts:
        risks.append(
            _risk(
                "component_self_short",
                "warning",
                f"{len(self_shorts)} component(s) have multiple pins on the same net.",
                {"components": self_shorts},
            )
        )
    if source_shorts:
        risks.append(
            _risk(
                "source_terminal_short",
                "error",
                "A power-source component has terminals on the same net.",
                {"components": source_shorts},
            )
        )
    if not facts.get("has_power_source"):
        risks.append(
            _risk(
                "missing_power_source",
                "info",
                "No power source was detected; this may be valid for a passive subcircuit.",
            )
        )
    if not facts.get("has_ground"):
        risks.append(
            _risk(
                "missing_ground",
                "info",
                "No ground symbol was detected; this is informational because ground detection may be out of scope.",
            )
        )

    semantic_facts = {
        **facts,
        "connected_component_count": len(connected_groups),
    }
    return TopologySemanticAudit(
        has_power_source=bool(facts.get("has_power_source")),
        has_ground=bool(facts.get("has_ground")),
        all_pins_connected=not bool(unmatched_pin_ids),
        unmatched_pin_ids=unmatched_pin_ids,
        single_pin_nets=single_pin_nets,
        zero_pin_nets=zero_pin_nets,
        connected_component_count=len(connected_groups),
        disconnected_component_groups=connected_groups if len(connected_groups) > 1 else [],
        component_self_shorts=self_shorts,
        source_terminal_shorts=source_shorts,
        circuit_completeness=_circuit_completeness(semantic_facts, risks),
        semantic_risks=risks,
    )
