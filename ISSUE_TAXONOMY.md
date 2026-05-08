# Issue Taxonomy

本文档定义 Sketch2DXF 当前审计与 Agent 修复中使用的问题类型。目标是让人工排查、regression harness 和 Agent 使用同一套语义。

## 1. Risk Flags

`risk_flags` 主要来自 `audit_inputs.json` 和 `case_summary.json`，描述当前 topology/netlist 中值得关注的风险。

### low_confidence_match

含义：某个 pin 已连接到 node，但 match confidence 低于低置信阈值。

典型原因：

- pin 到 evidence/node 的距离偏大；
- lateral offset 较大；
- terminal 方向与 evidence 对齐较弱。

处理建议：检查 overlay、terminal attachment、candidate match 和对应 repair dry-run。

### weak_confidence_match

含义：pin-node 连接可用，但置信度处于中等区间。

处理建议：通常不阻断结果，但应保留在审计报告和 human dossier 中。

### unmatched_pin

含义：某个 pin 没有恢复到任何 electrical node。

处理建议：这是 error 级问题，优先检查 terminal axis、pin 位置、附近 supported evidence 和可用 repair tool。

### unsupported_evidence

含义：存在没有 terminal 支持或路径支持的 raw evidence component。

处理建议：判断它是噪声/文字残留，还是断线造成的真实连接证据。

### relay_supported_node

含义：某个 raw component 自身没有 terminal 直接支持，但连接了多个 supported component，因此作为中继导线保留。

处理建议：检查 relay 是否真的位于主回路中，避免把文字残留当成中继导线。

### graph_legacy_diff

含义：graph-derived nodes 与 legacy nodes 存在差异。

处理建议：检查 `graph_nodes_dry_run.json` 和 `node_selection.json`，确认差异来自修复还是误合并/误丢弃。

### fallback_used

含义：graph-derived nodes 被拒绝，系统回退到 legacy nodes。

处理建议：检查 fallback reason。fallback 不是失败，但需要解释。

### isolated_net / single_pin_net

含义：存在 pin 数小于 2 的 net。

处理建议：检查是否是 floating pin、缺失导线、误分割 node 或真实开路。

### export_failed

含义：netlist 或 DXF 导出失败。

处理建议：优先检查 topology 完整性和 export stack。

## 2. Repair Candidate Types

`repair_candidates.json` 是确定性审计生成的候选问题集合；Agent advisor 会进一步调用工具验证，不会把这些候选直接当成最终答案。

### low_confidence_pin_match

对应风险：`low_confidence_match`

常见动作：

- `review_current_match`
- `review_alternative_match`
- `dry_run_reattach_pin`

### weak_confidence_pin_match

对应风险：`weak_confidence_match`

常见动作：

- `accept_current_with_review`
- 必要时 inspect terminal attachment。

### ambiguous_terminal_attachment

含义：某个 terminal 对多个 evidence/node 有接近的 attachment score。

常见动作：

- `inspect_terminal_attachments`
- `validate_candidate`

### unsupported_evidence_review

对应风险：`unsupported_evidence`

常见动作：

- `inspect_gap_bridge_candidates`
- `dry_run_gap_bridge_merge`
- `confirm_discard_as_noise`

### possible_gap_bridge

含义：两个 raw/supported evidence component 之间存在可桥接 gap。

常见动作：

- `inspect_gap_bridge_candidates`
- `dry_run_gap_bridge_merge`

### single_pin_stub_review

含义：单 pin net 附近可能存在可桥接 stub 或 supported node。

常见动作：

- `inspect_single_pin_stub`
- `dry_run_single_pin_stub_bridge`
- `dry_run_merge_nodes`

### component_axis_review

含义：两 pin 元件的 terminal axis 可能选错，常见于 bbox 方向或周围导线证据不足时。

常见动作：

- `inspect_component_terminal_axis`
- `dry_run_component_axis_flip`

### component_class_review

含义：检测类别可能错，但 proposal 中保留了其他类别候选。

常见动作：

- `inspect_component_class_candidates`
- `dry_run_component_class_override`

### graph_legacy_diff_review

对应风险：`graph_legacy_diff`

常见动作：

- inspect graph/legacy diff；
- 检查 fallback 是否合理。

### fallback_used_review

对应风险：`fallback_used`

常见动作：

- inspect node selection；
- 若 fallback 结果可解释，保留为 warning。

### consistency_warning_review

含义：拓扑存在 warning，但不一定影响可用性。

常见检查：

- floating pin；
- isolated net；
- weak match；
- unsupported evidence。

### consistency_error_review

含义：拓扑存在 error，通常会导致 case fail。

常见检查：

- unmatched pin；
- missing pin net reference；
- export failure；
- 明显短路或连接缺失。

## 3. Agent 3.9 Tool Families

当前 Agent 不是只有一个粗粒度 repair tool，而是使用工具族：

### Inspect tools

- `inspect_component_class_candidates`
- `inspect_component_terminal_axis`
- `inspect_single_pin_stub`
- `inspect_gap_bridge_candidates`
- `inspect_single_pin_nets`
- `inspect_terminal_attachments`

这些工具只提供事实，不生成修改。

### Granular dry-run tools

- `dry_run_merge_nodes`
- `dry_run_component_class_override`
- `dry_run_component_axis_flip`
- `dry_run_reattach_pin`
- `dry_run_gap_bridge_merge`
- `dry_run_single_pin_stub_bridge`

这些工具生成候选，但不会直接修改 topology。

### Applyable repair types

当前 human-approved apply 支持：

- `merge_nodes`
- `reattach_pin`
- `gap_bridge_merge`
- `single_pin_stub_bridge`
- `component_pin_axis_flip`
- `component_class_override`

## 4. Severity Rules

- `error`：结果不完整或明显不可用，需要优先修复。
- `warning`：结果可用但存在可疑点，需要复核。
- `info`：解释性提示，不一定需要修改。

## 5. Agent 使用约束

Agent 读取这些 issue 时必须遵守：

- 不直接修改 topology；
- 不把 confidence 当成概率；
- 不把 deterministic repair candidate 当成最终修复；
- 必须通过工具结果验证假设；
- 最终输出 `repair_plan`，由 human approval 后才 apply；
- 对 error 优先处理，对 warning 给出复核或延后理由，对 info 给出解释。
