# Sketch2DXF 四个答辩案例素材整理

本文档用于准备答辩/PPT 的案例页。路径均相对项目根目录 `D:\code\pywork\Sketch2DXF`。

建议主讲顺序：

1. **301**：主流程完整案例，最适合作为核心讲解。
2. **005**：元件类别修复，展示 Agent 如何利用候选类别和电路语义纠错。
3. **302**：多轮 Agent 工具调用，展示“LLM 作为系统大脑”如何提出、验证、组合修复计划。
4. **303**：多分支 clean DXF，展示最终矢量化输出效果。

---

## 一、总览

| Case | 定位 | 主链路状态 | Agent 修复计划 | 最终输出 |
|---|---|---|---|---|
| `agent40_301_plan` | 主流程完整案例 | `graph_derived`，初版 `fail` | `STB2` + `AXF1` 两步修复 | ladder clean DXF |
| `agent40_005_plan` | 元件类别修复 | `graph_derived`，`pass_with_warnings` | `CLS1` 类别 override | 电源类别修正后的 DXF |
| `agent40_302_plan` | 多轮 Agent 工具调用 | `legacy fallback`，初版 `fail` | `AXF1` + `MRG1` 两步修复 | two-rail ladder clean DXF |
| `agent40_303_plan` | 多分支 clean DXF | `graph_derived`，`pass_with_warnings` | `MRG1` 合并单 pin net | 多支路 clean DXF |

四个案例都建议准备 4 类素材：

- 原始输入图：`00_input.png`
- YOLO/检测叠加图：`06_proposals.png` 或 `13_overlay.png`
- Agent 报告关键截图：`agent_repair_advisor_report.md`
- 最终 DXF：`corrected_export.dxf`

---

## 二、推荐 PPT 结构

### 第 1 页：任务定义

一句话：

> 输入普通 PNG/JPG 手绘电路图，识别元件符号和连接关系，恢复结构化拓扑，并导出可编辑 DXF。

可以放：

- `outputs/debug_runs/agent40_301_plan/00_input.png`
- `outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/corrected_export.dxf`

### 第 2 页：系统主流程

建议讲：

1. YOLO 检测元件，得到 `components / bbox / class candidates`。
2. 根据 bbox 和附近导线生成 terminal hypotheses。
3. mask 元件后提取 wire evidence，不把 Hough 当最终真相。
4. 构建 evidence graph / supported graph。
5. terminal 作为锚点推断 electrical nodes。
6. 生成 topology / netlist。
7. Agent 对不确定结果进行审计、工具调用、dry-run 修复、人类确认。
8. 根据 topology 导出 clean DXF。

建议配图：

- `outputs/debug_runs/agent40_301_plan/06_proposals.png`
- `outputs/debug_runs/agent40_301_plan/13_overlay.png`
- `outputs/debug_runs/agent40_301_plan/12_active_nodes.png`

### 第 3-6 页：301 主案例完整走读

301 是最推荐主讲的 case，因为它同时覆盖：

- YOLO 检测；
- graph-derived 主链路；
- topology 初版问题；
- Agent 多工具审计；
- 两个可 apply 修复候选；
- human approval；
- corrected topology；
- ladder DXF。

### 第 7 页：005 类别修复

005 展示“不是所有错误都是导线错误”，有些是元件语义层的候选类别问题。Agent 利用 YOLO suppressed duplicate 和“电路中没有 power source”的语义信号，提出 class override。

### 第 8 页：302 多轮 Agent 工具调用

302 展示 Agent workflow 更精彩：它不是一次规则判断，而是经历 terminal axis inspection、dry-run axis flip、single-pin inspection、merge dry-run，最后形成多步骤 repair plan。

### 第 9 页：303 clean DXF

303 展示最终输出效果。重点讲“从识别到 DXF 不是描摹原图，而是基于拓扑重新生成标准化工程图”。

---

## 三、Case 301：主流程完整案例

### 1. 案例定位

