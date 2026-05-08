# Sketch2DXF 案例分析演讲稿

本文档不是素材路径索引，而是答辩时可以直接照着讲的案例分析稿。整体叙述以 **301** 为主案例，完整展示从手绘图像到结构化拓扑、Agent 修复、human approval、最终 DXF 的闭环；再用 **005、302、303** 补充展示 Agent 层最有代表性的能力。

---

## 0. 开场：这个项目真正解决的问题

我会先明确一点：我们这个项目不是在做“手绘线条的像素级描摹”，也不是单纯把一张图片矢量化。我们真正做的是：

> 从普通 2D 位图中的手绘电路图恢复结构化电路拓扑，并进一步生成可编辑、可复用的标准 DXF 工程图。

这句话里有两个关键词。第一个是 **结构化拓扑**，也就是系统最后要知道有哪些元件、每个元件有哪些端子、哪些端子属于同一个电气节点。第二个是 **可编辑 DXF**，也就是说输出不是一张描边图，而是一份工程图，里面的电阻、电容、电源和导线都可以被 CAD 软件继续编辑。

这类任务的难点在于，手绘电路图并不是一个干净的几何图形。它有断笔、抖动、元件和导线粘连、手写文字干扰、bbox 偏移、局部导线缺失，也有检测模型对相似符号的类别混淆。所以如果只靠 Hough 直线检测或者只靠图像处理，很容易遇到一个问题：每张图都需要重新调参数，系统变得很脆。

因此我们的设计思路是把整个问题拆成三层：

1. **元件语义层**：检测元件，估计类别、位置和端子。
2. **连接证据层**：从去除元件后的 residual 图像里提取导线、端点、junction 等证据。
3. **拓扑推断层**：不直接相信任何一条线，而是以 terminal 为锚点，把证据组织成 electrical nodes 和 netlist。

在这个基础上，我们再引入 Agent 层。Agent 不是代替视觉识别，也不是直接看图胡乱猜答案。它的角色更像一个受控的电路审计员：它读取主链路产生的结构化证据，提出假设，调用工具验证，生成 dry-run 修复计划，然后由人类批准后才真正修改 topology。

下面我用 301 这个 case 详细讲完整流程。

---

## 1. 主案例 301：一个三阶段 RC ladder 的完整恢复过程

### 1.1 原始图像：为什么这张图适合作为主案例

301 的原图是一个手绘的三阶段 RC ladder。图中左侧是电源，顶部有一串电阻，几个中间节点向下接电容，右侧还有一个负载电阻，底部是一条公共参考线。这个结构对电路来说很典型：它不是一个简单串联回路，而是包含串联主链和多个 shunt branch 的多节点网络。

从图像角度看，它也正好包含了这个项目要面对的典型困难：

第一，图中既有锯齿形电阻，也有手绘矩形电阻，符号风格并不统一。

第二，导线不是干净的 CAD 线，而是有手绘粗细、断裂和局部粘连。比如顶部主线经过多个元件，底部公共线又很长，局部 junction 很容易被错误拆开或合并。

第三，图上方还有手写文字 “3 stage RC ladder”，这些文字会在 residual 图像里形成黑色笔迹，如果处理不好，会被误认为短线段或孤立 evidence。

第四，右侧的负载电阻是竖向的，而检测框本身并不直接告诉我们 terminal 一定在上下还是左右。这个问题后面正是 Agent 需要修复的一个点。

所以 301 适合作为主案例，是因为它既能展示主链路已经具备一定复杂度，又能展示 Agent 如何处理局部不确定性。

### 1.2 第一步：元件语义层

在检测图中，YOLO 给出了电阻、电容和电源的候选框。这里我不会把重点放在 YOLO 本身，因为我们项目里已经假设元件检测效果总体可用。更关键的是，YOLO 输出以后，我们并不是直接得到拓扑。

检测框只告诉我们：“这里大概率有一个电阻或电容”。但电路拓扑真正关心的是：“这个元件的两个端子分别接到哪个电气节点”。也就是说，bbox 只是起点，terminal 才是连接推断的接口。

