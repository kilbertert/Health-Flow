# 20. 规格树是意图侧的真值，门禁跑在 CI 而不是本机 git hook

日期：2026-10-17
状态：已采纳
影响版本：新增 `.spec/` 规格树（根 1 + 层/概念 24 个节点）与 `.github/workflows/spec-tree.yml`；
不改任何运行时代码

## 背景

本仓库的意图写得非常完整：`ARCHITECTURE.md` 逐节讲清数据流与七个模块；`GLOSSARY.md` 有领域词条；
`docs/adr/` 有 19 条决策记录；而最值钱的是**各模块 docstring**——它们记着每条规则
曾经由几份实现回答、副本怎么漂移、代价是什么。

`docs/analysis-2026-10-17-vocabulary-ownership.md` 把这件事总结成一句话，并给出了量出来的普遍性：

> 一个领域事实的含义，由「恰好写了它的那段代码」携带，而不是由一份所有生产者与消费者共享的
> 声明携带。

改前对 `app/` 下全部 `Literal[...]` 做 AST 扫描：**34 处，只有 8 处是派生的**，
值域完全相同的重复声明 **8 组**。三份同步失败的后果逐条对应且不是巧合：N 个生产者 → 漂移；
契约不承载该事实 → 前端复算或发明一个谁都校验不到的字符串；词表住在散文里 → 拼错漏值无人发现；
守卫是事后手写、一次一个概念 → **覆盖面等于「有人想起来写了一份」**。

这四样东西（ARCHITECTURE / GLOSSARY / ADR / docstring）共同缺一样：**没有机制在代码改动后
指出其中哪一条过期了**。同一条概念被实现四次的代价已经被逐条记录，但那条记录本身
不会在第五次发生时发出信号。

## 决策

引入 SpexCode 的 `.spec/` 规格树：`.spec/healthflow/…/<node>/spec.md`，每个节点 frontmatter 的
`code:` 指向它治理的文件或具名单元（`path#Symbol` 即 anchor）。漂移由 git 现算：
自该 spec.md 最后一次提交以来，锚定单元的行区间是否被提交改动过。命中即 `anchor-drift`
错误并指名越线的那次提交。不存任何漂移数据库。

节点按 `ARCHITECTURE.md` 的分节与 `app/` 的实际分层建，不另造分解：
`report-api` / `service` / `schema` / `data` 四个当前运行时节点，
`agent` 与 `archive` 两个**冻结代码**节点，再把仓库里最贵的那些「一个概念被实现四到六次」
收成节点——`admission`、`interpretation-failure`、`report-material`、
`confirmation-decision`、`report-metric-counts`、`recommendation-empty-reason`、
`audit-events`、`report-lifecycle`、`diagnosis`、`config-freshness`、`external-boundary` 等。

采用时确立四条：

1. **以 `--pure` 引入。** 只新增 `.spec/`；不装 git hook、不改 `CLAUDE.md`/`AGENTS.md`、
   不生成 `.plugins/`。三项已逐一验证为空。

2. **anchor 只落在承载契约的单元上。** `vision_encoder.py`（485 行）锚 `#VisionEncoderService`、
   `safety_guard.py` 锚 `#check_response`、`report_ownership.py` 锚整文件（它本身就是那条边界）。
   文件级 anchor 会让任何无关改动都报漂移，噪音会训练人忽略这条检查。

3. **门禁跑在 CI，不接本机 `serverPolicy.chainedHooksPath`。** 阻断式 hook 技术上可接，
   但它需要每台机器手工 `git config`，而 `spex init` 写入的内容不带机器可读标记，
   会被 `dev-policy-audit` 判定为「仓库覆盖 `core.hooksPath`」并尝试修复。
   **一个会被别的工具悄悄撤销的护栏不是护栏。**

4. **CI 必须 `fetch-depth: 0`，版本钉 `0.7.0`。** 实测在真浅克隆（depth 1）下 lint 报
   `0 error 0 warning`，即便锚定单元已被改动。一个静默通过的检查比没有检查更糟。

**冻结代码单独成节点**（`archive`、`agent`），理由不是分类癖：「冻结」是一个**状态**，
不是一个位置。这些模块散落在 `api/`、`service/`、`agent/` 里，读代码的人无法从目录判断
它是否在跑。把状态写成节点，读的人一眼知道改它不产生对外行为。

