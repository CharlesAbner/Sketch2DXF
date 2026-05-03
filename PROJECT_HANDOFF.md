# Sketch2DXF 项目交接笔记

> Historical note: this file records the project state across earlier 2.x / 3.0
> development steps. For the current runnable interface, use `README.md`,
> `AGENT_WORKFLOW.md`, and `DATA_SCHEMA.md` as the source of truth. The current
> agent suite is 3.7: LangGraph advisor, human-approved apply/replay, eval
> harness, failure memory, and polished DXF export.

## 1. 项目目标

本项目目标不是把手绘线条像素级完美复原，而是从手绘电路图中恢复结构化电路拓扑：

- components
- pins / terminals
- electrical nodes
- connections
- nets / netlist
- DXF / overlay / audit output

一句话：

```text
手绘图像 -> 元件语义 -> 连接证据 -> 电气拓扑 -> 工程输出
```

## 2. 当前核心共识

导线提取不再承担“最终真相”的角色。

手绘图里存在断笔、抖动、字母干扰、元件粘连和风格差异。如果强依赖完整 wire mask 或完美 Hough，会导致参数越来越脆。

当前主线是：

```text
component semantics
-> wire evidence
-> terminal anchoring
-> supported graph
-> graph-derived electrical nodes
-> topology / netlist
```

## 3. 当前 2.0 主链路

2.0 已经打通并切到 graph-derived nodes：

```text
input image
-> preprocess
-> proposal / YOLO component detection
-> pin locator
-> component masking + wire_extract
-> junction_detect
-> evidence_graph
-> terminal_attachments
-> supported_graph
-> graph_nodes_dry_run
-> graph_node_selector
-> topology_builder
-> validation / export
-> audit_inputs
```

关键模块：

- `src/perception/yolo_component_detector.py`
- `src/perception/component_proposals.py`
- `src/perception/wire_extract.py`
- `src/perception/junction_detect.py`
- `src/topology/pin_locator.py`
- `src/topology/evidence_graph.py`
- `src/topology/terminal_attachment.py`
- `src/topology/supported_graph.py`
- `src/topology/graph_node_dry_run.py`
- `src/topology/graph_node_selector.py`
- `src/topology/topology_builder.py`
- `src/topology/audit_inputs.py`

## 4. 2.0 六步完成状态

### Step 1: evidence graph

已完成。

输出 `evidence_graph.json`，包括：

- vertices
- edges
- raw_components
- bridge_candidates
- stats

作用：让导线证据成为可审计图结构。

### Step 2: terminal attachments

已完成。

输出 `terminal_attachments.json`，回答每个 pin 最可能贴到哪个 segment / raw_component。

作用：把 terminal 作为锚点引入 evidence graph。

### Step 3: supported graph

已完成。

输出 `supported_graph.json`，区分：

- terminal-supported evidence
- relay-supported evidence
- unsupported evidence

`relay_supported` 用于保留没有 terminal 直接支持、但桥接多个 supported components 的中继导线。

### Step 4: graph nodes dry-run

已完成。

输出 `graph_nodes_dry_run.json`，基于 supported graph 生成候选 graph nodes，并与 legacy nodes 做 diff。

当前关键样例均 exact match。

### Step 5: graph-derived nodes 接管主链路

已完成。

`graph_node_selector.py` 默认使用 graph-derived nodes，并保留 legacy fallback。

核心配置：

```python
use_graph_derived_nodes = True
graph_nodes_enable_fallback = True
```

输出 `node_selection.json`，记录：

- selected_node_source
- fallback_used
- fallback_reasons
- graph/legacy consistency score
- node_diff_stats

### Step 6: audit-ready semantics

已完成。

输出 `audit_inputs.json`，作为人工和 Agent 的统一审计入口。

包含：

- summary
- components
- pins
- nodes
- nets
- evidence_summary
- node_selection
- node_diff
- validation
- export
- risk_flags

## 5. 当前样例验收结果

001 / 003 / 004 / 005 当前均满足：

```text
selected_node_source = graph_derived
fallback_used = false
consistency_score = 1.0
export_success = true
所有 pin 均连接
```

