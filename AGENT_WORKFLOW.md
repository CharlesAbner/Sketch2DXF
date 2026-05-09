# Agent Workflow

本文档描述当前 Sketch2DXF 的 Agent 层。当前版本的 Agent 报告 schema 为：

```text
3.9-hypothesis-tool-agent
```

Agent 的定位不是替代视觉/几何主链路，而是在主链路已经输出 topology、netlist、audit 之后，作为“系统大脑”进行问题定位、工具调用、候选验证、human approval 和 replay。

## 1. 设计原则

Agent 层遵守四个原则：

1. **LLM 不直接改 topology**  
   LLM 只能提出假设并调用工具。真正的 topology 修改必须由 dry-run candidate 生成，并经过 human approval。

2. **工具是系统能力边界**  
   LLM 不能凭空创建连接。它必须调用 inspect/dry-run 工具，工具返回结构化证据和候选。

3. **每一步可回放**  
   Advisor、human dossier、approval decision、corrected topology、replay report、eval report 都落盘。

4. **规则和 LLM 分工明确**  
   规则负责可计算事实、约束、验证；LLM 负责选择下一步工具、比较假设、组织 repair_plan 与解释。

## 2. 输入与输出

Agent advisor 的输入通常是一个 debug run 目录，例如：

```text
outputs/debug_runs/agent40_301_plan/
```

它会读取：

- `topology.json`
- `netlist.json`
- `case_summary.json`
- `audit_inputs.json`
- `repair_candidates.json`
- `terminal_attachments.json`
- `evidence_graph.json`
- `supported_graph.json`
- `graph_nodes_dry_run.json`
- 其他可用 debug artifact

Advisor 输出：

- `agent_repair_advisor_report.json`
- `agent_repair_advisor_report.md`
- `agent_human_review_dossier.json`
- `agent_human_review_dossier.md`

Apply 输出：

- `approval_request.json/.md`
- `approval_decision.json`
- `corrected_topology.json`
- `corrected_netlist.json`
- `corrected_export.dxf`
- `repair_replay_report.json/.md`

Eval 输出：

- `agent_eval_report.json/.md`
- 多 case 时还有 summary json/md。

## 3. 外层 Workflow

如果安装了 LangGraph，可以通过 `--workflow-engine langgraph` 使用 LangGraph `StateGraph` 承载外层节点：

```text
audit_tool -> observe -> planner_tool_loop -> critic -> reviewer -> END
```

其中：

- `audit_tool`：生成或刷新 deterministic/LLM audit。
- `observe`：构建给 LLM 的事实观察，不把结论喂得过死。
- `planner_tool_loop`：LLM 多轮选择工具，项目代码执行工具并更新状态。
- `critic`：规则 guardrail，检查是否还有明显未处理的问题、是否有违规工具调用、是否 dry-run 安全。
- `reviewer`：LLM 根据全部工具结果生成最终 repair_plan 和解释。

内部 tool loop 没有完全交给 LangGraph 的原因是这里需要严格做：

- 参数 grounding；
- 工具白名单；
- dry-run safety；
- human approval；
- 本地 artifact 落盘；
- 兼容无 LangGraph 的 local workflow。

所以 LangGraph 是外层状态机，项目代码负责高安全要求的工具执行细节。

## 4. Planner 如何工作

Planner 每轮会收到：

- 当前 case 摘要；
- audit 发现；
- 可用工具 schema；
- 已完成工具结果；
- guardrail feedback；
- memory matches；
- 允许的最大工具调用次数。

它输出：

- `stop_decision`：`continue` / `final_ready` 等；
- `tool_calls`：本轮要调用的工具；
- `hypotheses`：当前假设；
- `open_questions`：仍未解决的问题；
- `deferred_questions`：明确延后处理的问题及原因；
- `planner_notes`：推理摘要。

如果 Planner 带着 open question 提前停止，critic/guardrail 会把它拉回继续调用工具，或要求它给出明确 deferred reason。

## 5. 工具族

### 5.1 Compact Overview Tools

这些工具给 LLM 快速掌握全局：

- `get_case_summary`
- `get_single_pin_nets`
- `get_terminal_attachments`
- `get_repair_candidates`
- `repair_dry_run`

其中 `repair_dry_run` 是兼容旧流程的聚合工具。当前推荐优先使用更细粒度的 inspect/dry-run 工具。

### 5.2 Inspection Tools

这些工具只观察，不生成可 apply 修改：

