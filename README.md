# Sketch2DXF

Sketch2DXF 的目标不是把手绘线条像素级复原，而是从手绘电路图中恢复结构化电路拓扑，并导出可检查的 `JSON / netlist / DXF`。

当前主线可以概括为：

```text
元件语义层 -> 连接证据层 -> 拓扑推断层 -> 审计/Agent 层
```

也就是：检测元件和 terminal，用导线提取生成 evidence，再用 terminal 作为锚点构建 supported graph，最后生成 electrical nodes、topology、netlist 和 DXF。LLM/Agent 层不直接识别图片，也不直接改拓扑；它读取结构化 artifacts，按需调用安全工具，给出审查、dry-run 修复候选和人类可读解释。

## Current Pipeline

当前 2.2 主链路如下：

```text
input image
-> preprocess
-> component proposal / YOLO
-> component masking + wire evidence extraction
-> endpoint / junction detection
-> evidence_graph
-> terminal_attachments
-> supported_graph
-> graph-derived nodes
-> topology / netlist / DXF
-> audit_inputs / repair_candidates / case_summary
-> agent 3.7 workflow suite
```

关键语义：

- `wire_extract` 只提供连接证据，不承担最终真相。
- `terminal_attachments` 表示每个 pin 沿 terminal corridor 能看见哪些 raw evidence。
- `supported_graph` 表示哪些 evidence 被 terminal 直接或间接支持。
- `graph_nodes_dry_run` 从 supported graph 生成候选 electrical nodes。
- `node_selection` 决定最终使用 graph-derived nodes 还是 legacy fallback。
- `topology / netlist / DXF` 只应使用最终 active electrical nodes。
- `agent_*` 输出只做审查、解释和 dry-run 建议，不自动覆盖 `topology.json`。

## Environment

常用环境：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B <script> <args>
```

依赖主要在 `requirements.txt`。如果要启用真实 LLM，需要安装 `openai`，如果要启用 LangGraph workflow，需要安装 `langgraph`。当前代码支持没有 LLM key 时退回规则/离线模式，但真实 agent 评估应使用真实 LLM backend。

GitHub 版本默认不提交大型 YOLO 数据集、训练输出和 `.pt` 权重。若使用 `--proposal-backend yolo`，请在本机保留或重新放置 `src/config.py` 中配置的权重路径；若只想验证主流程接口，可使用 `--proposal-backend traditional`。

## Debug Run

单图逐阶段调试：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B debug_run.py data\samples_easy\001_series_loop.png --proposal-backend yolo --run-name my_001_check --debug-level standard
```

常用参数：

- `--stage`: 执行到哪个最高阶段，后面的阶段不会跑；例如 `wire` 会自动包含 preprocess/proposal/wire。
- `--debug-level standard`: 保存紧凑但够审查的 artifacts。
- `--debug-level full`: 额外保存更重的中间图和中间 JSON，适合深挖底层错误。
- `--proposal-backend yolo`: 使用 YOLO proposal；也可以用 `traditional`。

输出目录：

```text
outputs/debug_runs/<run_name>/
```

优先看这些文件：

- `13_overlay.png`: 最终拓扑叠加图。
- `12_active_nodes.png`: 最终 active electrical nodes。
- `evidence_graph.json`: 原始导线证据图。
- `terminal_attachments.json`: 每个 pin 能附着到哪些 raw evidence。
- `supported_graph.json`: 被 terminal 支持后的证据图。
- `graph_nodes_dry_run.json`: supported graph 生成的候选 nodes。
- `node_selection.json`: 最终节点来源和 fallback 情况。
- `nodes.json`: 最终 electrical nodes。
- `matches.json`: pin 到 node 的匹配。
- `topology.json` / `netlist.json`: 最终拓扑和网表。
- `audit_inputs.json`: 审计和 agent 的统一结构化输入。
- `repair_candidates.json`: 规则系统生成的可疑点和 dry-run 前候选。
- `case_summary.json`: 单图质量摘要。

## Pipeline CLI

