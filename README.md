# Sketch2DXF

Sketch2DXF 是一个面向手绘电路草图的语义解析与结构化重建项目。输入是一张普通 PNG/JPG 位图，系统输出可编辑的 DXF 矢量图，同时生成 topology、netlist、审计报告与 Agent 修复记录。

项目当前的核心思想不是“把手绘线条逐像素完美复原”，而是：

> 用元件语义作为锚点，把导线识别降级为 evidence，再通过拓扑推断和受控 Agent 工具调用恢复电路结构。

## 当前流程

```text
input image
  -> preprocess
  -> component proposal / YOLO detection
  -> terminal hypotheses
  -> wire evidence extraction
  -> evidence_graph
  -> terminal_attachments
  -> supported_graph
  -> graph-derived nodes with legacy fallback
  -> topology / netlist
  -> clean DXF export
  -> deterministic audit
  -> LangGraph Agent advisor
  -> human-approved repair apply / replay
  -> agent eval summary
```

核心分层：

- **元件语义层**：检测元件类别、bbox、候选类别置信度，并生成 terminal hypotheses。
- **连接证据层**：mask 元件后从 residual 中提取 wire segments、junctions、raw graph。
- **拓扑推断层**：以 terminal 为锚点筛选 supported evidence，生成 electrical nodes、nets、netlist。
- **Agent 增强层**：LLM 不直接改 topology，而是循环调用工具观察、验证 dry-run repair、生成 repair_plan，最后必须经过 human approval 才能 apply。
- **DXF 重建层**：默认 clean 模式根据 topology 生成规整 DXF；复杂网状结构会回退到几何保持式导出。

## 环境

推荐使用独立 conda 环境：

```powershell
conda activate sketch2dxf
pip install -r requirements.txt
```

如果需要调用 DeepSeek 或 OpenAI：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
$env:OPENAI_API_KEY="your_key"
```

项目支持 `openai`、`deepseek`、`custom` 等兼容 OpenAI Chat Completions 风格的后端；`custom` 可通过 `--base-url` 指定服务地址。

## 调试单张图片

标准 debug 会输出紧凑的检查文件，适合日常验证：

```powershell
python -B debug_run.py data\samples_easy\001_series_loop.png --proposal-backend yolo --run-name my_001_check --debug-level standard
```

完整 debug 会额外保存较重的中间文件：

```powershell
python -B debug_run.py data\samples_easy\001_series_loop.png --proposal-backend yolo --run-name my_001_full --debug-level full
```

`--stage` 控制运行到哪一层；`--debug-level` 控制保存多少中间产物。常用阶段包括 preprocess、proposal、wire、junction、pins、nodes、topology、export、all。

## 主流程 CLI

日常复现建议使用两类入口：

- `debug_run.py`：只跑确定性主链路，保存逐层 debug artifacts。
- `main_run.py`：跑完整闭环，包含确定性主链路、Agent advisor、人工确认、repair apply 和 eval。

Debug 输出目录通常位于：

```text
outputs/debug_runs/<run-name>/
```

核心输出：

- `topology.json`：结构化拓扑。
- `netlist.json`：元件-引脚-net 关系。
- `case_summary.json`：审计摘要。
- `13_overlay.png`：检测、pin、node、wire 可视化。
- `14_export.dxf` / `corrected_export.dxf`：DXF 输出。

## Agent 工作流

当前 Agent 层是 3.9 语义：`schema_version = 3.9-hypothesis-tool-agent`。

外层可以由 LangGraph `StateGraph` 承载：

```text
audit_tool -> observe -> planner_tool_loop -> critic -> reviewer -> END
```

其中 planner/tool loop 由项目代码控制，原因是这里需要严格保证：

- 工具白名单；
- 参数 grounding；
- dry-run safety；
- human approval；
- repair replay；
- 输出可落盘、可复查。

单图完整 Agent 闭环推荐统一使用 `main_run.py`。它会顺序完成 deterministic debug、Agent advisor、人工确认、repair apply 和 eval：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
python -B main_run.py data\generated\handdrawn_advanced_showcase\301_three_stage_rc_ladder.png `
  --run-name demo_301 `
  --proposal-backend yolo `
  --backend deepseek `
  --model deepseek-v4-pro `
  --api-key-env DEEPSEEK_API_KEY `
  --audit-backend deepseek `
  --workflow-engine langgraph `
  --max-agent-tool-steps 12 `
  --max-tool-calls-per-step 1