**推荐作为主讲案例。**

301 是一个多级 RC ladder/RC network，图中有：

- 1 个电源：`V1`
- 4 个电阻：`R1/R2/R3/R4`
- 3 个电容：`C1/C2/C3`
- 顶部串联链路
- 多个竖向支路
- 底部公共参考 net

这个案例比 005 更复杂，也比 302 更适合讲“主链路”，因为它的 node source 是 `graph_derived`，不会让听众纠结 fallback。

### 2. 素材路径

基础图：

- 原始输入图：`outputs/debug_runs/agent40_301_plan/00_input.png`
- YOLO 检测图：`outputs/debug_runs/agent40_301_plan/06_proposals.png`
- 导线证据图：`outputs/debug_runs/agent40_301_plan/09_wire_segments.png`
- active nodes 图：`outputs/debug_runs/agent40_301_plan/12_active_nodes.png`
- overlay 总览图：`outputs/debug_runs/agent40_301_plan/13_overlay.png`

结构化输出：

- 初版 topology：`outputs/debug_runs/agent40_301_plan/topology.json`
- 初版 netlist：`outputs/debug_runs/agent40_301_plan/netlist.json`
- case summary：`outputs/debug_runs/agent40_301_plan/case_summary.json`
- audit report：`outputs/debug_runs/agent40_301_plan/agent_audit_report.md`

Agent 输出：

- Agent report：`outputs/debug_runs/agent40_301_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
- Human review dossier：`outputs/debug_runs/agent40_301_plan/agent_deepseek_toolfamily/agent_human_review_dossier.md`
- Approval request：`outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/approval_request.md`
- Approval decision：`outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/approval_decision.json`
- Replay report：`outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/repair_replay_report.md`

最终输出：

- corrected topology：`outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/corrected_topology.json`
- corrected netlist：`outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/corrected_netlist.json`
- corrected DXF：`outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/corrected_export.dxf`

### 3. 初版问题

`case_summary.json` 中 301 的初版结果：

- `quality_label`: `fail`
- `selected_node_source`: `graph_derived`
- `fallback_used`: `false`
- `component_count`: `8`
- `pin_count`: `16`
- `net_count`: `6`
- `risk_counts`: `error=2, warning=5, info=3`
- `repair_candidate_count`: `12`

这说明：主链路不是完全失败，而是已经恢复出大部分拓扑，但仍存在局部错误，需要 Agent 做审计和修复。

PPT 可以这样讲：

> 主链路已经能把 8 个元件、16 个 pin 和 6 个 electrical nets 恢复出来，但在局部 terminal attachment 和 single-pin net 上仍有不确定性。这正是 Agent 层介入的边界：不是重新识图，而是审计、验证和修复候选。

### 4. Agent 两个修复候选

301 的 Agent 形成了两步 repair plan：

| Step | Candidate | Type | 目标 | 改善指标 |
|---|---|---|---|---|
| S1 | `STB2` | `single_pin_stub_bridge` | 合并 `N4` 与 `N7` 附近的 stub 证据 | `single_pin_net_count` |
| S2 | `AXF1` | `component_pin_axis_flip` | 将 `cp_1` 从 horizontal fallback 改为 vertical attachment | `unmatched_pin_count` |

`STB2` 的 dry-run 理由：

- source node `N4` 只有一个 pin：`cp_4_p1`
- target node `N7` 有 2 个 pins
- bbox gap 约 `16 px`
- source 和 target bbox 轴向对齐
- `cp_4_p1` terminal attachment score 约 `0.993`

`AXF1` 的 dry-run 理由：

- 当前 axis 是 horizontal fallback
- 替代 axis 是 vertical
- 当前 attached pins：`0`
- alternate attached pins：`2`
- attachment score sum：`0.0 -> 1.69`
- unmatched pin count：`2 -> 0`

PPT 可以这样讲：

> Agent 不是直接修改拓扑，而是先调用工具验证两个假设：一个是 single-pin stub 是否应接回主图，另一个是元件 terminal 方向是否选错。两个候选都通过 dry-run 验证后，才生成 human approval request。

### 5. Human approval 后 corrected topology

人类批准记录：

- decision：`accept`
- candidate_ids：`["STB2", "AXF1"]`
- approved_by：`Lzk`
- notes：`approved tool-family repair plan for 301`

修复后 corrected topology 里，主要 nets 为：

- `N1`: 底部公共 net，连接 `C1.2/C2.2/C3.2/V1.2/R1.2`
- `N5`: `R3.1` 与 `V1.1`
- `N4`: `R3.2/C1.1/R4.1`
- `N2`: `R4.2/C2.1/R2.1`
- `N6`: `R2.2/C3.1/R1.1`

这对应一个清晰的 RC ladder：

```text
V1 -> R3 -> R4 -> R2 -> output/load
        |     |     |
       C1    C2    C3/R1
        |     |     |
       common bottom net N1