## 被否掉的替代形态

**不引入规格树，沿用 ADR 0019 那条「封闭取值集只能有一位主人」的类级不变式。**
这是最强的替代，且它已经落地并在跑：一条机械检查覆盖全类，比一次一个概念的手写守卫强，
并且它的量出结论（8 组重复声明）正是本次采纳的依据。它输在**覆盖面只到值集**——
`docs/analysis-2026-10-17-vocabulary-ownership.md` 自己列出的候选里有四类不在其射程：
抽取队列状态词表、`{"confirmed","assessed"}` 业务组合、前端生效值回退链、前端主体一致性闸门。
更根本的是：那条不变式检测**值集重复**，而本仓库的缺陷形态还包括「一段无名局部规则决定
哪些值跨过证据边界」（`metric_effective_value`）与「四层平行实现坐标合法性」
（`origin_location`）——这两种没有可机械检测的重复声明，只有「这条意图归哪段代码管」。
规格树补的正是后半句。

**把契约写进代码注释。** 理由：注释就在代码旁边，改代码的人一定看得见。它输在两处：
注释无法被 lint 成「这条意图归哪个文件管」的关系，而那正是要检测的东西；
且本仓库既有实践恰好相反——契约级意图写在模块 docstring 顶层，本地坑写在行内注释，
把契约塞进函数上方会被本仓库自己的 `CODING_STANDARDS.md` 判为层级错位。

**接 `serverPolicy.chainedHooksPath`，让本机提交就被拦。** 它确实能拦，反馈比 CI 早、
回路比 CI 短，这是它最强的理由。它输在可持续性：配置不在仓库里、需要每个 clone 重复、
且会被 `dev-policy-audit` 的修复路径撤销。代价是本机可以提交未消解的漂移、由 CI 在 PR 上拦
——反馈变晚，但不会失效。

**只给最贵的七条规则建节点，不给分层建节点。** 理由：节点越多维护越贵，而层节点
（`report-api` / `service` / `schema` / `data`）说的是「哪里有什么」，README 已经说了。
它输在两处：其一，没有层节点就没有覆盖网，coverage 会常年挂着一批未归属文件，
而一个开局就黄的门禁会被关掉；其二，根节点的架构图画的是**直接子节点**，
没有层节点就没有可画的顶层结构。

## 后果

**更容易的。** 「这段代码归哪条意图管」从跨四份文档搜索变成读一个 frontmatter。
锚定单元被改动而 spec 未动时，lint 直接给越线提交号，以及
`git diff $(git log -1 --format=%H -- <spec.md>)..HEAD -- <file>` 这条读代码增量的命令。
`ARCHITECTURE.md` 与 spec 树的分节一一对应，改架构时两处一起动有了依据。

**更难的、以及被限制住的。** 新增一条维护义务：改动锚定单元时必须同步改 spec，
或用 `git commit --trailer "Spec-OK: <node>"` 记录「契约仍成立」的真实理由。
这条义务的强度完全取决于 anchor 选得准不准——选粗了退化成噪音，选细了（类型/接口）
重构时频繁误报，所以约定是锚行为承载单元。**本记录不主张这套机制能自动判断意图是否真的
更新**：`Spec-OK` 是一次诚实声明，不是一次验证；一个盲目的 ack 就是一句谎。

**一处已知的当前状态。** `spec-tree` 作业目前**不是必需检查**（必需检查为
`Workflow policy` + `Tests`）。今天它会在 PR 上变红但不阻断合并。
把它提升为必需检查是一个独立决定，应先观察它在本仓库的误报率。

**本记录明确不主张的。** 它不主张 `.spec/` 取代 `ARCHITECTURE.md`、`GLOSSARY.md`、
`docs/adr/` 或 ADR 0019 那条类级不变式。分工是：ADR 回答「为什么这样决定、否掉了什么」；
ARCHITECTURE 回答「数据怎么流、往哪读」；GLOSSARY 回答「这个词是什么意思」；
0019 的检查回答「值集有没有第二个主人」；spec 节点回答「这段代码归属哪条意图、它是否还成立」。
最后一个是唯一**可锚定、可检测**的，这正是它不替代任何一份、而是补上它们共同缺的那一环。
