"""Shared ranking logic for repair dry-run candidates."""

from __future__ import annotations

from typing import Any


RANKER_VERSION = "3.1-step4-repair-candidate-ranker"

VALIDATION_PRIORITY = {
    "viable": 0.45,
    "reviewable": 0.25,
    "blocked": -1.0,
}

RISK_PENALTY = {
    "low": 0.0,
    "medium": 0.08,
    "high": 0.22,
}

IMPROVEMENT_WEIGHTS = {
    "source_terminal_short_count": 0.4,
    "zero_pin_net_count": 0.35,
    "unmatched_pin_count": 0.3,
    "component_self_short_count": 0.28,
    "single_pin_net_count": 0.22,
    "connected_component_count": 0.16,
}

REGRESSION_WEIGHTS = {
    "source_terminal_short_count": 0.45,
    "zero_pin_net_count": 0.4,
    "unmatched_pin_count": 0.35,
    "component_self_short_count": 0.32,
    "connected_component_count": 0.2,
    "component_count": 0.5,
    "pin_count": 0.5,
}


def _metric_weight(metric: str, weights: dict[str, float], default: float) -> float:
    return weights.get(metric, default)


def _scope_penalty(candidate: dict[str, Any]) -> float:
    affected_nodes = len(candidate.get("target_nodes", []))
    affected_pins = len(candidate.get("target_pins", []))
    affected_nets = len(candidate.get("affected_nets", []))
    return min(0.18, affected_nodes * 0.015 + affected_pins * 0.01 + affected_nets * 0.008)


def rank_repair_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Attach ranking fields to a single candidate without mutating topology."""
    validation = candidate.get("validation", {})
    validation_result = candidate.get("validation_result") or validation.get("validation_result", "reviewable")
    improved_metrics = candidate.get("improved_metrics") or validation.get("improved_metrics", [])
    regressed_metrics = candidate.get("regressed_metrics") or validation.get("regressed_metrics", [])
    blocking_issues = candidate.get("blocking_issues") or validation.get("blocking_issues", [])
    risk_level = candidate.get("risk_level", "medium")
    tool_score = float(candidate.get("score", 0.0) or 0.0)

    validation_score = VALIDATION_PRIORITY.get(validation_result, 0.0)
    improvement_score = min(
        0.35,
        sum(_metric_weight(metric, IMPROVEMENT_WEIGHTS, 0.08) for metric in improved_metrics),
    )
    regression_penalty = min(
        0.5,
        sum(_metric_weight(metric, REGRESSION_WEIGHTS, 0.12) for metric in regressed_metrics),
    )
    blocking_penalty = min(0.75, len(blocking_issues) * 0.25)
    risk_penalty = RISK_PENALTY.get(risk_level, 0.12)
    scope_penalty = _scope_penalty(candidate)
    tool_score_component = min(0.25, max(0.0, tool_score) * 0.25)
    no_regression_bonus = 0.08 if not regressed_metrics and not blocking_issues else 0.0

    raw_score = (
        validation_score
        + improvement_score
        + tool_score_component
        + no_regression_bonus
        - regression_penalty
        - blocking_penalty
        - risk_penalty
        - scope_penalty
    )
    ranking_score = round(max(0.0, min(1.0, raw_score)), 3)

    if validation_result == "blocked" or blocking_issues:
        recommendation = "reject"
    elif validation_result == "viable" and ranking_score >= 0.6:
        recommendation = "accept_for_human_review"
    elif validation_result in {"viable", "reviewable"}:
        recommendation = "needs_more_evidence"
    else:
        recommendation = "no_action"

    reasons = [
        f"Validation result is {validation_result}.",
        f"Improved metrics: {', '.join(improved_metrics) or 'none'}.",
        f"Regressed metrics: {', '.join(regressed_metrics) or 'none'}.",
        f"Blocking issues: {', '.join(blocking_issues) or 'none'}.",
        f"Tool-local score is {tool_score}.",
    ]

    ranked = dict(candidate)
    ranked["ranking"] = {
        "ranker_version": RANKER_VERSION,
        "ranking_score": ranking_score,
        "recommendation": recommendation,
        "ranking_reasons": reasons,
        "ranking_factors": {
            "validation_score": round(validation_score, 3),
            "improvement_score": round(improvement_score, 3),
            "tool_score_component": round(tool_score_component, 3),
            "no_regression_bonus": round(no_regression_bonus, 3),
            "regression_penalty": round(regression_penalty, 3),
            "blocking_penalty": round(blocking_penalty, 3),
            "risk_penalty": round(risk_penalty, 3),
            "scope_penalty": round(scope_penalty, 3),
        },
    }
    ranked["ranking_score"] = ranking_score
    ranked["recommendation"] = recommendation
    ranked["ranking_reasons"] = reasons
    return ranked


def rank_repair_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank candidates across repair tools."""
    ranked = [rank_repair_candidate(candidate) for candidate in candidates]
    ranked.sort(
        key=lambda item: (
            item.get("recommendation") == "reject",
            -float(item.get("ranking_score", 0.0)),
            -float(item.get("score", 0.0) or 0.0),
            len(item.get("target_nodes", [])) + len(item.get("target_pins", [])),
            item.get("candidate_id", ""),
        )
    )
    for rank, candidate in enumerate(ranked, start=1):
        candidate["rank"] = rank
    return ranked
