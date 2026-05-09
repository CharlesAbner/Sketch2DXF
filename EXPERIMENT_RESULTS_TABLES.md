# Experiment Results Tables

本文档整理实验 A 和实验 B 的 PPT 可用统计表。建议答辩正文只放“简表”，详细 case 表可作为备份页或答辩问答材料。

## 表 1：实验总体设置

| 实验 | 目的 | Case 范围 | 是否调用 LLM | 输出类型 |
| --- | --- | --- | --- | --- |
| 实验 A：Agent 闭环实验 | 验证确定性主链路 + Agent advisor + human-approved repair + eval 是否能完整闭环 | 20 张：001/003/004/005 + 101-110 + 202-204 + 301-303 | 是，DeepSeek V4 Pro | advisor report、repair replay、corrected DXF、eval report |
| 实验 B：规则消融与参数扰动 | 分析确定性规则的必要性、稳定性和敏感边界 | 同一批 20 张 | 否 | rule ablation / parameter perturbation matrix |

## 表 2：实验 A 总体结果

| 指标 | 结果 |
| --- | ---: |
| 总 case 数 | 20 |
| 成功完成完整流程 | 20 |
| 脚本级失败 | 0 |
| Agent backend | DeepSeek V4 Pro |
| 生成 repair plan 的 case | 10 |
| 实际 applied 的 case | 8 |
| unsupported apply type | 3 |
| 未执行 apply / review only | 9 |

## 表 3：实验 A 的 eval 状态分布

| Eval 状态 | 数量 | 含义 |
| --- | ---: | --- |
| pass | 4 | 修复/输出通过评估 |
| pass_with_warnings | 6 | 主体可用，但仍有警告或人工复查项 |
| needs_review | 5 | Agent 没有贸然修改，建议人工检查 |
| fail | 5 | 自动 apply 或确定性指标判定存在明显风险 |

## 表 4：实验 A 的 Agent 决策分布

| Agent final decision | 数量 | 解释 |
| --- | ---: | --- |
| repair_candidate_ready_for_human_review | 7 | Agent 找到可 dry-run 的修复候选，等待人工批准 |
| review_only_issue_for_human_review | 9 | Agent 发现问题但不建议自动修复，只输出审计意见 |
| no_candidate_found | 2 | 没找到可行动修复候选 |
| needs_more_evidence | 1 | 证据不足，需要更多人工或视觉检查 |
| no_action | 1 | 无需修复 |

## 表 5：实验 A 中 applied cases 的语义评估

| Case | Applied candidate | Eval 状态 | LLM semantic verdict | 说明 |
| --- | --- | --- | --- | --- |
| 005 | CLS1 | fail | warning | 元件类别从 capacitor 改为 power_source，语义合理但分类置信度较低，需要人工确认 |
| 104 | MRG1 | pass | pass | merge_nodes 修复成功 |
| 107 | STB2 | pass | pass | single-pin stub bridge 修复成功 |
| 110 | AXF1 | fail | fail | axis flip 造成 single-pin regression，评估系统成功拦截风险 |
| 202 | AXF2 | fail | fail | axis flip 造成元件自短路，属于不应自动接受的候选 |
| 301 | AXF1 | fail | pass | 语义上修复主要未连接问题，但确定性 eval 对 pin_count_changed 较保守 |
| 302 | AXF1 + MRG1 | fail | pass | 多步 repair 语义上成功，解决 unmatched pins 和 single-pin nets；确定性 eval 仍标记 pin_count_changed |
| 303 | MRG1 | pass | pass | 多分支图 merge_nodes 修复成功 |

## 表 6：实验 B 规则消融与参数扰动矩阵

