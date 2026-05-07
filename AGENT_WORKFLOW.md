# Sketch2DXF Agent Workflow

当前 agent 层的版本语义是 3.7：以 3.4 LangGraph-native repair advisor 为核心，外加 human-approved apply/replay、eval harness 和 failure memory。

它的目标不是让 LLM 直接识别图片，也不是让 LLM 直接修改 `topology.json`。它读取 2.2 主链路产出的结构化 artifacts，然后按需调用安全工具，生成审查结论、dry-run 修复候选、人类可读 dossier、审批后的 corrected artifacts、评估报告和长期 failure memory。

## Position

```text
debug artifacts
-> deterministic audit facts
-> LLM/rule planner
-> safe tool calls
-> critic
-> reviewer
-> human review dossier
-> human-approved apply/replay
-> eval harness
-> failure memory
```

Agent 可以是真实 LLM，也可以是 rule/mock 后端。真实 LLM 用于 planner 和 reviewer；规则后端用于离线验证工作流是否可跑。

## Inputs

对一个已经完成的 debug run，例如：

```text
outputs/debug_runs/probe_104_baseline
```

workflow 会读取：

- `case_summary.json`
- `audit_inputs.json`
- `repair_candidates.json`
- `terminal_attachments.json`
- `supported_graph.json`
- `graph_nodes_dry_run.json`
- `topology.json`
- `netlist.json`
- `node_selection.json`
- `validation.json`

缺失 artifact 会被记录为风险，不会让 workflow 直接崩溃。

## Tools

当前暴露给 planner 的工具是有边界的：

- `get_case_summary`: 读 case-level 摘要。
- `get_single_pin_nets`: 查看单 pin net 和相关 pin/component。
- `get_terminal_attachments`: 查看 terminal corridor attachment 证据。
- `get_repair_candidates`: 读确定性 review/repair candidates。
- `repair_dry_run`: 生成、验证、排序修复候选；只 dry-run，不修改 topology/netlist/DXF。

这些工具都适合 LLM 使用，因为它们输出的是压缩后的结构化事实，不要求 LLM 直接读大图或猜像素。

## Dynamic Tool Loop

3.4 不再固定调用一整套工具。LangGraph 路径中的流程是：

1. `audit_tool`: 加载或生成 agent audit report。
2. `observe`: 读取紧凑 observation，包括 case summary、audit、artifact presence、available tools。
3. `plan_next_action`: planner 根据 observation 和已有 tool results 决定下一轮调用 0 到 N 个工具。
4. `execute_tool`: 执行白名单工具。
5. `update_state`: 把工具结果写回 agent state。
6. `decide_continue`: 根据工具结果、stop decision 和预算决定继续规划还是进入审查。
7. `critic`: 检查工具调用是否越界、是否重复、是否缺少必要证据、是否试图直接改 topology。
8. `reviewer`: 综合 audit、tool results、critic 输出最终建议。
9. `human_review_dossier`: 把内部 ID 翻译成人类可检查的 pin/component/evidence/candidate 说明。

每轮最多工具数和最大轮数可配置：

```python
"agent": {
    "max_agent_tool_steps": 6,
    "max_tool_calls_per_step": 3,
}
```

命令行覆盖：

```powershell
--max-agent-tool-steps 6 --max-tool-calls-per-step 3
```

这里的 `3` 不是语义硬编码，只是默认预算。你可以设成 `1` 观察更细的单步决策，也可以设成更大值做批量工具调用。

## LangGraph

`--workflow-engine langgraph` 会使用 LangGraph 的 `StateGraph` 承载节点级循环：

```text
audit_tool -> observe -> plan_next_action -> execute_tool -> update_state -> decide_continue
                                             ^                                      |
                                             |                                      v
                                             +-------------- repeat ----------------+
-> critic -> reviewer -> END
```

planner、argument grounding、tool whitelist、dry-run safety 和输出落盘仍由项目代码中的工具函数负责，但循环控制已经暴露为 LangGraph 节点。

