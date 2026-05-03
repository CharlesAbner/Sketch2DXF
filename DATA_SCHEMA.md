# Sketch2DXF Data Schema

This document describes the current runtime artifacts. Historical schema
version strings such as `3.0-agent-audit` and `3.1-step5-repair-dry-run` are
kept for backward compatibility. The current agent suite is 3.7: it keeps the
3.4 LangGraph-native advisor schema, then adds human-approved apply/replay,
eval harness, failure memory, and polished DXF export artifacts.

## 1. Project State

Source: `src/state.py`, `src/pipeline.py`

```python
{
    "input": {...},
    "preprocess": {...},
    "perception": {...},
    "topology": {...},
    "validation": {...},
    "export": {...},
}
```

`run_pipeline()` returns this state. The formal visual/debug artifacts are
written by `debug_run.py`.

## 2. Wire Evidence

Source: `src/perception/wire_extract.py`

`wire_evidence.json` contains:

- `raw_segments`: raw Hough / extracted line evidence.
- `filtered_segments`: evidence after local cleanup.
- `segments`: current evidence segments consumed by later graph logic.
- `stats`: segment counts and orientation counts.

Wire evidence is not final truth. It is only the geometry evidence layer.

## 3. Evidence Graph

Source: `src/topology/evidence_graph.py`

`evidence_graph.json` organizes wire segments, endpoints, junctions, inferred
intersections, raw connected components, and bridge candidates.

Important concepts:

- `vertices`: endpoints, detected junctions, inferred intersections.
- `edges`: segment edges and touch/intersection links.
- `raw_components`: connected evidence components before terminal support.
- `bridge_candidates`: possible gaps or connector candidates.

## 4. Terminal Attachments

Source: `src/topology/terminal_attachment.py`

`terminal_attachments.json` answers:

> For each terminal hypothesis, what evidence can it see along its terminal
> corridor/ray?

Key fields:

- `attachments`: best attachment per pin.
- `candidates_by_pin`: all candidate attachments.
- `best_raw_component_id`
- `best_evidence_kind`
- `best_evidence_id`
- `best_attachment_score`

## 5. Supported Graph

Source: `src/topology/supported_graph.py`

`supported_graph.json` marks which raw evidence components are supported by
terminal attachments.

Common support states:

- `best_terminal_supported`
- `candidate_terminal_supported`
- `relay_supported`
- `unsupported`

`relay_supported` means the raw component is kept because it bridges supported
components, even if no terminal directly attaches to it.

## 6. Graph Nodes Dry Run

Source: `src/topology/graph_node_dry_run.py`

`graph_nodes_dry_run.json` builds candidate electrical nodes from the supported
graph and compares them with legacy node-builder output.

Key fields:

- `graph_nodes`
- `discarded_raw_components`
- `used_bridge_candidates`
- `node_diff`

## 7. Node Selection

Source: `src/topology/graph_node_selector.py`

`node_selection.json` records whether the final topology used graph-derived
nodes or legacy fallback.

```python
{
    "selected_node_source": "graph_derived" | "legacy",
    "fallback_used": bool,
    "fallback_reasons": list[str],
    "graph_node_count": int,
    "legacy_match_count": int,
    "graph_match_count": int,
    "node_diff_stats": dict,
}
```

The current expected path is `selected_node_source = graph_derived` with
`fallback_used = false`. Legacy fallback is still kept as a safety mechanism.

## 8. Final Nodes

Source: `src/topology/graph_node_selector.py`, `src/topology/node_builder.py`

`nodes.json` is the selected final node result.

Important fields:

- `nodes`: active electrical nodes.
- `raw_nodes`: raw/legacy node data retained for debug.
- `discarded_nodes`: unsupported or filtered candidates.
- `stats`: selected node source, support counts, fallback state.

## 9. Topology And Netlist

Source: `src/topology/topology_builder.py`

`topology.json` contains components, pins, nodes, pin-node connections, nets,
component-net mapping, and compact netlist data.

`netlist.json` contains a simplified editable netlist:

- `components`: component refs/classes/pins/net ids.
- `nets`: net id, pin count, pin refs, component refs.

## 10. Audit Inputs

Source: `src/topology/audit_inputs.py`

`audit_inputs.json` is the structured input shared by human review and agent
workflows.

It includes:

- quality summary
- component/pin/node/net summaries
- evidence summary
- node selection details
- validation and export status
- risk flags

Common risk flag codes:

- `low_confidence_match`
- `weak_confidence_match`
- `unsupported_evidence`
- `relay_supported_node`
- `fallback_used`
- `graph_legacy_diff`
- `unmatched_pin`
- `isolated_net`
- `export_failed`

## 11. Repair Candidates

Source: `src/topology/repair_candidates.py`

`repair_candidates.json` is deterministic and non-mutating. It points out
reviewable risks before the agent workflow runs.

Common issue types:

- `unmatched_pin`
- `low_confidence_pin_match`
- `weak_confidence_pin_match`
- `ambiguous_terminal_attachment`
- `unsupported_evidence_review`
- `possible_gap_bridge`
- `relay_node_review`
- `fallback_used_review`
- `graph_legacy_diff_review`
- `consistency_warning_review`
- `consistency_error_review`

## 12. Case Summary

Source: `src/topology/case_summary.py`

`case_summary.json` is the compact single-case review entry point. It combines
`audit_inputs.json` and `repair_candidates.json`.

Important fields:

- `review_status`: `pass`, `needs_review`, or `fail`.
- `agent_ready`: whether the artifacts are sufficient for agent review.
- `summary`: compact counts and quality flags.
- `issue_overview`: risk and repair type counts.
- `review_focus`: short lists for humans and agents.
- `artifacts`: paths to key outputs.

## 13. Regression Report

Source: `run_regression.py`

`regression_report.json` records expected-case checks over fixed samples:

- graph-derived nodes selected
- no fallback
- export success
- acceptable consistency score
- no error-level risk
- no topology mutation by repair candidates

## 14. Standalone Agent Audit

Source: `tools/run_agent_audit.py`, `src/agent_workflow/workflow.py`

`agent_audit_report.json` is a standalone audit artifact used directly by the
3.4 advisor. Its schema string remains `3.0-agent-audit` for compatibility.

It contains:

- deterministic facts
- topology semantic audit
- stage diagnoses
- evidence list
- recommended actions
- optional LLM assessment

This audit is read-only.

## 15. Repair Dry Run

Source: `tools/run_agent_repair_dry_run.py`,
`src/agent_workflow/repair_dry_run.py`

`agent_repair_dry_run.json` generates validated and ranked candidate repairs
without mutating `topology.json`, `netlist.json`, or DXF.

Supported dry-run tools:

- `merge_nodes_dry_run`
- `reattach_pin_dry_run`
- `evidence_review_dry_run`

Candidate ranking may recommend `accept_for_human_review`, but this only means
the candidate is suitable for human approval. It is not an automatic repair.

## 16. Agent 3.4 Repair Advisor

Source: `tools/run_agent_repair_advisor.py`,
`src/agent_workflow/repair_advisor.py`

`agent_repair_advisor_report.json` is the current recommended agent output.

Schema:

```python
{
    "schema_version": "3.4-langgraph-native-tool-state-machine",
    "workflow_engine": "local" | "langgraph",
    "backend": "rule" | "mock" | "openai" | "deepseek" | "custom",
    "agent_loop_config": {
        "max_agent_tool_steps": int,
        "max_tool_calls_per_step": int,
    },
    "workflow_mode": "local_loop" | "langgraph_native_state_machine",
    "workflow_topology": list[str],
    "available_tools": list[dict],
    "planner": dict,
    "tool_results": list[dict],
    "critic": dict,
    "reviewer": dict,
    "human_review_dossier": dict,
    "final_decision": str,
    "selected_candidate_ids": list[str],
    "topology_mutated": False,
}
```

Current safe tools:

- `get_case_summary`
- `get_single_pin_nets`
- `get_terminal_attachments`
- `get_repair_candidates`
- `repair_dry_run`

Companion outputs:

- `agent_repair_advisor_report.md`
- `agent_human_review_dossier.json`
- `agent_human_review_dossier.md`

The dossier is the human-readable bridge from internal IDs such as `N2`, `N4`,
and `MRG1` to actual pins, components, evidence IDs, and candidate rationale.

## 17. Human-Approved Repair Apply

Source: `tools/run_agent_repair_apply.py`,
`src/agent_workflow/repair_apply.py`

This stage converts an accepted dry-run candidate into corrected artifacts
without overwriting original debug outputs.

Approval request:

```python
{
    "schema_version": "3.5-human-approval-request",
    "request_status": "pending_human_decision",
    "candidate": {
        "candidate_id": str,
        "repair_type": str,
        "target_nodes": list[str],
        "target_pins": list[str],
        "recommendation": str,
        "validation_result": str,
    },
}
```

Replay report:

```python
{
    "schema_version": "3.5-human-approved-repair-apply",
    "status": "approval_not_accepted" | "applied",
    "approval_decision": dict,
    "candidate": dict,
    "before_metrics": dict,
    "after_metrics": dict,
    "topology_mutated_in_place": False,
    "export": dict,
    "outputs": {
        "corrected_topology": "corrected_topology.json",
        "corrected_netlist": "corrected_netlist.json",
        "corrected_dxf": "corrected_export.dxf",
    },
}
```

Current apply support:

- `merge_nodes`

Original `topology.json`, `netlist.json`, and `14_export.dxf` remain unchanged.

## 18. Agent Eval Harness

Source: `tools/run_agent_eval_harness.py`,
`src/agent_workflow/eval_harness.py`

`agent_eval_report.json` evaluates one advisor/apply run. It combines
deterministic safety metrics with an optional LLM semantic review.

Single-case report:

```python
{
    "schema_version": "3.6-agent-eval-harness",
    "case_id": str,
    "strategy_name": str,
    "eval_status": "pass" | "pass_with_warnings" | "needs_review" | "fail",
    "scores": {
        "agent_behavior_score": float,
        "repair_effect_score": float,
        "semantic_score": float | None,
        "overall_score": float,
    },
    "agent_behavior": {
        "workflow_engine": str,
        "workflow_mode": str,
        "backend": str,
        "llm_used": bool,
        "tool_names": list[str],
        "selected_candidate_ids": list[str],
    },
    "repair_effect": {
        "status": str,
        "approval_decision": str,
        "candidate": dict,
        "before_metrics": dict,
        "after_metrics": dict,
        "improvement": dict,
        "export_success": bool,
    },
    "semantic_eval": {
        "used": bool,
        "backend": str,
        "semantic_verdict": "pass" | "warning" | "fail" | "inconclusive" | "not_run",
        "semantic_score": float | None,
        "summary": str | None,
        "risks": list[str],
        "next_checks": list[str],
    },
    "deterministic_findings": list[dict],
    "artifacts": dict,
}
```

Companion output:

- `agent_eval_report.md`

`agent_eval_summary.json` aggregates many case reports for strategy comparison.

Batch summary:

```python
{
    "schema_version": "3.6-agent-eval-summary",
    "strategy_name": str,
    "cases_dir": str,
    "pattern": str,
    "total_cases": int,
    "status_counts": dict,
    "average_scores": dict,
    "repair_applied_count": int,
    "repair_improved_count": int,
    "llm_semantic_eval_used_count": int,
    "finding_counts": dict,
    "cases": list[dict],
}
```

Companion output:

- `agent_eval_summary.md`

The deterministic portion checks artifact presence, human approval safety,
before/after topology metrics, single-pin/zero-pin regressions, component/pin
stability, and corrected DXF export. The optional LLM semantic evaluator only
reviews compact structured artifacts; it does not inspect images or mutate
topology.

## 19. Failure Memory

Source: `tools/run_agent_failure_memory.py`,
`src/agent_workflow/failure_memory.py`

`failure_memory.json` stores recurring failure patterns extracted from
`agent_eval_report.json` or `agent_eval_summary.json`.

```python
{
    "schema_version": "3.7-failure-memory",
    "created_at": str,
    "updated_at": str,
    "pattern_count": int,
    "patterns": [
        {
            "pattern_id": str,
            "kind": "deterministic" | "semantic" | "eval_status",
            "code": str,
            "count": int,
            "severity_counts": dict,
            "cases": list[dict],
            "last_message": str,
            "suggested_actions": list[str],
            "example_case": dict,
        }
    ],
    "case_index": dict,
}
```

Companion output:

- `failure_memory.md`

Advisor integration:

```python
{
    "observation": {
        "failure_memory": {
            "exists": bool,
            "path": str,
            "pattern_count": int,
            "case_signals": list[str],
            "matched_pattern_count": int,
            "matched_patterns": list[dict],
        }
    }
}
```

Memory is contextual. It can influence tool planning, but it is not a proof of
the current case and cannot approve or apply repairs.

## 20. Polished DXF Export

Source: `src/export/dxf_exporter.py`

The DXF exporter writes a readable schematic-style file while preserving the
recovered relative geometry. Current layers:

- `WIRES`
- `COMPONENTS`
- `LABELS`
- `NETS`
- `PINS`
- `NODES`
- `TITLE`
- `REPAIR`

Human-approved corrected topologies may include `repair_history` and
`human_approval`; these are rendered in the `REPAIR` layer so the corrected DXF
can be traced back to the approved candidate.
