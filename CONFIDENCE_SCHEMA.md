# Confidence Schema

本文档说明 Sketch2DXF 当前各类分数的语义。核心原则是：

```text
分数可以用于排序、审计和解释，但不要把所有分数都当成统计概率。
```

## 1. model_score

来源：元件检测模型。

含义：模型对元件检测框和类别的置信度。

用途：

- proposal 过滤；
- 重叠框去重；
- Agent 检查 class_candidates。

限制：

- 只描述检测模型输出；
- 不直接说明 terminal 或拓扑连接正确。

## 2. evidence_score

来源：导线 evidence extraction。

含义：导线候选的结构证据强弱。

常见来源：

- 长线段；
- Hough/合并线段；
- 共线支撑；
- gap bridge 候选。

限制：

- 不是“这条导线一定正确”的概率；
- 不应单独决定 topology。

## 3. pin_confidence

来源：terminal 生成逻辑。

含义：元件 terminal 方向和位置判断的可信度。

用途：

- 判断 horizontal/vertical axis；
- 解释 terminal 是否主要来自周围导线证据还是 bbox fallback。

限制：

- 不等于 pin-node 最终连接置信度。

## 4. attachment_score

来源：terminal attachment。

含义：某个 terminal 与某条 evidence 的局部附着质量。

主要考虑：

- evidence 是否在 terminal 朝向区域内；
- lateral offset 是否小；
- forward distance 是否合理；
- evidence 方向是否匹配 terminal 朝向。

用途：

- terminal attachment 排序；
- supported graph 的 terminal support；
- 发现 ambiguous attachment。

限制：

- 是局部分数；
- 不能直接等同最终连接正确率。

## 5. match_confidence

来源：component-node matcher。

含义：pin 到最终 node 的匹配可信度。

主要考虑：

- pin 到 evidence/node 的距离；
- 方向对齐；
- attachment 或 node support；
- evidence/node 质量。

阈值：

```text
< 0.60  low confidence
< 0.75  weak confidence
```

限制：

- 这是规则分数，不是概率；
- 低于阈值不代表一定错误，只代表需要复核。

## 6. node_confidence

来源：node builder 或 graph node selector。

含义：节点来源和支持状态的摘要置信度。

用途：

- 区分 terminal-supported、relay-supported、discarded node；
- audit 展示。

限制：

- 不直接决定连接关系；
- graph-derived node 分数是状态映射，不是统计概率。

## 7. consistency_score

来源：topology consistency rules。

含义：当前拓扑是否触发明显规则问题。

用途：

- regression harness；
- fallback guard；
- audit summary。

限制：

- `1.0` 不等于拓扑绝对正确；
- 只表示当前规则没有发现明显错误。

## 8. review_status

来源：case summary。

取值：

- `pass`
- `needs_review`
- `fail`

用途：

- 人工排查入口；
- Agent 审计入口；
- regression 汇总。

限制：

- `needs_review` 不等于失败；
- 它表示系统有 warning 或候选修复项。

## 9. ranking_score

来源：Agent dry-run candidate ranking。

含义：某个 repair candidate 在同类候选中的排序分数。

通常综合：

- 是否减少 error；
- 是否减少 single-pin/unmatched 等问题；
- 是否引入新风险；
- 是否保留 component/pin/net 一致性；
- 工具特定的几何或语义证据。

限制：

- 只用于候选排序；
- 不是 LLM 置信度，也不是修复正确概率。

## 10. validation_result

来源：dry-run validator。

常见取值：

- `viable`
- `rejected`
- `no_candidate`
- `needs_review`

含义：候选在不修改原始 topology 的情况下通过了哪些一致性检查。

## 11. recommendation

来源：dry-run validation/ranking 和 reviewer 综合判断。

常见取值：

- `accept_for_human_review`
- `reject`
- `review_only`
- `defer`

它不是自动执行指令。真正执行必须经过 human approval。

## 12. repair_plan

来源：Agent reviewer。

含义：LLM 根据工具结果组织出的候选执行计划。

关键字段：

- `repair_plan_id`
- `status`
- `steps`
- `depends_on`

限制：

- `repair_plan` 是建议，不是自动修改；
- apply 前必须有人批准。