| Variant | 类型 | Changed cases | Topology count changed | Export failures | 结论 |
| --- | --- | ---: | ---: | ---: | --- |
| baseline | baseline | 0 / 20 | 0 / 20 | 0 | 当前默认确定性配置 |
| ablate_terminal_corridor | 规则消融 | 19 / 20 | 9 / 20 | 0 | 最核心规则；关闭后拓扑明显退化 |
| ablate_graph_nodes_legacy | 规则消融 | 19 / 20 | 0 / 20 | 0 | graph-derived 与 legacy 在数量上接近，但内部选择变化明显 |
| ablate_unsupported_filtering | 规则消融 | 11 / 20 | 0 / 20 | 0 | 影响证据筛选和审计状态，但不常改变拓扑数量 |
| ablate_bridge_candidates | 规则消融 | 8 / 20 | 0 / 20 | 0 | 对部分复杂图有影响，但不是主要拓扑决定因素 |
| ablate_corner_regularization | 规则消融 | 4 / 20 | 1 / 20 | 0 | 对断笔/直角闭合有帮助，整体影响中等 |
| ablate_relay_support | 规则消融 | 2 / 20 | 0 / 20 | 0 | 当前测试集中影响较小 |
| ablate_candidate_support | 规则消融 | 0 / 20 | 0 / 20 | 0 | 当前测试集中基本无影响，可能是冗余规则或覆盖不足 |
| perturb_tight_25 | 参数扰动 | 10 / 20 | 7 / 20 | 0 | 参数收紧会导致较多拓扑波动 |
| perturb_loose_25 | 参数扰动 | 12 / 20 | 5 / 20 | 0 | 参数放宽也会影响复杂图，但没有导致导出失败 |

## 表 7：实验 B 的关键结论

| 观察 | 解释 | 答辩表述 |
| --- | --- | --- |
| terminal corridor 影响最大 | terminal 作为锚点确实是拓扑恢复核心机制 | 系统不是直接依赖 Hough，而是以 terminal 约束 wire evidence |
| 参数扰动会影响复杂图 | 手绘图存在断笔、粘连、噪声和几何歧义 | 复杂图不能完全依赖固定阈值，需要 Agent 审计和修复 |
| candidate support 影响为 0 | 当前测试集没有体现该规则价值 | 我们不仅堆规则，也通过消融识别可能冗余的规则 |
| export failures 为 0 | 不同规则设置下 DXF 导出链路稳定 | 即使拓扑质量变化，工程输出流程本身是稳定的 |

## 表 8：实验 A case 级结果备查

| Case | Group | 初始状态 | Agent 决策 | Plan | Apply | Eval |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | baseline | needs_review | review_only_issue_for_human_review | - | not_applied | needs_review |
| 003 | baseline | needs_review | no_candidate_found | - | not_applied | needs_review |
| 004 | baseline | needs_review | no_candidate_found | - | not_applied | needs_review |
| 005 | baseline | needs_review | repair_candidate_ready_for_human_review | CLS1 | applied | fail |
| 101 | stress | needs_review | review_only_issue_for_human_review | - | not_applied | needs_review |
| 102 | stress | needs_review | review_only_issue_for_human_review | - | not_applied | needs_review |
| 103 | stress | needs_review | review_only_issue_for_human_review | RCAND1 | unsupported_apply_type | pass_with_warnings |
| 104 | stress | needs_review | repair_candidate_ready_for_human_review | MRG1 | applied | pass |
| 105 | stress | pass | review_only_issue_for_human_review | - | not_applied | pass_with_warnings |
| 106 | stress | pass | review_only_issue_for_human_review | EVR3, EVR1, EVR2 | unsupported_apply_type | pass_with_warnings |
| 107 | stress | needs_review | repair_candidate_ready_for_human_review | STB2 | applied | pass |
| 108 | stress | pass | review_only_issue_for_human_review | - | not_applied | pass_with_warnings |
| 109 | stress | pass | no_action | - | not_applied | pass |
| 110 | stress | pass | review_only_issue_for_human_review | AXF1 | applied | fail |
| 202 | showcase | fail | review_only_issue_for_human_review | AXF2 | applied | fail |
| 203 | showcase | needs_review | repair_candidate_ready_for_human_review | RCAND7 | unsupported_apply_type | pass_with_warnings |
| 204 | showcase | fail | needs_more_evidence | - | not_applied | pass_with_warnings |
| 301 | advanced_showcase | fail | repair_candidate_ready_for_human_review | AXF1 | applied | fail |
| 302 | advanced_showcase | fail | repair_candidate_ready_for_human_review | AXF1, MRG1 | applied | fail |
| 303 | advanced_showcase | needs_review | repair_candidate_ready_for_human_review | MRG1 | applied | pass |

## PPT 推荐引用句

实验 A 说明系统已经完成端到端 Agent 闭环：20 个 case 全部跑通，Agent 能生成 repair plan、执行 human-approved replay，并通过 eval 暴露不安全修复。实验 B 说明确定性规则不是随意堆叠：terminal corridor 是最核心机制，复杂图对参数扰动敏感，因此需要 Agent 进行高层审计和修复建议。