我们的 terminal 生成逻辑不是靠像素级检测每个端子，而是结合元件类别、bbox 几何形状，以及周围导线证据生成 terminal hypotheses。比如一个水平电阻通常左右出 pin，一个竖向电阻通常上下出 pin。但这里不能写死，因为手绘图里 bbox 会偏，元件可能倾斜，导线也可能在边缘附近断开。因此 terminal 位置要以 bbox 为基础，再参考附近导线证据。

这一层输出的是组件和 pin 的语义骨架。它相当于告诉后续系统：这些位置附近应该发生连接，但连接关系不能在这一层直接下结论。

### 1.3 第二步：导线证据层

接下来系统会把元件区域 mask 掉，在 residual 图像上提取导线证据。这里有一个很重要的设计选择：我们没有把 Hough 直线检测当作最终真相，而是把它降级成 evidence。

原因很简单。手绘导线经常会断，电阻和电容符号会和导线粘连，文字也会残留。如果要求 Hough 把所有导线都完美恢复，整个系统会变成参数调试工程。换一张图，阈值、最短线段长度、gap merge 参数都会变。

所以在我们的系统里，wire extraction 的职责是产生候选证据。它会输出线段、端点、junction、raw evidence component，以及这些证据的来源和局部质量信息。真正决定连接关系的是后面的 topology inference。

在 301 的 overlay 图里，可以看到系统把导线证据画成了不同颜色：水平线、竖直线、端点、junction、node、pin 都分开标注。这个图非常关键，因为它说明系统不是一个黑盒。我们能看到每一步中间结果：哪些地方被识别成导线，哪些地方被认为是端点，哪些地方被聚合成节点，哪些 terminal 被匹配到节点。

### 1.4 第三步：从 evidence graph 到 active nodes

证据层输出以后，系统不会立刻把所有几何连通块都变成 electrical nets。这里是早期版本最容易出错的地方：如果“见线就收”，文字残留、孤立短线、局部噪声都会进入 netlist。

因此现在的主链路有一个重要机制：区分 raw graph 和 supported graph。

raw graph 是所有几何证据形成的初始图，它比较宽松，尽量保留可能有用的信息。supported graph 则会利用 terminal 支持、节点连通性、证据质量等信息，把真正可能属于电路的部分筛出来。最后 active electrical nodes 只从 supported graph 中生成。

这个设计解决了两个问题：

第一，它避免把噪声证据直接变成电气节点。比如手写文字形成的小线段，可能会出现在 raw evidence 里，但如果没有 terminal 支持，也不应该进入最终 topology。

第二，它给 Agent 提供了可解释的审计入口。Agent 后面可以问：这个 node 为什么被保留？这个 raw component 为什么被 discard？这个 single-pin net 是真的开路，还是导线断裂造成的？

301 初版的主链路结果是 graph-derived，这一点很重要。它说明当前拓扑不是 fallback 拼出来的，而是从 evidence graph 和 supported graph 推导出来的。初版已经恢复出大部分结构，包括 8 个元件、16 个 pin 和多个 net，但仍然存在局部错误。

这正是我们设计 Agent 的原因：确定性主链路负责建立大体结构，Agent 负责处理主链路暴露出的不确定点。

---

## 2. 301 的初版问题：为什么需要 Agent

301 的初版不是完全失败，而是“整体结构对了，但局部连接有问题”。这个状态很符合真实工程系统：最难的不是从零到一，而是在一个大体正确的结果里找到少数关键错误。

301 里主要有两类问题。

第一类是 single-pin stub。也就是说某个节点只连到了一个元件端子，看起来像一个悬空端。对于电路图来说，single-pin net 不一定绝对错误，因为真实电路也可能有测试端口或未连接端，但在 301 这种闭合 ladder 结构里，它更可能说明导线断裂、节点没合并，或者局部 evidence 没有被正确支持。

第二类是 terminal axis fallback。右侧的一个电阻在图像中是竖向放置的，但它的 terminal 方向初始判断并没有足够的直接证据，导致两个 pin 没有正确接到上下两个节点。这个问题如果只靠规则硬改，很容易过拟合；但如果让 Agent 先观察、再 dry-run 验证，就比较安全。

这两个问题的共同点是：它们都不是像素级问题，而是结构解释问题。导线证据已经在那里，元件框也在那里，但系统需要判断哪种解释更符合电路拓扑。这就是 Agent 最适合发挥作用的部分。

