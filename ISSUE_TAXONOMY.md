# Issue Taxonomy

本文档定义 Sketch2DXF 当前审计和修复候选中的问题类型。它的目标是让人工排查、regression harness 和后续 Agent 使用同一套问题语义。

## 1. Risk Flags

`risk_flags` 来自 `audit_inputs.json`，用于描述当前结果中值得关注的风险。

### low_confidence_match

含义：某个 pin 已经连接到 node，但 `match_confidence < 0.6`。

典型原因：

- pin 到 evidence 距离偏大。
- lateral offset 较大。
- 方向对齐较弱。

处理建议：检查该 pin 的 overlay、match evidence 和 repair candidate。

### weak_confidence_match

含义：某个 pin 已经连接到 node，但置信度处于中等区间。

处理建议：通常不阻断结果，但需要保留在审计报告中。

### unmatched_pin

含义：某个 pin 没有恢复到任何 node。

处理建议：这是 error 级问题，优先检查 terminal 位置、导线证据和匹配半径。

### unsupported_evidence

含义：存在没有 terminal 或路径支持的 raw evidence component。

处理建议：判断它是噪声、文字残留，还是真实导线断裂造成的缺失支持。

### relay_supported_node

含义：某个节点使用了 relay evidence。

处理建议：检查中继导线是否确实位于两个 terminal-supported evidence 之间。

### graph_legacy_diff

含义：graph-derived nodes 与 legacy nodes 存在差异。

处理建议：检查 graph node dry-run diff，确认新旧差异来自修复还是误合并/误丢弃。

### fallback_used

含义：graph-derived nodes 被拒绝，系统回退到 legacy nodes。

处理建议：检查 fallback reasons。

### isolated_net

含义：存在 pin 数小于 2 的 net。

处理建议：检查是否有 floating pin、缺失导线或误分割 node。

### export_failed

含义：DXF 或 netlist 导出失败。

处理建议：优先修复导出逻辑或检查 topology 数据是否不完整。

## 2. Repair Candidate Types

`repair_candidates.json` 来自 2.1 确定性候选生成器。它不自动修改 topology，只把可疑点整理成候选项。

### low_confidence_pin_match

对应风险：`low_confidence_match`。

推荐动作：

- `review_current_match`
- `review_alternative_match`

检查重点：

- 当前 node 是否符合肉眼判断。
- 是否存在不同 node 的可行候选。
- terminal attachment 是否支持当前连接。

### weak_confidence_pin_match

对应风险：`weak_confidence_match`。

推荐动作：

- `accept_current_with_review`

检查重点：

- 当前连接是否只是几何偏移较大。
- 是否需要调 terminal corridor 或 bbox。

### ambiguous_terminal_attachment

含义：某个 terminal 对多个 raw evidence component 有接近的 attachment 分数。

推荐动作：

- `review_terminal_attachment`

检查重点：

- best attachment 和 alternative attachment 是否属于不同电气节点。
- 是否存在文字残留或邻近支路干扰。

### unsupported_evidence_review

对应风险：`unsupported_evidence`。

推荐动作：

- `confirm_discard_as_noise`
- `inspect_possible_missing_support`

检查重点：

- 该 evidence 是否是文字、噪声或断线。
- 是否靠近 supported graph。

### possible_gap_bridge

含义：两个 evidence components 之间存在几何上可能桥接的 gap。

推荐动作：

- `confirm_bridge`
- `inspect_gap_bridge`

检查重点：

- gap 是否真的代表断开的导线。
- 桥接是否会造成短路或误合并。

### relay_node_review

对应风险：`relay_supported_node`。

推荐动作：

- `confirm_relay_supported_node`

检查重点：

- relay raw component 是否确实是主回路的一部分。
- 是否存在文字残留被当作中继导线。

### graph_legacy_diff_review

对应风险：`graph_legacy_diff`。

推荐动作：

- `inspect_graph_legacy_diff`

检查重点：

- graph-derived nodes 是否比 legacy 更符合电路语义。
- 是否需要 fallback。

### fallback_used_review

对应风险：`fallback_used`。

推荐动作：

- `inspect_graph_node_rejection`

检查重点：

- fallback reason 是否合理。
- graph-derived 是否存在误丢弃或误合并。

### consistency_warning_review

对应风险：consistency warning。

推荐动作：

- `inspect_consistency_warning`

检查重点：

- floating pin。
- incomplete component。
- isolated net。

### consistency_error_review

对应风险：consistency error。

推荐动作：

- `fix_consistency_error`

检查重点：优先处理，因为 error 会导致样例整体 fail。

## 3. Severity Rules

当前 severity 语义：

- `error`：结果不完整或明显不可用，需要优先修复。
- `warning`：结果可用但存在可疑点，需要复核。
- `info`：解释性提示，不一定需要修改。

## 4. Agent 使用约束

后续 Agent 读取这些 issue 时必须遵守：

- 不直接修改 topology。
- 不把 confidence 当概率。
- 不把 repair candidate 当最终修复。
- 必须引用具体 issue 和 evidence 给出判断。
- 对 error 优先处理，对 warning 给出复核建议，对 info 给出解释。