如果没有安装 LangGraph，可用：

```powershell
--workflow-engine local
```

local 版本执行同样语义，只是不经过 LangGraph runtime。

## Commands

规则后端：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend rule --workflow-engine langgraph
```

DeepSeek：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend deepseek --model deepseek-v4-flash --workflow-engine langgraph
```

OpenAI-compatible custom provider：

```powershell
$env:CUSTOM_LLM_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend custom --base-url https://your-provider.example/v1 --model your-model-name --workflow-engine langgraph
```

调小每轮工具调用，观察 agent 是否真的按需决策：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend deepseek --model deepseek-v4-flash --workflow-engine langgraph --max-tool-calls-per-step 1 --max-agent-tool-steps 8
```

## Outputs

`run_agent_repair_advisor.py` 输出：

- `agent_repair_advisor_report.json`
- `agent_repair_advisor_report.md`
- `agent_human_review_dossier.json`
- `agent_human_review_dossier.md`

优先读：

- `agent_repair_advisor_report.md`: agent 流程、工具调用、critic、reviewer 结论。
- `agent_human_review_dossier.md`: 将 `N2 / N4 / MRG1` 等内部 ID 映射到具体 pin/component/evidence。

## Safety Semantics

- Agent 不直接修改 `topology.json`。
- `repair_dry_run` 不写 corrected topology，只产出 candidate。
- `accept_for_human_review` 来自确定性 validator/ranker，不是 LLM 单独拍板。
- LLM reviewer 可以同意、拒绝或要求更多证据，但最终仍是 human review。
- `run_agent_repair_apply.py` 只有在人工给出 `--approval accept` 或 approval file 后才生成 corrected artifacts。
- 原始 `topology.json / netlist.json / 14_export.dxf` 不会被覆盖。
- API key 会被 scrub，不应写入 JSON/Markdown 报告。

## Human Approval And Replay

Step 2 的闭环是：

```text
agent advisor
-> approval_request.json/md
-> human accept/reject
-> corrected_topology.json
-> corrected_netlist.json
-> corrected_export.dxf
-> repair_replay_report.json/md
```

命令：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_apply.py outputs\debug_runs\probe_104_baseline --advisor-dir outputs\debug_runs\probe_104_baseline\agent_34_langgraph_deepseek_check --candidate-id MRG1 --approval accept --approved-by Lzk --output-dir outputs\debug_runs\probe_104_baseline\repair_apply_check
```

当前 apply 支持 `merge_nodes` 候选。后续可以继续扩展 `reattach_pin` 和 evidence-based bridge apply。

## Eval Harness

Step 3 的闭环是：

```text
agent advisor
-> human-approved repair apply
-> deterministic eval
-> optional LLM semantic eval
-> single-case report / multi-case summary
```

命令：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_eval_harness.py --case-dir outputs\debug_runs\probe_104_baseline --advisor-dir outputs\debug_runs\probe_104_baseline\agent_34_langgraph_deepseek_check --apply-dir outputs\debug_runs\probe_104_baseline\repair_apply_check --strategy-name deepseek_v4_pro_apply --llm-backend rule --output-dir outputs\debug_runs\probe_104_baseline\agent_eval_check
```

如果要让 LLM 做整体语义复核：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_eval_harness.py --case-dir outputs\debug_runs\probe_104_baseline --advisor-dir outputs\debug_runs\probe_104_baseline\agent_34_langgraph_deepseek_check --apply-dir outputs\debug_runs\probe_104_baseline\repair_apply_check --strategy-name deepseek_v4_pro_apply --llm-backend deepseek --model deepseek-v4-pro --output-dir outputs\debug_runs\probe_104_baseline\agent_eval_deepseek_check
```

