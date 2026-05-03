# Sketch2DXF 术语表

## Core

### component
电路元件，例如电阻、电容、电源。

### proposal
元件候选框，表示“这里可能有一个元件”。当前支持 `traditional` 和 `yolo` 两种 proposal backend。

### pin / terminal
元件端子。当前端子主要由元件 bbox、类别规则和周围导线位置推断。

### wire evidence
导线证据。它不是最终连接真相，只是从 residual 图像中提取出的线段、端点、交点等候选证据。

### segment
向量化导线线段，通常是水平或垂直线段。

### endpoint
导线端点。

### junction
导线交点或拐角候选。

### electrical node
电气节点。一组应被认为电气连通的导线证据和端子。

### net
网。当前项目里通常与一个 electrical node 一一对应。

### netlist
结构化网表，描述每个元件 pin 接到哪个 net。

## Graph 2.0

### evidence_graph
由 wire evidence 构成的图。包含 vertices、edges、raw_components 和 bridge_candidates。

### raw_component
evidence graph 中几何上连通的一组导线证据，还没有经过 terminal support 判断。

### bridge_candidate
两个 raw_components 之间可能需要桥接的候选关系，例如短 gap 或 point-to-segment 关系。

### terminal_attachment
pin 到 evidence 的候选附着关系。回答“这个 pin 沿自己的方向最可能接到哪条 segment / 哪个 raw_component”。

### supported_graph
在 evidence_graph 上叠加 terminal attachment 后得到的支持图。它标记哪些证据被 terminal 支持、哪些应被丢弃、哪些是中继证据。

### relay_supported
一种特殊支持状态。raw component 自己没有 terminal 直接支持，但它连接了多个 terminal-supported components，因此作为中继导线保留。

### graph-derived node
由 supported_graph 生成的正式 electrical node。2.0 默认使用它作为主链路 node 来源。

### legacy fallback
旧版 `node_builder + matcher` 路径。当前仍保留，用于 graph-derived nodes 出错时回退。

### audit_inputs
面向人工和 Agent 的统一审计输入，整合 topology、node selection、supported graph、risk flags 等关键信息。

## Output

### overlay
把识别结果画回原图的调试可视化。

### DXF
工程图格式。当前用于输出可打开、可检查的第一版电路图结果。

### validation
规则一致性检查，主要检查 floating pins、missing connections、isolated nets 等明显问题。

### risk flag
审计风险标记，例如 `low_confidence_match`、`unsupported_evidence`、`relay_supported_node`。
