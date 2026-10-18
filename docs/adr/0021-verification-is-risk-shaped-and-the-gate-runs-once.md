# 21. 验证按风险成形，全量门禁只跑一次

日期：2026-10-18
状态：已采纳
影响版本：AFK 脚手架模板 1.7.23（`.sandcastle/*` 的 prompt 与 `CODING_STANDARDS.md`）；
不改任何运行时代码

## 背景

AFK 的三个实现 prompt 里长期写着这一段，且它直接坐在验证门禁之上：

```
Use red-green-refactor where applicable:

1. RED: write one failing test
2. GREEN: implement to pass it
3. REPEAT until the issue is done
4. REFACTOR the code
```

**它不是"测试约定"，它是一个循环。** `3. REPEAT until the issue is done` 读起来就是
"一直转"，而紧跟其后的 gate 是**全量** `uv run pytest` + ruff。所以 agent 实际读到的
指令是「跑完全部，然后再来一轮」。

同族模板上的实测（AI-Ops，1805 个测试文件）：全量 `uv run pytest` **299.62 s**，
单文件 **2.91 s**——**≈103×**；而该 gate 在 run 日志里每个 issue 被调用 **4 次以上**。
一个 issue 因此在反复重跑它已经跑过的 suite。**写测试不是成本，重复才是。**

值得说准的是：**本仓从未启用任何覆盖率阈值**（`pyproject.toml` 无 `fail_under`，
CI 不验覆盖率，`.sandcastle/policy-check.mjs` 不读覆盖率）。压测出来的不是"测试太多"，
而是"同一个门禁在一轮里被要求跑很多遍"。

## 决策

**重写承载循环的那一个区块：保留"测试是最便宜的 oracle 时先用测试"，把全量门禁从
"每一轮"降为"收尾一次"。**

新文本要求：迭代时只跑**能证伪当前改动的最窄检查**（改动涉及的测试文件、typecheck、
linter）；全量 gate 在收尾跑一次。同时明确**不为凑数字造测试**——散文、格式化、
改名、一行接线不欠测试；要能说出"哪个输入会弄坏这次改动"，说不出来就在 commit body
里说，而不是写一个把实现断言回自己的测试。

`CODING_STANDARDS.md` 的 `Update tests alongside behavior changes; keep the suite green.`
改为：**回归测试是老行为仍然成立的持久记录**（这条被强化，不是削弱），而全量 suite 是
**交付前的门禁，不是每次编辑后转的圈**。

**与 `verify-healthflow` 的关系是互补，不是替代。** 那个 skill 驱动**真实应用**、
产出截图证据，回答"患者看得见的行为对不对"；本决策管的是 pytest 的**跑法与范围**。
两者都不把对方的结论当自己的证据——prompt 里"Tests are not a substitute"那句原样保留。

## 被否掉的替代形态

- **整份照搬外部的「从此不写测试、既有红灯不再是事故」doctrine。** 与
  `.sandcastle/consensus-contract.json` 把 `deterministic-ci` 列为不可豁免不变量的做法
  直接冲突：红灯 = CI 红 = 合不了。照抄会自断门禁。其中**「作者 ≠ Hacker」**（对抗性验证
  必须换手）被采纳，因为本仓 implementer 与 reviewer 是同族模型、同一套 prompt。

- **维持 blanket TDD，只把全量换成分片。** 治不到「REPEAT」这个结构：`3. REPEAT`
  读起来就是无限循环。103× 的比值说明省下的是**重复次数**。

- **让 verify-healthflow 承担全部验证，pytest 退到最少。** 反向的过度修正：那个 skill
  要跑浏览器、要人看截图，成本和反馈速度都不适合每次编辑后跑；而它覆盖不了
  `test_audit_single_producer`、`test_contract_vocabulary_ownership` 这类纯逻辑不变量。
  两者服务不同的目的。

- **用整文件覆盖（`cmp` 快照）下发这条纪律。** 本仓的 live prompt 与模板已差 9–25 行
  （手工套用的 `uv run pytest` 替换 + 本仓手写的 `verify-healthflow` 段落与
  `var/verify-evidence/` 证据落点说明）。覆盖会销毁那段，而"守卫拒绝"又会让升级在本仓
  静默 no-op。改用锚点编辑。

## 后果

- 一个 issue 在循环里的全量门禁次数从 4+ 降到 1。
- **回归测试的地位被强化，不是削弱**：`test_audit_single_producer`、
  `test_contract_vocabulary_ownership`、`test_auth` 这类不变量测试一个没动。
- 本仓手写的验证 skill 段落与证据落点说明**原样存活**。
- **代价**：迭代时只跑"最窄检查"依赖改动者判断范围，判断偏了会漏；补偿是 CI 仍跑全量，
  且 prompt 要求说出会弄坏改动的那个输入。
- 本决策只改脚手架 prompt，不改任何运行时代码，因此不影响服务行为。
