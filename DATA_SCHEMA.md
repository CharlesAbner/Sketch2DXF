# Data Schema

本文档说明 Sketch2DXF 当前主要 JSON artifact 的语义。它不是逐字段 API 规范，而是用于理解 pipeline、debug、Agent 和 eval 输出之间的关系。

当前主线：

```text
image -> proposals -> terminals -> wire evidence
      -> evidence_graph -> terminal_attachments
      -> supported_graph -> graph_nodes_dry_run
      -> topology/netlist -> audit/case_summary
      -> agent advisor -> repair_plan -> human-approved apply
      -> corrected topology/netlist/DXF -> eval
```

## 1. proposals.json

元件检测输出。每个 proposal 通常包含：

- `component_id`
- `class_name`
- `bbox`
- `confidence`
- `class_candidates`
- `source`

`class_candidates` 对 Agent 很重要。例如 005 中当前类别是 capacitor，但候选里存在 power_source，Agent 可以据此提出 `component_class_override`。

## 2. pins / terminals

Terminal 通常由 bbox、元件类别、axis、周围导线 evidence 综合生成。重要字段包括：

- `pin_id`
- `component_id`
- `x`, `y`
- `side`
- `axis`
- `confidence`

Terminal 是拓扑恢复的锚点，不是单纯依赖像素检测得到的端点。

## 3. wire.json

导线提取结果现在被视为 evidence，而不是最终真相。

典型字段：

- `segments`
- `orientation`
- `x1`, `y1`, `x2`, `y2`
- `source`
- `keep_reasons`
- `noise_flags`

导线提取失败不一定直接导致 topology 失败，因为后续还有 terminal attachment、supported graph 和 Agent repair。

## 4. junctions.json

描述线段端点、交点和 junction candidates：

- `endpoints`
- `junctions`
- `segment_refs`

它属于连接证据层。

## 5. evidence_graph.json

Evidence graph 是 2.0 之后的核心中间层。它把线段、端点、junction 组成 raw connected components。

常见结构：

- `raw_components`
- `segments`
- `points`
- `component_ids`
- `support_status`
- `candidate_bridge_links`
- `unsupported_components`

它回答的问题是：

> 纯几何证据可以连成哪些 raw graph component？

但 raw component 不等于 electrical node。

## 6. terminal_attachments.json

Terminal attachment 描述每个 pin 能看到哪些 nearby evidence。

常见字段：

- `pin_id`
- `component_id`
- `raw_component_id`
- `segment_id`
- `projected_point`
- `distance`
- `alignment_score`
- `attachment_score`
- `axis`

它回答的问题是：

> terminal 作为锚点，附近有哪些可能的连接证据？

Agent 的 `inspect_component_terminal_axis`、`inspect_single_pin_stub` 等工具会消费这些信息。

## 7. supported_graph.json

Supported graph 将 raw evidence 按 terminal 支持、relay support、gap bridge 等规则筛选。

常见字段：

- `supported_components`
- `unsupported_components`
- `support_reasons`
- `terminal_support`
- `relay_support`
- `bridge_candidates`

它回答的问题是：

> 哪些 evidence 更像真实电路连接，哪些更像噪声或未解决证据？

## 8. graph_nodes_dry_run.json

Graph-derived nodes 的 dry-run 结果。它不会直接强制覆盖 legacy node，而是与 legacy 结果比较，由 node selection 选择。

常见字段：

- `graph_nodes`
- `legacy_nodes`
- `comparison`
- `selected_node_source`
- `fallback_used`
- `fallback_reason`

如果 graph-derived 质量不足，系统可能回退到 legacy nodes。

## 9. node_selection.json

记录最终选择 graph-derived 还是 legacy result。

关键字段：

- `selected_node_source`
- `fallback_used`
- `fallback_reason`
- `comparison_metrics`

这对答辩很重要：系统不是盲目使用新图算法，而是有可解释 fallback。

## 10. topology.json

Topology 是主链路的结构化输出，描述：

- `components`
- `pins`
- `nodes`
- `connections`
- `nets`
- `metadata`
- `repair_history`（如果来自 corrected topology）

它是 DXF 导出的主要输入。

## 11. netlist.json

Netlist 更偏电路关系，常见字段：

- `components`
- `nets`
- `connections`
- `pins`

它适合做审计，例如：

- 是否存在 zero-pin net；
- 是否存在 single-pin net；
- 是否所有 pin 都在同一个 net；
- 两端元件是否短接到同一个 net。

## 12. case_summary.json

Case summary 是面向人和 Agent 的紧凑摘要。