已观察到的风险项主要是低置信 pin match：

- 001: 1 个 low_confidence_match
- 003: 1 个 low_confidence_match
- 004: 1 个 low_confidence_match，1 个 weak_confidence_match，1 个 relay_supported_node，3 个 unsupported_evidence
- 005: 1 个 duplicate_component_suppressed，1 个 low_confidence_match

这些风险项不会阻断当前 topology，但应作为后续调参和 Agent 审计重点。

## 6. 当前最该看的输出文件

不要直接读所有 JSON。优先看：

1. `audit_inputs.json`
2. `node_selection.json`
3. `validation.json`
4. `nodes.json`
5. `topology.json`
6. `netlist.json`

判断顺序：

```text
node_selection 是否 graph_derived 且无 fallback
-> validation 是否 1.0
-> audit_inputs 是否 error=0
-> nodes 的 support_status 是否合理
-> netlist 是否符合电路语义
```

## 7. Agent 接入建议

现在可以开始考虑 Agent，但不要让 Agent 直接参与底层像素/几何识别。

推荐顺序：

1. Audit Agent：读取 `audit_inputs.json`，输出审计报告。
2. Explanation Agent：根据 topology 和 audit inputs 生成自然语言解释。
3. Repair Agent：根据 risk flags 给出修复建议，但先不自动改结果。
4. Fusion / Planner Agent：等多策略候选更多后再考虑。

Agent 最适合处理：

- low confidence match 解释
- unsupported evidence 判断
- relay-supported node 解释
- floating / isolated / missing connection 报告
- 面向答辩的自然语言说明

## 8. 代码清理记录

已清理：

- `src/perception/component_proposals_yolo.py`：空文件，实际 YOLO proposal 入口在 `yolo_component_detector.py`。
- `src/**/__pycache__`：Python bytecode 缓存。

保留：

- `node_builder.py`：仍作为 legacy fallback。
- `src/agent/*`：轻量 pipeline summary helper；真正的动态 agent 位于 `src/agent_workflow/*`。
- `export/schema.py`、`io_utils/vis_io.py`：轻量接口/工具，不影响主链路。

## 9. 下一步建议

短期：

- 用更多样例跑 `audit_inputs.json` 质量表。
- 统计低置信 match 的类型和分布。
- 根据 risk flags 决定是否调 terminal corridor / match confidence。

中期：

- Agent 读取 `audit_inputs.json` 生成审计报告。
- 增强 repair suggestions，但保持人工确认。
- 对 005 这类低置信匹配样例做 targeted 调参。

长期：

- 更强的 path inference。
- 更稳的 bus / T-junction 推断。
- 更规整的 schematic layout。

## 10. 2.1 Repair Candidates 更新

2.1 已新增确定性修复候选输出 `repair_candidates.json`。

它的定位是：

```text
risk_flags -> repair_candidates
```

也就是把 2.0 里的低置信匹配、歧义 attachment、unsupported evidence、possible gap bridge、relay node 等风险进一步整理成结构化候选项，方便人工和后续 Agent 复核。

重要约束：

- 不自动修改 topology。
- 不直接覆盖 graph-derived nodes。
- 不直接应用修复。
- 每个候选都标记 `status = candidate_only`。

当前常见候选类型：

- `low_confidence_pin_match`
- `weak_confidence_pin_match`
- `ambiguous_terminal_attachment`
- `unsupported_evidence_review`
- `possible_gap_bridge`
- `relay_node_review`
- `graph_legacy_diff_review`
- `consistency_warning_review`

正式 pipeline 中，结果会放在：

```python
state["validation"]["repair_candidates"]
```

debug run 中，结果会保存为：

```text
outputs/debug_runs/<run_name>/repair_candidates.json
```

## 11. 2.2 Agent-ready Summary / Regression 更新

2.2 已新增两个稳定检查入口：

```text
case_summary.json
regression_report.json
```

`case_summary.json` 是单图入口，用于快速判断：