多 case 汇总：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_eval_harness.py --cases-dir outputs\debug_runs --pattern "probe_*_baseline" --strategy-name deepseek_v4_pro_apply --llm-backend rule --output-dir outputs\debug_runs\agent_eval_summary_check
```

确定性评估负责：

- advisor 是否真的按工具循环工作。
- apply 是否经过 human approval。
- 原始 `topology.json / netlist.json / 14_export.dxf` 是否未被覆盖。
- 修复前后 `single_pin_net_count / zero_pin_net_count / component_count / pin_count` 是否健康。
- corrected DXF 是否导出成功。

LLM 语义评估负责：

- 修复后的 netlist 是否更像合理电路。
- merge 是否可能只是指标变好但语义可疑。
- 是否存在短路风险、缺失电源/负载、闭合路径不清楚等高层问题。
- 下一步应该人工看图还是继续查工具。

## Failure Memory

Step 4 把 eval harness 的结论沉淀为长期可读的 failure memory：

```text
agent_eval_report / agent_eval_summary
-> failure_memory.json/md
-> advisor observation.failure_memory
-> planner/reviewer 使用历史失败模式作为上下文
```

更新 memory：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_failure_memory.py update --eval-summary outputs\debug_runs\agent_eval_summary_check\agent_eval_summary.json --memory-file outputs\agent_memory\failure_memory.json
```

查询 memory：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_failure_memory.py query --memory-file outputs\agent_memory\failure_memory.json --debug-dir outputs\debug_runs\probe_104_baseline
```

带 memory 运行 advisor：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend deepseek --model deepseek-v4-pro --workflow-engine langgraph --memory-file outputs\agent_memory\failure_memory.json --memory-limit 5
```

Memory 只提供上下文，不提供最终判决。它的典型用途是提醒 planner：类似图过去常见问题是单 pin net、错误 merge、DXF 导出失败、短路风险，所以下一轮应优先调用哪些检查工具。

## Agent Eval 3.8 Safety Updates

The eval/apply layer now has stricter consistency semantics:

- `run_agent_repair_apply.py` verifies that the target debug run, `case_summary.json`, and `agent_repair_advisor_report.json` all describe the same case before writing approval or corrected artifacts.
- Review-only candidates such as `evidence_review` are not auto-applied. If accepted through the apply CLI, they produce `repair_replay_report.json` with `status=unsupported_apply_type` instead of crashing or writing corrected topology.
- Eval no longer treats a missing replay report as a failed repair by default. It distinguishes `no_action_expected`, `review_only_expected`, `pending_human_approval`, `approval_not_accepted`, `unsupported_apply_type`, and `missing_apply_unexpected`.
- Missing corrected netlist/DXF is only a failure when a replay was actually expected. For no-action or review-only cases, the baseline topology is explicitly marked as retained.
- Eval checks component identity, not just component count. A repaired netlist that changes component id/refdes/class now raises `component_identity_changed`.
- Multi-case summary prefers existing per-case eval reports only when they use the current eval schema and the same `strategy_name`; otherwise it regenerates that case eval.

Reviewer decisions use these normalized labels:

- `repair_candidate_ready_for_human_review`: an apply-able repair candidate, currently `merge_nodes`, is ready for human confirmation.
- `review_only_issue_for_human_review`: evidence or low-confidence issues should be inspected, but no automatic topology correction is selected.
- `needs_more_evidence`: tool results are insufficient or inconclusive.
- `no_candidate_found`: repair dry-run ran but produced no actionable candidate.
- `no_action`: no further action is recommended.

## What Agent Adds

当前 agent 真正负责：

- 判断下一步该看哪些工具结果。
- 根据工具结果决定是否继续查证。
- 把多个 artifacts 中的 evidence 串起来。
- 对 dry-run candidate 做自然语言解释。
- 输出人类可审核的 dossier。
- 在 Step 3 中对 advisor/apply 结果做评估，并可选调用 LLM 进行整体语义复核。
- 在 Step 4 中从 eval 结果沉淀 failure memory，并把相关历史模式注入下一次 advisor observation。

它还没有做到：

- 多策略重跑 pipeline。
- 主动生成新的反例测试。

这些是后续 4.0 的优化方向。