常见字段：

- `review_status`
- `agent_ready`
- `summary`
- `issue_overview`
- `review_focus`
- `artifacts`

它把 topology、netlist、audit、repair_candidates 等信息压缩成一个入口。

## 13. repair_candidates.json

确定性审计生成的候选问题和初步候选，不等同于 Agent 最终 repair_plan。

常见字段：

- `repair_candidate_id`
- `issue_type`
- `severity`
- `recommended_action`
- `refs`
- `candidate_mode`

当前 Agent 会把它作为观察材料之一，但不会被它完全绑定。

## 14. Agent Advisor Report

当前 schema：

```text
3.9-hypothesis-tool-agent
```

主要结构：

- `summary`
- `agent_trace`
- `planner`
- `tool_results`
- `critic`
- `reviewer`
- `repair_plan`
- `outputs`

核心语义是 `repair_plan`，而不是旧版本的 `selected_candidate_ids`。

## 15. Tool Results

每个工具结果至少包含：

- `tool_call_id`
- `tool_name`
- `status`
- `arguments`
- `grounded_args`
- `summary`
- `candidates` 或 inspection-specific payload

Inspection tool 只返回事实。Dry-run tool 返回候选。

## 16. Granular Dry-run Candidate

当前 granular dry-run schema：

```text
3.9-granular-repair-dry-run
```

候选字段通常包括：

- `candidate_id`
- `repair_type`
- `candidate_mode`
- `validation_result`
- `recommendation`
- `ranking_score`
- `before_metrics`
- `after_metrics`
- `improved_metrics`
- `risk_flags`
- `target_nodes`
- `target_pins`

当前可 dry-run/apply 的 repair types：

- `merge_nodes`
- `reattach_pin`
- `gap_bridge_merge`
- `single_pin_stub_bridge`
- `component_pin_axis_flip`
- `component_class_override`

## 17. repair_plan

当前 Agent 最终建议使用 repair_plan 表达：

```json
{
  "repair_plan_id": "PLAN1",
  "status": "pending_human_review",
  "steps": [
    {
      "step_id": "S1",
      "candidate_id": "AXF1",
      "repair_type": "component_pin_axis_flip",
      "depends_on": []
    }
  ]
}
```

多问题 case 可以有多个 step，并通过 `depends_on` 表达顺序。

## 18. Human Review Dossier

给人看的审阅材料，通常包括：

- final decision；
- repair_plan；
- candidate groups；
- confidence cues；
- human checklist；
- net/component/pin 解释。

它的目标是让人不需要在一堆 JSON 中盲找 N1、N2、cp_3_p1。

## 19. Apply / Replay Report

当前 apply schema：

```text
3.5-human-approved-repair-apply
```

Apply 输出：

- `approval_request`
- `approval_decision`
- `corrected_topology`
- `corrected_netlist`
- `corrected_dxf`
- `replay_report`

Replay report 记录：

- 哪些 candidate 被执行；
- topology 是否修改；
- netlist 指标如何变化；
- DXF 是否成功导出；
- 是否有风险或失败。

## 20. Agent Eval

当前 eval schema：

```text
3.8-agent-eval-harness
3.8-agent-eval-summary
```

Eval 评估：

- Agent 是否使用 LLM；
- 是否调用工具；
- 是否生成 repair_plan；
- 是否经过 apply/replay；
- before/after topology 是否改善；
- 可选 LLM semantic eval 是否通过。

## 21. Failure Memory

当前 memory schema：

```text
3.7-failure-memory
```

它记录历史失败模式，给后续 Agent planner 提供参考，不作为硬规则直接修改 topology。

## 22. DXF Export

DXF 导出读取 topology/corrected topology，不读取原始图像来描线。

当前 clean 模式包含：

- common schematic redraw；
- RC ladder redraw；
- two-rail ladder redraw；
- mesh-like graph redraw；
- geometry-preserving fallback。

这让 DXF 更像标准电路图，而不是手绘线条的矢量化噪声。

## 23. 兼容旧字段

项目中仍保留少量旧 schema 或旧字段用于兼容历史输出，例如：

- old bulk `repair_dry_run`
- older eval/advisor artifacts
- legacy node fallback

但当前文档和新输出应以以下语义为准：

- Advisor：`3.9-hypothesis-tool-agent`
- Dry-run：granular tools + `3.9-granular-repair-dry-run`
- Repair selection：`repair_plan`
- Apply：`3.5-human-approved-repair-apply`
- Eval：`3.8-agent-eval-harness`
- Memory：`3.7-failure-memory`