```

### 6. 最终 DXF

最终 DXF 路径：

- `outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/corrected_export.dxf`

讲法：

> DXF 不是简单描摹原图像素，而是根据 corrected topology 识别 RC ladder 结构后重新绘制。这样输出更像标准工程图：元件对齐、导线水平/垂直、底部公共 net 明确，便于后续编辑和复用。

### 7. 301 推荐截图组合

PPT 一页放四图：

1. 左上：`outputs/debug_runs/agent40_301_plan/00_input.png`
2. 右上：`outputs/debug_runs/agent40_301_plan/06_proposals.png`
3. 左下：`outputs/debug_runs/agent40_301_plan/13_overlay.png`
4. 右下：`outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/corrected_export.dxf`

额外一页讲 Agent：

- `outputs/debug_runs/agent40_301_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
- `outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/approval_request.md`
- `outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/repair_replay_report.md`

---

## 四、Case 005：元件类别修复

### 1. 案例定位

005 是一个小电路，但非常适合讲“Agent 不是只能修导线，也能修元件语义”。

主链路原本把左侧元件识别成 `capacitor.unpolarized`，但 YOLO 候选里还保留了一个被 NMS/duplicate suppression 压掉的 `power_source` 备选。Agent 结合“当前电路没有电源”的语义，提出 class override。

### 2. 素材路径

基础图：

- 原始输入图：`outputs/debug_runs/agent40_005_plan/00_input.png`
- YOLO 检测图：`outputs/debug_runs/agent40_005_plan/06_proposals.png`
- active nodes 图：`outputs/debug_runs/agent40_005_plan/12_active_nodes.png`
- overlay 总览图：`outputs/debug_runs/agent40_005_plan/13_overlay.png`

Agent 输出：

