"""Shared topology candidate validation for repair dry-run tools."""

from __future__ import annotations

from typing import Any


VALIDATOR_VERSION = "3.1-step3-topology-candidate-validator"

METRIC_DIRECTIONS = {
    "single_pin_net_count": "lower_is_better",
    "zero_pin_net_count": "lower_is_better",
    "unmatched_pin_count": "lower_is_better",
    "component_self_short_count": "lower_is_better",
    "source_terminal_short_count": "lower_is_better",
    "connected_component_count": "lower_is_better",
}

INVARIANT_METRICS = ("component_count", "pin_count")


def _numeric(value: Any) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _metric_delta(metric: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_value = before.get(metric)
    after_value = after.get(metric)
    delta = _numeric(after_value) - _numeric(before_value)
    direction = METRIC_DIRECTIONS.get(metric, "invariant")

    if metric in INVARIANT_METRICS:
        outcome = "unchanged" if before_value == after_value else "regressed"
    elif delta == 0:
        outcome = "unchanged"
    elif direction == "lower_is_better":
        outcome = "improved" if delta < 0 else "regressed"
    else:
        outcome = "changed"

    return {
        "metric": metric,
        "before": before_value,
        "after": after_value,
        "delta": delta,
        "direction": direction,
        "outcome": outcome,
    }


def _blocking_issues(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    issues = []
    if after.get("zero_pin_net_count", 0) > before.get("zero_pin_net_count", 0):
        issues.append("introduces_zero_pin_net")
    if after.get("unmatched_pin_count", 0) > before.get("unmatched_pin_count", 0):
        issues.append("introduces_unmatched_pin")
    if after.get("component_self_short_count", 0) > before.get("component_self_short_count", 0):
        issues.append("introduces_component_self_short")
    if after.get("source_terminal_short_count", 0) > before.get("source_terminal_short_count", 0):
        issues.append("introduces_source_terminal_short")
    if after.get("connected_component_count", 0) > before.get("connected_component_count", 0):
        issues.append("introduces_disconnected_topology")
    if after.get("component_count") != before.get("component_count"):
        issues.append("changes_component_count")
    if after.get("pin_count") != before.get("pin_count"):
        issues.append("changes_pin_count")
    return issues


def _non_blocking_warnings(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    warnings = []
    if after.get("node_count", 0) >= before.get("node_count", 0):
        warnings.append("does_not_reduce_node_count")
    if (
        after.get("single_pin_net_count", 0) == before.get("single_pin_net_count", 0)
        and before.get("single_pin_net_count", 0) > 0
    ):
        warnings.append("does_not_reduce_single_pin_nets")
    return warnings


def validate_topology_candidate(
    repair_type: str,
    before_metrics: dict[str, Any],
    after_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Validate a dry-run candidate using topology-level invariants."""
    metrics_to_compare = [
        "single_pin_net_count",
        "zero_pin_net_count",
        "unmatched_pin_count",
        "component_self_short_count",
        "source_terminal_short_count",
        "connected_component_count",
        "component_count",
        "pin_count",
    ]
    metric_deltas = [
        _metric_delta(metric, before_metrics, after_metrics) for metric in metrics_to_compare
    ]
    improved_metrics = [
        item["metric"] for item in metric_deltas if item["outcome"] == "improved"
    ]
    regressed_metrics = [
        item["metric"] for item in metric_deltas if item["outcome"] == "regressed"
    ]
    unchanged_metrics = [
        item["metric"] for item in metric_deltas if item["outcome"] == "unchanged"
    ]
    blocking = _blocking_issues(before_metrics, after_metrics)
    warnings = _non_blocking_warnings(before_metrics, after_metrics)

    if blocking:
        result = "blocked"
    elif improved_metrics and not regressed_metrics:
        result = "viable"
    else:
        result = "reviewable"

    return {
        "validator_version": VALIDATOR_VERSION,
        "repair_type": repair_type,
        "validation_result": result,
        "metric_deltas": metric_deltas,
        "improved_metrics": improved_metrics,
        "regressed_metrics": regressed_metrics,
        "unchanged_metrics": unchanged_metrics,
        "blocking_issues": blocking,
        "non_blocking_warnings": warnings,
    }