---

## 3. 301 中 Agent 的推理过程

### 3.1 Agent 不是一次性输出建议，而是 hypothesis-tool loop

我们现在的 Agent 不是简单调用一次大模型生成报告。它是一个 LangGraph workflow，外层流程大致是：

```text
audit -> observe -> planner/tool loop -> critic -> reviewer
```

这里的 planner 和 reviewer 由 LLM 驱动。planner 负责根据当前状态提出下一步要调用什么工具，reviewer 负责在工具结果都返回以后，选择哪些候选进入最终 repair plan。

工具本身是确定性的。例如 inspect 工具只读取证据，dry-run 工具只模拟修复效果，不会直接修改 topology。这一点很重要，因为它保证了 Agent 的“想法”必须经过工具验证，不能只靠自然语言说服我们。

换句话说，LLM 负责提出假设和选择工具，规则工具负责给出可复现的证据和 dry-run 结果。这是一个比较稳的 Agent 架构：LLM 有推理空间，但每一步都被结构化工具约束。

### 3.2 Agent 第一步：先检查 terminal axis

301 的 Agent 首先注意到一个关键现象：某个电阻的 pin 没有正确匹配，而且它的方向判断来自 fallback。这时 LLM 没有直接说“把它翻转”，而是先调用 `inspect_component_terminal_axis`。

这个工具会回答一个很具体的问题：

> 当前元件的 terminal 方向有没有导线证据支持？如果换一个方向，端子附近是否能看到更合理的证据？

这一步的意义在于，Agent 把“方向可能错了”从一个猜测变成了一个可验证问题。它先收集当前方向和替代方向的 attachment evidence，再决定是否需要 dry-run。

### 3.3 Agent 第二步：检查 gap bridge 和 single-pin stub

接着 Agent 调用了 gap bridge 和 single-pin stub 相关工具。这里体现了它不是只盯着一个错误，而是在检查不同错误之间是否有关联。

在电路图里，一个 unmatched pin 和一个 single-pin stub 往往不是两个独立错误。它们可能来自同一个原因：某段导线断了，或者某个局部节点没有合并。Agent 因此会问：

- 这个 single-pin net 是否靠近某个 supported node？
- 它们之间是否轴向对齐？
- gap 是否足够小？
- 附近有没有端子证据支持？
- 合并后会不会引入短路或回归？

这就是工具化 Agent 的价值。LLM 不是在脑子里想象电路，而是把问题拆成多个可以被工具回答的小问题。

### 3.4 Agent 第三步：dry-run single-pin stub bridge

当 Agent 发现某个 single-pin stub 与附近 supported node 之间有较小的 axis-aligned gap，并且 terminal attachment 很强时，它提出第一个修复假设：

> 这个孤立 stub 应该并入旁边的 supported node，从而把一个悬空电容端子接回 RC ladder 的主结构。

然后它调用 dry-run 工具验证。dry-run 的关键是“模拟修改但不真正改文件”。它会检查这个合并是否能减少 single-pin net，是否会造成 net 数异常、pin 丢失、同一元件两端短接等问题。

在 301 中，这个候选通过了验证。它的意义不是“补一条线”这么简单，而是把一个原本孤立的 terminal 重新放回 RC ladder 的第一级结构中。也就是说，修复结果符合电路语义。

### 3.5 Agent 第四步：inspect terminal attachments

解决 stub 问题后，Agent 继续追踪另一个问题：右侧负载电阻的两个 pin 为什么没有接上？

它调用 `inspect_terminal_attachments` 去看两个 pin 附近的候选证据。这个工具不是给结论，而是给 LLM 提供结构化事实：每个 pin 附近有哪些 node、segment 或 raw evidence，距离多少，方向对齐程度如何，证据是否强。

在 301 里，当前 horizontal axis 下并没有足够好的 attachment，但从图像和 bbox 几何上看，这个电阻更像竖向元件。于是 LLM 形成第二个假设：

> 当前 terminal axis 可能错了。如果把这个电阻改成上下出 pin，它可能分别连接顶部输出节点和底部公共节点。

### 3.6 Agent 第五步：dry-run component axis flip

Agent 随后调用 `dry_run_component_axis_flip`。这个工具会把元件 terminal 方向改成候选方向，重新计算 pin 到 node 的连接，并检查 topology 指标是否改善。