- Agent report：`outputs/debug_runs/agent40_005_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
- Human review dossier：`outputs/debug_runs/agent40_005_plan/agent_deepseek_toolfamily/agent_human_review_dossier.md`
- Approval request：`outputs/debug_runs/agent40_005_plan/repair_deepseek_toolfamily/approval_request.md`
- Replay report：`outputs/debug_runs/agent40_005_plan/repair_deepseek_toolfamily/repair_replay_report.md`

最终输出：

- corrected topology：`outputs/debug_runs/agent40_005_plan/repair_deepseek_toolfamily/corrected_topology.json`
- corrected netlist：`outputs/debug_runs/agent40_005_plan/repair_deepseek_toolfamily/corrected_netlist.json`
- corrected DXF：`outputs/debug_runs/agent40_005_plan/repair_deepseek_toolfamily/corrected_export.dxf`

### 3. 初版状态

`case_summary.json`：

- `quality_label`: `pass_with_warnings`
- `selected_node_source`: `graph_derived`
- `fallback_used`: `false`
- `component_count`: `2`
- `pin_count`: `4`
- `net_count`: `2`
- `risk_counts`: `error=0, warning=1, info=1`
- `repair_candidate_count`: `2`

也就是说，005 的拓扑连通性本身问题不大，但元件类别语义不够合理。

### 4. Agent 修复候选

repair plan：

| Step | Candidate | Type | 目标 | 改善指标 |
|---|---|---|---|---|
| S1 | `CLS1` | `component_class_override` | 将 `cp_1` 从 `capacitor.unpolarized` 改为 `power_source` | `power_source_count` |

`CLS1` 的 dry-run 理由：

- 当前 class：`capacitor.unpolarized`
- 替代 class：`power_source`
- 两者都是 2-pin 元件，terminal 数量兼容
- 当前 selected score：`0.684`
- alternative score：`0.317`
- alternative 与当前 bbox IoU：`0.963`
- dry-run 不改变 terminal geometry 或 node topology
- 当前 recovered topology 中没有 power-source component

PPT 可以这样讲：

> 这里不是让 LLM 看图“猜电源”，而是把 YOLO 的多类别候选、bbox overlap、当前拓扑是否存在电源等事实提供给 Agent。Agent 选择调用 class override dry-run，确认不会破坏拓扑后，生成需要人类确认的修复。

### 5. 修复后效果

human approval 后，`corrected_topology.json` 中 `cp_1` 变为：

- `class_name`: `power_source`
- `refdes`: `V1`
- `class_override.candidate_id`: `CLS1`
- `source`: `human_approved_agent_repair`

最终电路：

- `V1` 与 `R1` 形成一个两 net 回路
- `N1`: `V1.2` 与 `R1.2`
- `N2`: `V1.1` 与 `R1.1`

### 6. 005 推荐截图组合

1. `outputs/debug_runs/agent40_005_plan/00_input.png`
2. `outputs/debug_runs/agent40_005_plan/06_proposals.png`
3. `outputs/debug_runs/agent40_005_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
4. `outputs/debug_runs/agent40_005_plan/repair_deepseek_toolfamily/corrected_export.dxf`

建议这页标题：

> Case 005：当检测类别不确定时，Agent 利用电路语义修正元件类型

---

## 五、Case 302：多轮 Agent 工具调用

### 1. 案例定位

302 适合作为“Agent 真的在循环调用工具”的展示案例。

这个 case 的主链路初版使用了 legacy fallback：

- `selected_node_source`: `legacy`
- `fallback_used`: `true`
- `quality_label`: `fail`

这点不适合作为主流程亮点，但非常适合讲 Agent 层如何处理复杂不确定问题。

### 2. 素材路径

基础图：

- 原始输入图：`outputs/debug_runs/agent40_302_plan/00_input.png`
- YOLO 检测图：`outputs/debug_runs/agent40_302_plan/06_proposals.png`
- active nodes 图：`outputs/debug_runs/agent40_302_plan/12_active_nodes.png`
- overlay 总览图：`outputs/debug_runs/agent40_302_plan/13_overlay.png`

Agent 输出：

- Agent report：`outputs/debug_runs/agent40_302_plan/agent_deepseek_ab_fix/agent_repair_advisor_report.md`
- Human review dossier：`outputs/debug_runs/agent40_302_plan/agent_deepseek_ab_fix/agent_human_review_dossier.md`
- Approval request：`outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/approval_request.md`
- Replay report：`outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/repair_replay_report.md`

最终输出：

- corrected topology：`outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/corrected_topology.json`
- corrected netlist：`outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/corrected_netlist.json`
- corrected DXF：`outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/corrected_export.dxf`

### 3. 初版问题

`case_summary.json`：

- `quality_label`: `fail`
- `selected_node_source`: `legacy`
- `fallback_used`: `true`
- `component_count`: `8`
- `pin_count`: `16`
- `net_count`: `7`
- `risk_counts`: `error=2, warning=6, info=3`
- `repair_candidate_count`: `15`

主要问题：

1. `cp_3` 两个 pin 未匹配，疑似 terminal axis 错误。
2. `N8` 与 `N4` 是两个 single-pin nets，疑似应该合并。