```

Advisor 会生成：

- `agent_repair_advisor_report.json/.md`
- `agent_human_review_dossier.json/.md`

如果报告里有 `repair_plan`，`main_run.py` 会在命令行打印报告路径并等待输入 `yes`；确认后才会生成 corrected topology/netlist/DXF 和 replay report。

## Agent 工具族

Agent 不是直接让 LLM “看图改答案”，而是让 LLM 在受控工具空间里提出假设、调用工具、验证 dry-run。

主要工具族：

- Compact overview：`get_case_summary`、`get_single_pin_nets`、`get_terminal_attachments`、`get_repair_candidates`、`repair_dry_run`
- Inspection tools：`inspect_component_class_candidates`、`inspect_component_terminal_axis`、`inspect_single_pin_stub`、`inspect_gap_bridge_candidates`
- Granular dry-run tools：`dry_run_merge_nodes`、`dry_run_component_class_override`、`dry_run_component_axis_flip`、`dry_run_reattach_pin`、`dry_run_gap_bridge_merge`、`dry_run_single_pin_stub_bridge`
- Validation：`validate_candidate`

当前可 apply 的 repair types：

- `merge_nodes`
- `reattach_pin`
- `gap_bridge_merge`
- `single_pin_stub_bridge`
- `component_pin_axis_flip`
- `component_class_override`

所有 repair 都先 dry-run，不直接改 topology。最终输出是 `repair_plan`，由人批准后才 apply。

## Agent Eval

单 case eval 已经由 `main_run.py` 自动完成。输出通常位于：

```text
outputs/debug_runs/<run-name>/agent_eval/agent_eval_report.md
```

最终实验 A 使用统一脚本跑完整 Agent 闭环，case 范围是 `001/003/004/005 + 101-110 + 202-204 + 301-303`：

```powershell
$env:DEEPSEEK_API_KEY="your_key"
python -B tools\experiment_a\run_experiment_a.py --backend deepseek --model deepseek-v4-pro
```

实验 B 使用同一批 case 做规则消融和参数扰动，不调用 LLM：

```powershell
python -B tools\experiment_b\run_experiment_b.py --proposal-backend yolo
```

生成最终 synthetic cases：

```powershell
python -B tools\generators\generate_cases.py --suite final
```

## DXF 导出

默认配置使用 clean DXF。它仍然只基于 topology/netlist 等结构化结果，不读取原始图片来“描图”。

导出逻辑分两档：

- **Clean schematic redraw**：对常见电路结构做规整重绘，例如 rail/branch、RC ladder、two-rail ladder、mesh-like 图。
- **Geometry-preserving fallback**：当无法可靠识别为模板结构时，保留 topology 中的几何坐标并做基础清理。

这让输出既满足“标准化矢量图”的要求，又避免把手绘抖动原样带进 DXF。

## 推荐展示案例

- `005`：元件类别修复。检测层把电源误识别为电容，Agent 根据全局“没有电源”和类别候选提出 `component_class_override`。
- `301`：主流程完整案例。包含确定性拓扑恢复、Agent 多问题修复、human approval、clean ladder DXF。
- `302`：多轮 Agent 工具调用。展示 LLM 如何围绕 axis flip、single-pin stub、gap bridge 等假设逐步调用工具。
- `303`：多分支 clean DXF。展示拓扑到规整 DXF 的重绘效果。

案例讲解稿可参考：

- `CASE_STUDY_PRESENTATION_SCRIPT.md`
- `CASE_STUDY_MATERIALS.md`

## 关键目录

```text
src/
  agent_workflow/        Agent audit/advisor/apply/eval/memory
  export/                DXF exporter and clean layout engine
  topology/              graph, node, topology, validation logic
  perception/            proposal / YOLO wrapper / wire evidence
  preprocess/            image preprocessing

tools/
  generators/                       final synthetic case generation
  experiment_a/                     full deterministic + Agent loop experiment
  experiment_b/                     rule ablation / parameter perturbation experiment
```

## 文档

- `AGENT_WORKFLOW.md`：Agent 3.9 工作流、工具族与安全边界。
- `DATA_SCHEMA.md`：主要 JSON 输出结构。
- `ISSUE_TAXONOMY.md`：审计问题类型与 repair 类型。
- `CONFIDENCE_SCHEMA.md`：打分与置信度来源。
- `GLOSSARY.md`：术语表。

## 当前边界

项目已经能完成手绘电路图到 topology/netlist/DXF 的主链路，并通过 Agent 对部分错误进行可审计修复。但它不是通用 OCR/EDA 工具，仍依赖：

- 元件检测模型的类别候选；
- terminal 规则与 evidence graph；
- 有限 repair tool family；
- 人类批准最终拓扑修改；
- 对常见电路结构的 clean redraw 模板。

系统没有让 LLM 任意改结构，而是把不确定性限制在可解释、可回放的工具闭环里。