在 301 中，axis flip dry-run 的结果非常关键：

- 当前方向下，两个 pin 没有可靠连接。
- 改为 vertical 后，两个 pin 都能接到合理节点。
- unmatched pin 数量下降。
- 没有引入明显回归。

这时 Agent 才把这个修复候选加入 repair plan。

这一步体现了我们 Agent 层最重要的思想：LLM 可以提出“换方向”的假设，但它不能直接改 topology。必须通过 dry-run 工具验证，确认确实改善结构指标，才会进入最终计划。

### 3.7 critic：为什么不是 LLM 想停就停

在 planner 结束以后，还有一个 critic 阶段。critic 的作用不是重新识图，而是检查 Agent 的行为是否合规，比如：

- 是否有工具调用失败？
- 是否存在未解决的关键问题却提前结束？
- dry-run 是否真的没有修改 topology？
- 是否至少有一个可排名、可审核的修复候选？

这一步很重要，因为 LLM 有时会“觉得差不多了”就输出 final。critic 相当于一个规则护栏，保证它不能带着明显 open question 直接结束。对于工程任务来说，这比单纯 prompt 更可靠。

### 3.8 reviewer：从多个候选形成 repair plan

最后 reviewer 会根据所有工具结果形成 repair plan。301 的最终 plan 包含两个步骤：

第一步是 single-pin stub bridge，把孤立 stub 接回主结构。

第二步是 component axis flip，把右侧负载电阻从错误方向修正为竖向连接。

这两个修复都是 dry-run 验证过的，且它们解决的是不同问题：一个修复孤立节点，一个修复未匹配 pin。reviewer 没有把所有可能建议都塞进去，而是选择了能直接改善 topology 指标、风险较低、适合 human review 的候选。

这里可以强调一个项目亮点：

> Agent 输出的不是一句自然语言建议，而是一份可执行、可追踪、可回放的 repair plan。

---

## 4. 301 的 human approval 与 corrected topology

Agent 生成 repair plan 后，系统不会自动改 topology，而是生成 human approval request。用户确认后，repair apply 才会真正修改 topology、netlist 和 DXF。

这个设计解决了两个问题。

第一，LLM 有不确定性，不能让它直接改工程文件。

第二，拓扑修复应该可追溯。我们需要知道是哪一个候选修改了哪个节点、哪个 pin，修改前后有哪些指标变化。

301 中 human approval 接受了两个候选。修复后的拓扑可以解释成一个标准三阶段 RC ladder：左侧电源驱动顶部串联链路，每一级通过电容连接到底部公共 net，右侧还有负载电阻。也就是说，修复后的 topology 不只是“指标变好了”，它在电路语义上也更合理。

这一点答辩时可以这样讲：

> 修复后的结果把图像中的局部连接错误转化成了一个标准 RC ladder 拓扑。这里 Agent 的作用不是替代算法，而是在主链路已经给出大体结构后，对局部不确定点做可验证的结构修复。

---

## 5. 301 的 DXF：为什么不是简单描边

最后一步是 DXF 导出。这里我会特别强调：DXF 不是从原始图像直接描线生成的，而是从 corrected topology 重新绘制。

这带来一个很大的好处：手绘图里的线条抖动、倾斜、粗细不一都不会直接进入最终工程图。输出的 DXF 会被规整成标准化结构：元件对齐、导线水平/垂直、节点清晰、底部公共线明确。

301 的 DXF 使用的是 ladder-style clean redraw。系统根据 topology 识别出：

- 哪个 net 是底部公共参考线；
- 哪些元件构成顶部串联链；
- 哪些元件是从顶部节点接到底部公共 net 的 shunt branch；
- 右侧负载应该作为一个竖向支路放置。

所以最终 DXF 看起来更像规范电路图，而不是把手绘图原样复制出来。这非常符合题目要求里的“可编辑和复用的标准化矢量图”。

---

## 6. 301 可以怎么完整讲出来

下面是一段可以直接照着讲的版本。