- 当前样例是 `pass`、`needs_review` 还是 `fail`。
- 是否使用 graph-derived nodes。
- 是否 fallback。
- 是否导出成功。
- risk 和 repair candidate 数量。
- 人工或 Agent 应优先看的 review focus。

debug run 中，结果会保存为：

```text
outputs/debug_runs/<run_name>/case_summary.json
```

正式 pipeline 中，结果会放在：

```python
state["validation"]["case_summary"]
```

`regression_report.json` 由根目录脚本生成：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B run_regression.py --proposal-backend yolo
```

默认输出：

```text
outputs/regression/regression_report.json
```

2.2 同时新增两份约束文档：

- `ISSUE_TAXONOMY.md`：统一 risk flags / repair candidates 的问题类型。
- `CONFIDENCE_SCHEMA.md`：说明各类分数的语义和使用边界。

这一步仍然不引入 Agent，也不自动修复 topology；它只是把 2.0 / 2.1 的结果整理成后续 Agent 和人工排查都能稳定使用的接口。

## 12. 3.0 Agent Audit Workflow

3.0 已新增只读 Agent Audit workflow，位置：

```text
src/agent_workflow/
tools/run_agent_audit.py
AGENT_WORKFLOW.md
```

它读取 2.2 生成的 debug artifacts，完成：

- deterministic fact extraction
- topology semantic audit
- stage diagnosis
- next-action planning
- optional LLM assessment
- `agent_audit_report.json` / `agent_audit_report.md` 输出

离线规则版：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_audit.py outputs\debug_runs\<run_name> --backend rule
```

可选 OpenAI 后端：

```powershell
$env:OPENAI_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_audit.py outputs\debug_runs\<run_name> --backend openai
```

可选 DeepSeek 后端：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_audit.py outputs\debug_runs\<run_name> --backend deepseek --model deepseek-v4-flash
```

可选自定义 OpenAI-compatible 后端：

```powershell
$env:CUSTOM_LLM_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_audit.py outputs\debug_runs\<run_name> --backend custom --base-url https://your-provider.example/v1 --model your-model-name
```

`--llm-provider` 是 `--backend` 的别名。代码不会把 API key 写入 `agent_audit_report.json` / `agent_audit_report.md`。
DeepSeek/custom 不会自动复用 `OPENAI_API_KEY`，如果需要复用某个环境变量，应显式传入 `--api-key-env`。

当前 3.0 约束：

- 不让 LLM 直接识别图片。
- 不自动修改 `topology.json`。
- 不直接应用 repair candidate。
- LLM 只基于结构化 artifacts 做补充审查和解释。

代表性验证：

- `probe_104_baseline`：定位为 `graph_node_merge / split_real_connection_or_missing_bridge`。
- `probe_107_baseline`：定位为 `capability_boundary / non_orthogonal_wire_not_supported`。
- `probe_105_crossing_no_connect`：审查通过。
- `probe_110_baseline`：审查通过，弱匹配只保留低优先级 spot-check。

下一步建议是 3.1：把 3.0 的 `recommended_actions` 接到 dry-run repair tools，但仍然不自动改正式 topology。

## 13. 3.0.1 Agent Audit 加固

3.0.1 已完成三项加固：

- 核心 debug artifacts 缺失时，报告直接标记为 `fail / insufficient_artifacts / artifact_loading`，避免空目录或旧目录被误判为 pass。
- LLM 输出被拆分为 `confirmed_by_artifacts`、`hypotheses`、`low_priority_notes`、`reasoning_notes` 和 `recommended_actions`。
- deterministic `pass` 是权威结论；LLM 的额外猜测只能降级为低优先级备注，不能直接推翻主链路。
- `--workflow-engine langgraph` 必须真的使用 LangGraph；只有 `--workflow-engine auto` 才允许 fallback 到 local。

已安装到 `sketch2dxf` conda 环境：

```text
openai
langgraph
```

验证样例：

- `probe_104_baseline --backend rule --workflow-engine langgraph`：真实 LangGraph 路径，输出 `workflow_engine = langgraph`。
- `probe_105_crossing_no_connect`：缺核心 artifacts，输出 `fail / insufficient_artifacts / artifact_loading`。
- `check_105_crossing_no_connect --backend rule --workflow-engine langgraph`：完整 artifacts，输出 `pass`。
- `probe_104_baseline --backend deepseek --workflow-engine langgraph`：无 key 时记录 `DEEPSEEK_API_KEY is not set`，规则审查不崩。

## 14. 3.1 Repair Dry-run

已新增：

```text
src/agent_workflow/repair_dry_run.py
tools/run_agent_repair_dry_run.py
```

命令：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_dry_run.py outputs\debug_runs\<run_name>
```