### 4. Agent 多轮工具调用

302 的 repair plan：

| Step | Candidate | Type | 目标 | 改善指标 |
|---|---|---|---|---|
| S1 | `AXF1` | `component_pin_axis_flip` | 将 `cp_3` terminal axis 从 horizontal 改为 vertical | `unmatched_pin_count` |
| S2 | `MRG1` | `merge_nodes` | 合并 `N8` 与 `N4` | `single_pin_net_count` |

关键工具调用链：

1. `inspect_component_terminal_axis`
   - 检查 `cp_3` 当前 terminal 方向是否合理。
2. `dry_run_component_axis_flip`
   - 验证将 `cp_3` 改为 vertical 是否能重新接入已有 nodes。
3. `inspect_single_pin_nets`
   - 查看 single-pin nets 的分布。
4. `inspect_single_pin_stub`
   - 分析 `N8/N4` 是否像孤立 stub。
5. `dry_run_single_pin_stub_bridge`
   - 测试 stub bridge。
6. `dry_run_merge_nodes`
   - 在 stub bridge 不足时，测试直接合并两个 single-pin nodes。

PPT 可以这样讲：

> 302 的亮点是 Agent 不是一次性给建议，而是围绕两个假设循环调用工具：先验证元件方向，再验证 single-pin net 是否可合并。每一步工具都是 dry-run，不直接改 topology。最终输出 repair plan，再由 human approval 执行。

### 5. 两个修复候选

`AXF1`：

- repair type：`component_pin_axis_flip`
- target pins：`cp_3_p1`, `cp_3_p2`
- target nodes：`N1`, `N2`
- ranking score：`0.931`
- 改善：`unmatched_pin_count`
- 逻辑：`cp_3` 原来是 horizontal fallback，改成 vertical 后两个 pin 可以接回上下两条 net。

`MRG1`：

- repair type：`merge_nodes`
- target nodes：`N8`, `N4`
- target pins：`cp_7_p1`, `cp_8_p1`
- ranking score：`0.887`
- 改善：`single_pin_net_count`
- 逻辑：两个 single-pin nets 合并为一个 2-pin net，避免孤立端子。

### 6. 修复后 corrected topology

修复后主要 nets：

- `N8`: `R6.1` 与 `V1.1`
- `N6`: `R1.1/R5.1/R6.2`
- `N1`: `C1.1/R5.2/R3.2`
- `N2`: `R2.2/C1.2/R3.1`
- `N7`: `R1.2/R2.1/R4.2`
- `N5`: `R4.1/V1.2`

最终结构更接近 two-rail ladder：

- 左侧电源 `V1`
- 上下两条链路
- 中间和右侧有跨接/支路元件

### 7. 302 推荐截图组合

1. `outputs/debug_runs/agent40_302_plan/00_input.png`
2. `outputs/debug_runs/agent40_302_plan/13_overlay.png`
3. `outputs/debug_runs/agent40_302_plan/agent_deepseek_ab_fix/agent_repair_advisor_report.md`
4. `outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/corrected_export.dxf`

建议这页标题：

> Case 302：多轮 Agent 工具调用生成两步修复计划

注意答辩措辞：

> 这个 case 初版使用了 legacy fallback，因此我不把它作为主链路成功案例，而是作为 Agent 层处理复杂不确定问题的展示。

这样讲更诚实，也更稳。

---

## 六、Case 303：多分支 clean DXF

### 1. 案例定位

303 适合展示最终 DXF 效果，尤其是多分支结构被规整成 clean schematic。

主链路状态：

- `quality_label`: `pass_with_warnings`
- `selected_node_source`: `graph_derived`
- `fallback_used`: `false`
- `component_count`: `7`
- `pin_count`: `14`
- `net_count`: `6`
- `risk_counts`: `error=0, warning=4, info=2`

### 2. 素材路径

基础图：