> 这一页展示的是 301 这个主案例。原图是一个三阶段 RC ladder，左边是电源，顶部有串联电阻，中间有多个电容支路，右侧还有一个负载电阻。这个图比简单串联回路更有代表性，因为它包含多个节点、公共底线和并联支路。
>
> 系统首先通过 YOLO 检测元件。检测框本身只告诉我们元件在哪里，但拓扑恢复真正关心的是端子接到哪里。所以我们会基于 bbox 和周围导线证据生成 terminal hypotheses。
>
> 接下来，系统把元件区域 mask 掉，在 residual 图像上提取导线 evidence。这里我们没有把 Hough 结果当成最终真相，而是把它作为连接证据。因为手绘线条有断笔和抖动，要求导线提取百分百正确会导致系统非常脆。
>
> 然后系统构建 evidence graph，再利用 terminal 支持过滤 unsupported evidence，得到 active electrical nodes。301 的初版是 graph-derived 结果，说明它不是 fallback 拼出来的，而是从证据图推断出来的。初版已经恢复出了主要拓扑，但仍然有两个局部问题：一个是 single-pin stub，另一个是右侧电阻的 terminal axis 可能选错，导致 pin 没有正确匹配。
>
> 这时 Agent 层开始工作。Agent 不是直接看图改答案，而是在 LangGraph workflow 中循环调用工具。它先调用 terminal axis inspection，确认右侧电阻当前方向缺少证据；再检查 gap bridge 和 single-pin stub，发现一个孤立端子和 nearby node 之间存在合理的轴向 gap；随后调用 dry-run 工具验证 stub bridge，发现合并后可以减少 single-pin net 且没有回归。
>
> 接着 Agent 又检查右侧电阻两个 pin 的 terminal attachment，提出 axis flip 的假设，并调用 dry-run component axis flip。dry-run 发现改成竖向 terminal 后，两个 pin 可以分别接到顶部输出节点和底部公共节点，unmatched pin 数下降，拓扑更合理。
>
> 最后 reviewer 把两个通过验证的候选组成 repair plan。注意，这时 topology 还没有被修改。系统先生成 human approval request，用户确认后才 apply。apply 后会重新生成 corrected topology、netlist 和 DXF。
>
> 最终 DXF 不是简单描摹原图，而是根据 corrected topology 重新布局成 clean ladder schematic。这样输出的图更标准，也更适合 CAD 编辑和复用。

---

## 7. 辅助案例 005：类别语义修复

005 的亮点是展示 Agent 不只会修导线，也能修元件类别。

这个图里，主链路把左侧元件识别成了普通无极性电容，但 YOLO 的候选信息里其实保留了另一个被抑制的类别：power source。也就是说，检测模型不是完全没看到电源，而是在 NMS 或类别竞争中把它压下去了。

如果只看最高分分类，系统会认为它是电容。但从电路语义上看，一个完整回路通常应该有电源，而当前 topology 里没有 power source。这就是 Agent 可以发挥作用的地方。

Agent 的思考过程大致是：

第一，它先调用 component class candidates inspection，查看这个元件是否有其他类别候选。结果显示 power source 是一个 suppressed alternative，而且 bbox 与当前候选高度重叠。

第二，它调用 dry-run class override，把元件类别临时从电容改成电源，检查是否会破坏 terminal 数量、节点连接和 netlist。因为电容和电源都是 2-pin 符号，所以这个 override 不会改变拓扑连接，只改变元件语义。

第三，它继续检查低置信 pin match，确认没有更好的 reattachment 方案。因此最终只推荐 class override，而不是乱改连接关系。

这个案例可以这样讲：

> 005 展示的是 Agent 对检测模型不确定性的利用。我们没有让 LLM 直接看图判断类别，而是把 YOLO 的多类别候选、bbox overlap 和电路语义缺失作为结构化事实提供给它。Agent 发现当前电路没有电源，又发现同一位置存在 suppressed power-source 候选，于是调用 class override dry-run。验证通过后，它把这个候选提交给 human review。

这个案例的技术点是：我们不是把检测模型当成一个只输出 top-1 的黑盒，而是保留 class candidates，让 Agent 可以在高层语义上重新审计检测结果。

---

## 8. 辅助案例 302：多轮工具调用与 guardrail

302 的亮点是展示 Agent 真的在多轮调用工具，而不是一次性生成报告。