- `inspect_component_class_candidates`  
  查看某个元件的类别候选，例如当前是 capacitor，但候选里有 power_source。

- `inspect_component_terminal_axis`  
  检查元件当前 terminal axis、候选 axis、pin 位置与 attachment 证据。

- `inspect_single_pin_stub`  
  检查单 pin net 周围的 supported node、stub pair、距离、方向等。

- `inspect_gap_bridge_candidates`  
  查看 supported/unsupported evidence 之间的 gap bridge 候选。

- `inspect_single_pin_nets`

- `inspect_terminal_attachments`

### 5.3 Granular Dry-run Tools

这些工具生成候选，但仍然不修改文件：

- `dry_run_merge_nodes`
- `dry_run_component_class_override`
- `dry_run_component_axis_flip`
- `dry_run_reattach_pin`
- `dry_run_gap_bridge_merge`
- `dry_run_single_pin_stub_bridge`

dry-run 返回 candidate，包含：

- `candidate_id`
- `repair_type`
- `candidate_mode`
- `validation_result`
- `ranking_score`
- `recommendation`
- `before_metrics`
- `after_metrics`
- `improved_metrics`
- `risk_flags`

### 5.4 Validation

- `validate_candidate`

用于对候选进行额外一致性检查。

## 6. 当前可 Apply 的 Repair Types

当前 apply 层支持：

- `merge_nodes`
- `reattach_pin`
- `gap_bridge_merge`
- `single_pin_stub_bridge`
- `component_pin_axis_flip`
- `component_class_override`

这意味着 Agent 不再只是“提出报告”，而是可以在 human approval 后真正 replay 到 topology/netlist/DXF。

## 7. repair_plan 语义

当前语义使用 `repair_plan`，不再以旧字段 `selected_candidate_ids` 作为主语义。

一个 repair_plan 大致长这样：

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
    },
    {
      "step_id": "S2",
      "candidate_id": "MRG1",
      "repair_type": "merge_nodes",
      "depends_on": ["S1"]
    }
  ]
}
```

这样可以表达多问题 case 的顺序修复，而不是“一次只选一个候选”。

## 8. Human Approval

Advisor 阶段只输出建议。真正修改由 `main_run.py` 在人类确认后完成：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
python -B main_run.py data\generated\handdrawn_advanced_showcase\301_three_stage_rc_ladder.png `
  --run-name demo_301 `
  --proposal-backend yolo `
  --backend deepseek `
  --model deepseek-v4-pro
```

Apply 会：

1. 读取 advisor report 和 repair_plan。
2. 生成 approval request。
3. 写入 approval decision。
4. 按计划 replay 候选。
5. 重新生成 corrected topology/netlist/DXF。
6. 生成 replay report。

## 9. Eval Harness

Eval harness 当前 schema 为：

```text
3.8-agent-eval-harness
3.8-agent-eval-summary
```

它评估：

- Agent 是否使用了 LLM；
- 是否调用了合理工具；
- 是否有 repair_plan；
- apply/replay 是否存在；
- repair 前后 topology/netlist 指标是否改善；
- 可选 LLM semantic eval 是否认为修复语义合理。

## 10. Failure Memory

Failure memory 当前 schema：

```text
3.7-failure-memory
```

它记录历史失败模式，例如：

- 某类元件经常发生 axis fallback；
- 某类图容易产生 single-pin stub；
- 某种修复经常被人工接受或拒绝。

Memory 不是核心识别依据，而是给 Planner 提供可解释的经验提示。

## 11. 常用命令

运行单图完整 Agent 闭环：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
python -B main_run.py data\generated\handdrawn_advanced_showcase\301_three_stage_rc_ladder.png `
  --run-name demo_301 `
  --proposal-backend yolo `
  --backend deepseek `
  --model deepseek-v4-pro `
  --audit-backend deepseek `
  --workflow-engine langgraph `
  --max-agent-tool-steps 12 `
  --max-tool-calls-per-step 1
```

## 12. 当前边界

Agent 层已经具备真实工具调用闭环，但仍有边界：

- 它依赖确定性主链路输出的 topology/netlist/evidence。
- 它不能直接从原图像像素中重新识别结构。
- 它只能 apply 已实现 repair tool family 覆盖的修改。
- 多步 repair 已由 `repair_plan` 表达，但复杂图仍需要 human review。

这是一种更适合严谨工程场景的 Agent 设计：LLM 负责策略，工具负责事实与执行，人的批准负责最终结构变更。
