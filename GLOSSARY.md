# Sketch2DXF 术语表

## Core

### component

电路元件，例如电阻、电容、电源。

### proposal

元件候选框，表示“这里可能有一个元件”。当前支持 traditional 和 YOLO proposal backend。

### class_candidates

元件检测模型给出的类别候选列表。Agent 可以用它判断是否存在类别修复可能，例如 capacitor 与 power_source 的混淆。

### pin / terminal

元件端子。当前端子主要由 bbox、类别规则、axis 和周围导线 evidence 推断。

### terminal axis

两 pin 元件的端子方向，例如 horizontal 或 vertical。axis 错误可能导致两个 pin 都连不上正确 node。

### wire evidence

导线证据。它不是最终连接真相，只是从 residual 图像中提取的线段、端点、交点等候选证据。

### segment

向量化导线线段，通常是水平或垂直线段。

### endpoint

导线端点。

### junction

导线交点、T 型连接或拐角候选。

### electrical node

电气节点。一组应被认为电气连通的导线证据和 terminal。

### net

网。当前项目中通常与一个 electrical node 一一对应。

### netlist

结构化网表，描述每个元件 pin 接到哪个 net。

## Evidence / Graph Topology

### evidence_graph

由 wire evidence 构成的 raw graph，包含 raw components、segments、points 和 bridge candidates。

### raw_component

evidence graph 中几何上连通的一组导线证据，尚未经过 terminal support 判断。

### bridge_candidate

两个 raw/support components 之间可能需要桥接的候选关系，例如短 gap、point-to-segment 连接。

### terminal_attachment

pin 到 evidence 的候选附着关系。它回答：“这个 pin 沿自身方向最可能接到哪条 segment / 哪个 raw_component？”

### supported_graph

在 evidence_graph 上叠加 terminal attachment 后得到的支持图。它标记哪些证据被 terminal 支持，哪些是 relay support，哪些应被丢弃。

### relay_supported

raw component 自身没有 terminal 直接支持，但连接了多个 terminal-supported components，因此作为中继导线保留。

### graph-derived node

由 supported_graph 生成的 electrical node。

### legacy fallback

旧版 node_builder + matcher 路径。当 graph-derived nodes 质量不足时，系统可以回退到 legacy nodes。

### node_selection

记录系统最终选择 graph-derived 还是 legacy result，以及 fallback reason。

## Agent

### audit

对 topology/netlist 的一致性检查，生成 risk flags、repair candidates 和 case summary。

### Agent advisor

LLM 驱动的工具调用工作流。它读取结构化 artifacts，提出假设、调用工具、生成 repair_plan。

### inspect tool

只观察不修改的工具，例如 `inspect_component_terminal_axis`、`inspect_single_pin_stub`。

### dry-run tool

生成候选但不修改 topology 的工具，例如 `dry_run_merge_nodes`、`dry_run_component_axis_flip`。

### repair_plan

Agent reviewer 生成的修复计划，包含一个或多个候选步骤及依赖关系。它需要 human approval。

### human approval

人工批准 repair_plan 或 candidate 后，apply 层才会真正生成 corrected topology/netlist/DXF。

### replay report

记录 repair apply 过程和 before/after 指标的报告。

### failure memory

历史失败模式记忆，用于给 Agent planner 提供经验提示，不直接修改 topology。

## Output

### overlay

把检测、wire evidence、pin、node 等结果画回原图的调试可视化。

### DXF

标准化矢量图输出。当前 clean 模式会根据 topology 重绘常见电路结构；若无法可靠识别结构，则回退到几何保持式导出。

### validation

规则一致性检查，主要检查 unmatched pins、single-pin nets、missing references、export failure 等问题。

### risk flag

审计风险标记，例如 `low_confidence_match`、`unsupported_evidence`、`fallback_used`。