这个 case 的初版结果用了 legacy fallback，所以它不适合作为主链路最漂亮的代表。但它非常适合展示 Agent 处理复杂不确定性的过程。

302 里有两个问题：

第一，一个竖向元件被初始 terminal axis 解释错了，导致两个 pin unmatched。

第二，左侧有两个 single-pin nets，看起来像本应相连但被断开的节点。

Agent 的推理过程很有代表性：

它先处理高优先级的 unmatched pins，调用 terminal axis inspection，发现当前方向没有足够证据。然后调用 component axis flip dry-run，验证改为竖向后可以把两个 pin 接回上下两个节点。

接着它检查 single-pin nets。它不是直接合并，而是先调用 single-pin inspection，看这两个孤立端是否互相接近、是否有 supported node、是否可能 bridge。然后它尝试 single-pin stub bridge。这个工具没有返回候选，说明更严格的 stub bridge 规则不支持这个操作。

这时系统里一个很精彩的机制出现了：guardrail 发现 planner 想结束，但仍有一个 nearby single-pin pair 没有被 `dry_run_merge_nodes` 验证。于是 guardrail 不让 Agent 提前 final，而是把它送回 planner loop。LLM 收到反馈后继续调用 merge-nodes dry-run，最终发现合并两个 single-pin nodes 可以消除孤立 net 且没有明显回归。

这个过程体现了 Agent 系统的几个亮点：

第一，LLM 是 planner，它会根据工具结果动态决定下一步，而不是固定调用五个工具。

第二，工具是分层的。同一个问题可能先用 inspect 工具观察，再用更严格的 stub bridge 验证；如果失败，再尝试更一般的 merge-nodes dry-run。

第三，guardrail 可以阻止 LLM 带着未验证问题提前结束。这对工程任务非常重要，因为 LLM 的自然倾向是“给一个听起来完整的解释”，但系统需要的是可验证闭环。

可以这样讲 302：

> 302 展示了 Agent 层最像智能体的一面。它先根据审计结果提出 axis flip 假设，通过 dry-run 验证；随后发现 single-pin nets，还尝试了 stub bridge。第一次工具没有给出候选时，Agent 并没有直接放弃，guardrail 发现仍有未验证的 nearby single-pin pair，于是要求继续调用 merge-nodes dry-run。最终 repair plan 包含 axis flip 和 node merge 两步。这说明 Agent 不是简单报告生成器，而是在工具反馈驱动下逐步收敛到可执行修复计划。

---

## 9. 辅助案例 303：多分支 clean DXF 与局部合并

303 的主链路已经是 graph-derived，而且没有 error，只是存在 warning。它适合展示系统对多分支结构的 clean DXF 输出。

303 里的关键问题是一个 single-pin net 与附近主节点几何上高度重叠，但初版 topology 没有把它合并进去。Agent 的第一反应是尝试 single-pin stub bridge，但这个更严格的工具没有返回候选。随后 Agent 调用 merge-nodes dry-run，发现这个孤立节点与主节点 bbox 完全重叠，合并后可以消除 single-pin net，而且不会引入回归。

这说明我们的工具不是单一规则。对于同一个现象，Agent 可以先用更保守的工具，如果失败，再用更一般的工具验证。最终 reviewer 选择了 merge-nodes 候选进入 repair plan。

303 的最终 DXF 展示效果很好：多分支网络被规整成上下 rail 和竖向支路。这里的重点是，DXF 输出不是复刻手绘图，而是根据 topology 重新布局。对于工程图纸重建来说，这是更有价值的，因为它输出的是可编辑结构，而不是手绘噪声。

可以这样讲：

> 303 说明我们的系统不只是在做连接判断，还能把恢复出的 topology 转换成更规范的工程表达。Agent 修复了一个局部 node split，DXF 阶段再把多分支拓扑规整成 clean schematic。这个 case 适合作为最后展示页，因为它直观体现了从手绘图到标准化矢量图的最终效果。

---

## 10. 项目亮点总结

### 10.1 亮点一：不是像素描摹，而是 topology recovery

传统图像矢量化更关注线条边缘和几何形状。但电路图重建更关心电气语义。我们的系统最终输出的是 components、pins、nets、netlist 和 DXF，而不是一堆无语义线段。

这使得后续可以做检查、修复、解释和重新布局。