如果只想跑正式 pipeline，不需要 debug 图：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_pipeline.py data\samples_easy\001_series_loop.png --proposal-backend yolo
```

默认会写：

```text
outputs/reports/<image_stem>_pipeline_summary.json
```

也可以指定路径：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_pipeline.py data\samples_easy\001_series_loop.png --proposal-backend yolo --output-summary outputs\reports\my_pipeline_summary.json
```

Python API：

```python
from src.config import get_default_config
from src.pipeline import run_pipeline

config = get_default_config()
config["detector"]["proposal_backend"] = "yolo"
state = run_pipeline("data/samples_easy/001_series_loop.png", config)
```

## Regression

跑固定样例回归：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B run_regression.py --proposal-backend yolo
```

只跑某个 case：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B run_regression.py --proposal-backend yolo --case 001_series_loop
```

默认报告：

```text
outputs/regression/regression_report.json
```

## Stress Tests

生成手绘压力测试图：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\generate_handdrawn_tests.py --output-dir data\generated\handdrawn_stress
```

运行规则消融、参数扰动和反例压力测试：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_generalization_probe.py --manifest data\generated\handdrawn_stress\manifest.json --start-index 1 --first-n 10 --output-dir outputs\generalization_probe\my_probe
```

输出：

- `generalization_probe_report.json`: 完整矩阵。
- `generalization_probe_matrix.csv`: 方便表格查看。

这一步主要用于判断规则是否过拟合、参数是否脆弱、反例是否会骗过 terminal corridor / supported graph / node merge。

## Agent 3.7 / Eval 3.8

Agent advisor/apply/eval now uses no-apply-aware eval semantics:

- Advisor final decisions are normalized into `repair_candidate_ready_for_human_review`, `review_only_issue_for_human_review`, `needs_more_evidence`, `no_candidate_found`, and `no_action`.
- Apply refuses to mix a target debug run with an advisor report from another case.
- Only apply-able `merge_nodes` candidates generate corrected topology. Review-only candidates generate review/replay metadata but do not mutate topology.
- Eval distinguishes expected no-apply cases from real missing apply outputs, so no-action and review-only cases are not scored as lost topology.
- Eval also checks component identity, so a repair that changes component id/refdes/class is flagged as `component_identity_changed`.

## Agent 3.7

当前 agent 层以 3.4 LangGraph-native tool state machine 为核心，并已经补上 3.5 human-approved repair apply/replay、3.6 eval harness 和 3.7 failure memory。它不是固定调用 5 个工具，而是每轮由 planner 决定调用 0 到 N 个工具，N 由配置或命令行控制。默认每轮最多 3 个工具，最多 6 轮。

使用 `--workflow-engine langgraph` 时，trace 中能看到节点级循环：

```text
audit_tool -> observe -> plan_next_action -> execute_tool -> update_state -> decide_continue
                                             ^                                      |
                                             |                                      v
                                             +-------------- repeat ----------------+
-> critic -> reviewer
```

使用 `--workflow-engine local` 时，执行同样语义，但循环仍由本地顺序代码承载，主要作为无 LangGraph 环境的备用路径。

默认配置在 `src/config.py`：

```python
"agent": {
    "enable_audit_agent": True,
    "enable_explanation_agent": True,
    "max_agent_tool_steps": 6,
    "max_tool_calls_per_step": 3,
}
```

命令行可覆盖：

```powershell
--max-agent-tool-steps 6 --max-tool-calls-per-step 3
```

### Agent Tools

当前暴露给 planner 的工具：

- `get_case_summary`: 读取紧凑质量摘要。
- `get_single_pin_nets`: 查看单 pin net 以及相关 pin/component。
- `get_terminal_attachments`: 查看 terminal corridor attachment 证据。
- `get_repair_candidates`: 读取确定性候选问题。
- `repair_dry_run`: 生成、验证并排序非破坏性修复候选，不修改 topology/netlist/DXF。

`accept_for_human_review` 是 dry-run validator/ranker 的确定性建议，然后 LLM reviewer 会基于工具结果做最终审查。它不是“自动修复通过”，也不会直接写 corrected topology。

### Rule Backend

不用 LLM，跑 agent 工作流的规则后端：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend rule --workflow-engine langgraph
```

### DeepSeek Backend

推荐用环境变量传 key，避免 key 进入 shell 历史或日志：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend deepseek --model deepseek-v4-flash --workflow-engine langgraph
```