- 原始输入图：`outputs/debug_runs/agent40_303_plan/00_input.png`
- YOLO 检测图：`outputs/debug_runs/agent40_303_plan/06_proposals.png`
- active nodes 图：`outputs/debug_runs/agent40_303_plan/12_active_nodes.png`
- overlay 总览图：`outputs/debug_runs/agent40_303_plan/13_overlay.png`

Agent 输出：

- Agent report：`outputs/debug_runs/agent40_303_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
- Human review dossier：`outputs/debug_runs/agent40_303_plan/agent_deepseek_toolfamily/agent_human_review_dossier.md`
- Approval request：`outputs/debug_runs/agent40_303_plan/repair_deepseek_toolfamily/approval_request.md`
- Replay report：`outputs/debug_runs/agent40_303_plan/repair_deepseek_toolfamily/repair_replay_report.md`

最终输出：

- corrected topology：`outputs/debug_runs/agent40_303_plan/repair_deepseek_toolfamily/corrected_topology.json`
- corrected netlist：`outputs/debug_runs/agent40_303_plan/repair_deepseek_toolfamily/corrected_netlist.json`
- corrected DXF：`outputs/debug_runs/agent40_303_plan/repair_deepseek_toolfamily/corrected_export.dxf`

### 3. Agent 修复候选

repair plan：

| Step | Candidate | Type | 目标 | 改善指标 |
|---|---|---|---|---|
| S1 | `MRG1` | `merge_nodes` | 合并 `N8` 与 `N3` | `single_pin_net_count` |

`MRG1` 的 dry-run 理由：

- source node 是 single-pin net
- target node 有 3 个 pins
- bbox gap 为 `0.0 px`
- axis-aligned
- single-pin net count：`1 -> 0`

修复后主要 nets：

- `N1`: 底部公共 net，连接 `R2.2/C1.2/C2.2/V1.2`
- `N2`: 左侧上方 net，连接 `R4.1/V1.1`
- `N8`: 顶部/中部主 net，连接 `R1.1/R3.1/R4.2/C2.1`
- `N5`: `R1.2/C1.1`
- `N4`: `R2.1/R3.2`

### 4. DXF 展示重点

303 的 corrected DXF 适合讲：

> 识别阶段保留了拓扑关系，导出阶段不追求复刻手绘抖动，而是把多分支网络规整为可编辑、可复用的标准化 DXF。

这个 case 可以作为最后展示页：输入比较复杂，输出比较干净。

### 5. 303 推荐截图组合

1. `outputs/debug_runs/agent40_303_plan/00_input.png`
2. `outputs/debug_runs/agent40_303_plan/13_overlay.png`
3. `outputs/debug_runs/agent40_303_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
4. `outputs/debug_runs/agent40_303_plan/repair_deepseek_toolfamily/corrected_export.dxf`

建议这页标题：

> Case 303：多分支电路的拓扑重建与 clean DXF 导出

---

## 七、四个案例怎么串成答辩故事

### 推荐主线

主讲 301：

1. 展示原图。
2. 展示 YOLO 检测结果。
3. 展示 overlay：导线、节点、pin 的中间结果。
4. 说明初版 topology 已恢复大部分结构，但存在局部错误。
5. 展示 Agent report：发现 single-pin stub 和 axis fallback 问题。
6. 展示两步 repair plan：`STB2 + AXF1`。
7. 展示 human approval。
8. 展示 corrected topology/netlist。
9. 展示 ladder clean DXF。

辅助讲 005：

> 检测模型不是只输出一个死结果，而是保留 class candidates。Agent 可以利用这些候选和电路语义做元件类别修复。

辅助讲 302：

> 对复杂问题，Agent 不是只写报告，而是多轮调用工具：inspect -> dry-run -> inspect -> dry-run -> repair plan。

辅助讲 303：

> 最终 DXF 不是像素描摹，而是 topology-driven clean redraw。

### 推荐一句话总结