输出：

```text
agent_repair_dry_run.json
agent_repair_dry_run.md
```

当前已完成 Step 1 到 Step 5：

- 读取 `agent_audit_report.json`。
- 根据 audit 结果选择后续 dry-run tool。
- 独立检查核心 debug artifacts 是否完整。
- 生成 `merge_nodes_dry_run` 候选。
- 生成 `reattach_pin_dry_run` 候选，用于审查弱 terminal attachment 是否可能改接到其他 node。
- 生成 `evidence_review_dry_run` 候选，用于统一审查 unsupported evidence 和 possible bridge。
- 对候选计算 before/after metrics、`validation_result`、`blocking_issues`、`score`、`risk_level` 和 `recommendation`。
- 通过 `src/agent_workflow/candidate_validator.py` 输出统一 `metric_deltas`、`improved_metrics`、`regressed_metrics`、`non_blocking_warnings`。
- 通过 `src/agent_workflow/candidate_ranker.py` 输出统一 `ranking_score`、`ranking_factors`、`ranking_reasons` 和最终 `recommendation`。
- 记录 `topology.json`、`netlist.json`、`14_export.dxf` 的 hash。
- 不修改正式 topology/netlist/DXF。

`probe_104_baseline` 当前会生成 `MRG1`：dry-run merge `N2 + N4`，single-pin net count 从 `2` 降到 `0`，且无 blocking issues，ranker 推荐 `accept_for_human_review`；同时会把 unsupported evidence 转成 `EVR*` 审查候选。`probe_106_baseline` 会把 possible bridge 排到 evidence review 候选前列。

## 15. Step 6 Tool-calling Repair Advisor

已新增：

```text
src/agent_workflow/repair_advisor.py
tools/run_agent_repair_advisor.py
```

这一步把前五步的 tool layer 包装成真正的 tool-calling workflow：

```text
agent_audit_report.json
-> planner
-> repair_dry_run tool call
-> tool result
-> reviewer
-> agent_repair_advisor_report.json / md
```

命令：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\<run_name> --backend rule --workflow-engine langgraph
```

DeepSeek 版：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\<run_name> --backend deepseek --model deepseek-v4-flash --workflow-engine langgraph
```

输出：

```text
agent_repair_advisor_report.json
agent_repair_advisor_report.md
tool_calls/01_repair_dry_run/agent_repair_dry_run.json
tool_calls/01_repair_dry_run/agent_repair_dry_run.md
```

已验收 `probe_104_baseline --backend rule --workflow-engine langgraph`：

```text
planner_kind = rule
tool_calls = repair_dry_run
tool_result_count = 1
final_decision = candidate_ready_for_human_review
selected_candidate_ids = MRG1
topology_mutated = false
```

也验收了 `--backend mock`，确认模拟 LLM 后端同样会经过 planner / tool call / reviewer，而不是只生成摘要。

## 15. Current 3.7 Snapshot

当前代码已经在 3.4 advisor 之后继续补齐：

- 3.5 human-approved repair apply/replay。
- 3.6 agent eval harness，支持单 case 和多 case 评估。
- 3.7 failure memory，将 eval 暴露的失败模式注入下一次 advisor observation。
- DXF 导出增加 `PINS / NODES / TITLE / REPAIR` 层，便于展示 corrected artifacts。

因此，本交接笔记中早期关于 “后续 Agent” 的描述只作为历史上下文保留；当前可运行命令以 `README.md` 和 `AGENT_WORKFLOW.md` 为准。