DeepSeek 默认 base URL：

```text
https://api.deepseek.com
```

也可以覆盖：

```powershell
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend deepseek --model deepseek-v4-flash --workflow-engine langgraph
```

### OpenAI / Custom Compatible Backend

OpenAI：

```powershell
$env:OPENAI_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend openai --model gpt-4o-mini --workflow-engine langgraph
```

自定义 OpenAI-compatible 服务：

```powershell
$env:CUSTOM_LLM_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend custom --base-url https://your-provider.example/v1 --model your-model-name --workflow-engine langgraph
```

### Agent Outputs

默认写回对应 debug run 子目录：

```text
agent_repair_advisor_report.json
agent_repair_advisor_report.md
agent_human_review_dossier.json
agent_human_review_dossier.md
```

人类优先看：

- `agent_repair_advisor_report.md`: agent 调用了什么工具、得到什么结果、最终建议是什么。
- `agent_human_review_dossier.md`: 把 `N2 / N4 / MRG1` 这类内部 ID 翻译成人能读的 pin、component、attachment、candidate 说明。

### Human-Approved Repair Apply

Agent advisor 只生成候选，不自动修改拓扑。Step 2 新增的 apply 入口需要人工明确批准。

只生成审批请求：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_apply.py outputs\debug_runs\probe_104_baseline --advisor-dir outputs\debug_runs\probe_104_baseline\agent_34_langgraph_deepseek_check --candidate-id MRG1 --approval pending --output-dir outputs\debug_runs\probe_104_baseline\repair_apply_check
```

人工确认后应用：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_apply.py outputs\debug_runs\probe_104_baseline --advisor-dir outputs\debug_runs\probe_104_baseline\agent_34_langgraph_deepseek_check --candidate-id MRG1 --approval accept --approved-by Lzk --notes "confirmed from overlay" --output-dir outputs\debug_runs\probe_104_baseline\repair_apply_check
```

输出：

- `approval_request.json`
- `approval_request.md`
- `approval_decision.json`
- `corrected_topology.json`
- `corrected_netlist.json`
- `corrected_export.dxf`
- `repair_replay_report.json`
- `repair_replay_report.md`

原始 `topology.json / netlist.json / 14_export.dxf` 不会被覆盖。

### Agent Eval Harness

Step 3 新增 `tools/run_agent_eval_harness.py`。它不重新识别图片，也不自动批准修复；它读取 advisor/apply 已经产生的 artifacts，给出可重复的评估报告。

单 case 规则评估：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_eval_harness.py --case-dir outputs\debug_runs\probe_104_baseline --advisor-dir outputs\debug_runs\probe_104_baseline\agent_34_langgraph_deepseek_check --apply-dir outputs\debug_runs\probe_104_baseline\repair_apply_check --strategy-name deepseek_v4_pro_apply --llm-backend rule --output-dir outputs\debug_runs\probe_104_baseline\agent_eval_check
```

单 case + LLM 语义评估：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_eval_harness.py --case-dir outputs\debug_runs\probe_104_baseline --advisor-dir outputs\debug_runs\probe_104_baseline\agent_34_langgraph_deepseek_check --apply-dir outputs\debug_runs\probe_104_baseline\repair_apply_check --strategy-name deepseek_v4_pro_apply --llm-backend deepseek --model deepseek-v4-pro --output-dir outputs\debug_runs\probe_104_baseline\agent_eval_deepseek_check
```

多 case 汇总：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_eval_harness.py --cases-dir outputs\debug_runs --pattern "probe_*_baseline" --strategy-name deepseek_v4_pro_apply --llm-backend rule --output-dir outputs\debug_runs\agent_eval_summary_check
```

输出：

- `agent_eval_report.json`
- `agent_eval_report.md`
- `agent_eval_summary.json`
- `agent_eval_summary.md`

规则评估负责 artifact 完整性、approval/apply 安全、before/after 指标、DXF 导出、single-pin/zero-pin regression 等硬约束。LLM 语义评估是可选项，负责判断修复后的拓扑是否在电路语义上更合理、是否有短路风险、是否需要继续人工看图。

### Failure Memory

Step 4 新增 failure memory。它从 `agent_eval_report.json` 或 `agent_eval_summary.json` 沉淀常见失败模式，下一次 advisor 可以通过 `--memory-file` 把这些模式作为上下文读入。

从单个 eval report 更新 memory：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_failure_memory.py update --eval-report outputs\debug_runs\probe_104_baseline\agent_eval_check\agent_eval_report.json --memory-file outputs\agent_memory\failure_memory.json
```