> 本项目的核心不是把手绘线条完美描出来，而是从图像中恢复电路拓扑；确定性主链路负责生成可解释的结构化候选，Agent 负责审计不确定性、调用工具验证修复假设，并在人类确认后修复 topology，最后导出标准化 DXF。

---

## 八、PPT 可直接使用的短句

### 介绍主链路

> 我们把 wire extraction 降级为 evidence，而不是把 Hough 结果当作最终连接真相。真正决定连接关系的是 terminal-anchored topology inference。

### 介绍 Agent

> Agent 不直接改图，也不直接凭空判断拓扑，而是在受控工具集合内循环调用 inspect/dry-run 工具，形成可验证的 repair plan。

### 介绍 human approval

> 所有 topology mutation 都通过 dry-run 生成候选，再由 human approval 显式确认，避免 LLM 直接修改工程结果。

### 介绍 DXF

> DXF 导出阶段读取 corrected topology，而不是读原始图片；因此输出是可编辑的标准化工程图，而不是一张描边图。

### 介绍局限性

> 当前系统主要覆盖电阻、电容、电源等基础 2-pin 元件，以及串联、并联、RC ladder 等常见结构。更复杂的跨线、总线、多端器件和专业符号仍需要扩展检测类别、拓扑规则和 Agent 工具。

---

## 九、答辩时建议避免的说法

不建议说：

> 系统已经可以处理任意手绘电路图。

建议说：

> 系统已经打通了从手绘位图到结构化 topology/netlist/DXF 的完整闭环，并在多类典型电路上验证了确定性主链路和 Agent 修复闭环。当前重点覆盖基础 2-pin 元件和常见电路拓扑。

不建议说：

> Agent 直接识别电路。

建议说：

> Agent 不替代底层视觉识别，而是利用主链路输出的结构化证据，通过工具调用进行审计、dry-run 修复和解释。

不建议说：

> DXF 是照着原图画出来的。

建议说：

> DXF 是从 corrected topology 重新布局生成的，因此更标准、更可编辑。

---

## 十、最终展示素材清单

### 301 主讲素材

- `outputs/debug_runs/agent40_301_plan/00_input.png`
- `outputs/debug_runs/agent40_301_plan/06_proposals.png`
- `outputs/debug_runs/agent40_301_plan/13_overlay.png`
- `outputs/debug_runs/agent40_301_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
- `outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/approval_request.md`
- `outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/repair_replay_report.md`
- `outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/corrected_topology.json`
- `outputs/debug_runs/agent40_301_plan/repair_deepseek_toolfamily/corrected_export.dxf`

### 005 辅助素材

- `outputs/debug_runs/agent40_005_plan/00_input.png`
- `outputs/debug_runs/agent40_005_plan/06_proposals.png`
- `outputs/debug_runs/agent40_005_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
- `outputs/debug_runs/agent40_005_plan/repair_deepseek_toolfamily/corrected_topology.json`
- `outputs/debug_runs/agent40_005_plan/repair_deepseek_toolfamily/corrected_export.dxf`

### 302 辅助素材

- `outputs/debug_runs/agent40_302_plan/00_input.png`
- `outputs/debug_runs/agent40_302_plan/13_overlay.png`
- `outputs/debug_runs/agent40_302_plan/agent_deepseek_ab_fix/agent_repair_advisor_report.md`
- `outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/approval_request.md`
- `outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/corrected_topology.json`
- `outputs/debug_runs/agent40_302_plan/repair_deepseek_ab_fix/corrected_export.dxf`

### 303 辅助素材

- `outputs/debug_runs/agent40_303_plan/00_input.png`
- `outputs/debug_runs/agent40_303_plan/13_overlay.png`
- `outputs/debug_runs/agent40_303_plan/agent_deepseek_toolfamily/agent_repair_advisor_report.md`
- `outputs/debug_runs/agent40_303_plan/repair_deepseek_toolfamily/corrected_topology.json`
- `outputs/debug_runs/agent40_303_plan/repair_deepseek_toolfamily/corrected_export.dxf`

