# Confidence Schema

本文档说明 Sketch2DXF 当前各类分数的语义。核心原则是：

```text
分数可以用于排序、审计和解释，但不要把所有分数都当成概率。
```

## 1. model_score

来源：元件检测模型。

含义：模型对元件检测框及类别的置信度。

用途：

- 元件 proposal 过滤。
- 重叠框去重时优先保留高分框。

限制：

- 只描述检测模型输出。
- 不直接说明端子连接是否正确。

## 2. evidence_score

来源：导线证据提取。

含义：导线候选的结构证据强弱。

常见来源：

- 长线段。
- 桥接候选。
- 共线支撑。
- 未分类但保留的线段。

用途：

- 解释导线证据强弱。
- 辅助节点置信度。
- 为审计提供证据背景。

限制：

- 不是导线一定正确的概率。
- 不应单独决定 topology。

## 3. pin_confidence

来源：端子定位。

含义：元件端子方向判断的可信度。

用途：

- 判断端子是水平布局还是竖直布局。
- 解释端子位置是否主要来自周围导线证据。

限制：

- 只描述端子方向。
- 不等于端子最终连接置信度。

## 4. attachment_score

来源：terminal attachment。

含义：某个 terminal 与某个 evidence 的局部附着质量。

主要考虑：

- evidence 是否在 terminal 朝向区域内。
- lateral offset 是否小。
- forward distance 是否合理。
- evidence 方向是否符合 terminal 朝向。

用途：

- terminal 候选排序。
- supported graph 的 terminal support 判断。
- 发现 ambiguous terminal attachment。

限制：

- 是局部 terminal-evidence 分数。
- 不能直接等同最终 pin-node 连接正确率。

## 5. match_confidence

来源：component-node matcher。

含义：pin 到最终 node 的匹配可信度。

主要考虑：

- pin 到 evidence / node geometry 的距离。
- 方向对齐程度。

用途：

- pin-node match 排序。
- audit risk 标记。
- repair candidate 触发。

阈值：

```text
< 0.6   low confidence
< 0.75  weak confidence
```

限制：

- 这是规则分数，不是概率。
- 低于阈值不代表一定错误，只表示需要复核。

## 6. node_confidence

来源：node builder 或 graph node selector。

含义：节点来源和支持状态的摘要置信度。

用途：

- 审计展示。
- 区分 terminal-supported、relay-supported 等节点。

限制：

- 不直接决定连接关系。
- graph-derived node 的分数是状态映射，不是统计概率。

## 7. consistency_score

来源：topology consistency rules。

含义：当前拓扑是否触发明显规则问题。

用途：

- regression harness。
- fallback guard。
- audit summary。

限制：

- `1.0` 不等于拓扑绝对正确。
- 它只表示当前规则未发现明显错误。

## 8. review_status

来源：case summary。

含义：单个样例的整体复核状态。

取值：

- `pass`
- `needs_review`
- `fail`

用途：

- 人工排查入口。
- Agent 审计入口。
- regression report 汇总。

限制：

- `needs_review` 不等于失败。
- 它表示系统有 warning 或候选修复项，值得人工或 Agent 看一眼。