从多 case summary 更新 memory：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_failure_memory.py update --eval-summary outputs\debug_runs\agent_eval_summary_check\agent_eval_summary.json --memory-file outputs\agent_memory\failure_memory.json
```

查询某个 case 对应的历史失败模式：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_failure_memory.py query --memory-file outputs\agent_memory\failure_memory.json --debug-dir outputs\debug_runs\probe_104_baseline --output-dir outputs\debug_runs\probe_104_baseline\memory_query_check
```

让 advisor 使用 memory：

```powershell
D:\Miniconda\envs\sketch2dxf\python.exe -B tools\run_agent_repair_advisor.py outputs\debug_runs\probe_104_baseline --backend deepseek --model deepseek-v4-pro --workflow-engine langgraph --memory-file outputs\agent_memory\failure_memory.json --memory-limit 5 --output-dir outputs\debug_runs\probe_104_baseline\agent_memory_check
```

Memory 不是规则真理，也不会自动修图。它的作用是让 agent 知道“类似 case 以前失败在哪里、应该优先查什么”。

### DXF Polish

当前 DXF 导出仍保留原图相对几何，但增加了展示友好的层：

- `WIRES`
- `COMPONENTS`
- `LABELS`
- `NETS`
- `PINS`
- `NODES`
- `TITLE`
- `REPAIR`

`REPAIR` 层会记录 human-approved repair 的 candidate/type/approval，便于答辩时说明 corrected DXF 来自哪一次审批修复。

## Current Status

当前已经完成：

- 2.0: evidence graph、terminal attachments、supported graph、graph-derived node 骨架。
- 2.1: deterministic repair candidate / dry-run 修复候选。
- 2.2: case summary、回归检查、agent-ready artifacts。
- 3.4: LangGraph-native tool state machine，支持 rule/mock/openai/deepseek/custom backend，支持 human review dossier。
- 3.5: human-approved repair apply/replay，支持审批请求、人工批准、corrected topology/netlist/DXF 和 replay report。
- 3.6: agent eval harness，支持单 case 评估、多 case 汇总和可选 LLM 语义评估。
- 3.7: failure memory + advisor memory context + DXF 展示层增强。

仍然要注意：

- Agent 现在仍是“审查和建议层”，不会自动提交修复。
- 规则和参数仍需要靠 stress tests 暴露脆弱点。
- 真实 LLM 的价值主要体现在工具选择、证据串联、审查解释和候选判断，而不是直接看图识别。

## Key Files

- `debug_run.py`: 单图逐阶段 debug。
- `tools/run_pipeline.py`: 正式 pipeline 命令行入口。
- `run_regression.py`: 固定样例回归。
- `tools/generate_handdrawn_tests.py`: 生成压力测试图。
- `tools/run_generalization_probe.py`: 规则消融、参数扰动、反例测试。
- `tools/run_agent_audit.py`: 早期只读 audit workflow。
- `tools/run_agent_repair_dry_run.py`: deterministic repair dry-run。
- `tools/run_agent_repair_advisor.py`: 当前推荐的 agent advisor 入口。
- `tools/run_agent_repair_apply.py`: 人工批准后应用 dry-run repair candidate。
- `tools/run_agent_eval_harness.py`: Step 3 评估 advisor/apply 输出，支持单 case 和多 case 汇总。
- `tools/run_agent_failure_memory.py`: Step 4 从 eval 输出更新/查询 failure memory。
- `src/config.py`: 统一配置。
- `src/pipeline.py`: 正式 pipeline API。
- `src/agent_workflow/repair_advisor.py`: agent advisor 的 LangGraph-native 工具状态机核心。
- `src/agent_workflow/eval_harness.py`: agent 3.6 评估逻辑和报告生成。
- `src/agent_workflow/failure_memory.py`: agent 3.7 失败模式记忆。