### 10.2 亮点二：Hough 只是 evidence，不是最终真相

早期如果把导线提取作为决定性结果，系统会非常依赖参数。现在的设计把 Hough 和 wire extraction 降级为证据层，最终连接由 terminal-anchored topology inference 决定。

这让系统对手绘断裂、局部噪声和文字干扰更稳。

### 10.3 亮点三：raw graph / supported graph / active nodes 分层

不是所有几何连通块都应该进入 topology。系统先保留 raw evidence，再用 terminal 支持和图结构过滤，最后只把 active electrical nodes 写入 netlist。

这个分层也让 debug 更清楚：我们可以解释一个节点为什么被保留，另一个证据为什么被丢弃。

### 10.4 亮点四：Agent 是工具驱动的修复系统

Agent 不是直接修改 topology，而是：

1. 读取 audit facts；
2. 提出 hypotheses；
3. 调用 inspect 工具收集证据；
4. 调用 dry-run 工具验证修复；
5. 由 critic 检查是否还有未闭合问题；
6. 由 reviewer 生成 repair plan；
7. 人类批准后 apply；
8. replay 生成 corrected topology/netlist/DXF。

这个流程既体现 LLM 的推理能力，又保留工程系统的可控性。

### 10.5 亮点五：human-in-the-loop 保证严谨性

电路拓扑属于高准确性任务。我们没有让 LLM 自动改最终结果，而是把所有 topology mutation 放在 human approval 之后。这可以防止 LLM 幻觉，也让每个修复都有审计记录。

### 10.6 亮点六：DXF 是 topology-driven clean redraw

最终 DXF 从 corrected topology 生成，而不是从原图直接描边。系统会根据拓扑识别 ladder、rail、branch 等结构，重新生成更规范的电路图。这更符合“标准化矢量图”的要求。

---

## 11. 项目难点总结

### 11.1 难点一：手绘导线不可靠

手绘线条经常断开、粘连、抖动。单纯靠 Hough 或 skeleton 很难稳定恢复完整 wire mask。因此我们必须把导线当作证据，而不是最终答案。

### 11.2 难点二：terminal 是拓扑恢复的关键

导线证据本身没有电气语义。只有当 terminal 与 evidence 对齐后，系统才知道某条线是否真的属于某个元件连接。因此 terminal anchoring 是整个系统的核心。

### 11.3 难点三：局部几何正确不等于电路语义正确

有时两个线段几何上很近，但不应该合并；有时两个节点中间有缺口，但从电路结构看应该连接。这类问题很难用单一规则解决，需要 Agent 把多种证据结合起来审计。

### 11.4 难点四：LLM 不能直接接管识别

如果让 LLM 直接看图输出 netlist，结果不可控，也很难 debug。我们的做法是让 LLM 只操作结构化事实和受控工具。这比“全靠大模型”更稳，也更符合工程系统的要求。

### 11.5 难点五：修复必须可验证

拓扑修复不能只看起来合理，还要检查是否减少错误、是否引入短路、是否让 netlist 更糟。因此每个修复都必须先 dry-run，再 human approval，再 replay。

---

## 12. 最后总结页可以这样讲

> 这个项目实现了从手绘电路图到结构化拓扑，再到可编辑 DXF 的完整闭环。确定性主链路负责从图像中恢复元件、端子、导线证据和 electrical nodes；Agent 层负责审计主链路中的不确定点，通过多轮工具调用验证修复假设，并在人类确认后修改 topology。最终 DXF 不是手绘线条的描摹，而是 topology-driven clean redraw，因此更接近标准工程图。
>
> 从 301 可以看到系统已经能处理多级 RC ladder 这类非平凡结构；从 005 可以看到 Agent 能利用检测候选和电路语义修复类别；从 302 可以看到 Agent 能进行多轮工具调用和 guardrail 驱动的继续验证；从 303 可以看到系统能把多分支拓扑导出成规整 DXF。
>
> 当前系统仍主要覆盖基础 2-pin 元件和常见拓扑，但它已经具备一个完整、可解释、可审计、可扩展的工程框架。后续扩展更多元件类别、总线结构、多端器件和更复杂的版式时，可以继续沿用这套“确定性主链路 + 工具化 Agent 修复 + topology-driven DXF”的架构。

