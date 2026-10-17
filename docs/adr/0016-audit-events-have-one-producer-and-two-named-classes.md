# 16. 审计事件由单点产出，且「状态动过」与「没动」在词表里分开

日期：2026-10-17
状态：已采纳
影响版本：本仓库（`app/service/audit_vocabulary.py`、`app/service/report_status.py`、
`app/service/report_worker.py`、`app/api/report.py`、`app/schema/report.py`）

## 背景

「这份报告发生过什么」由审计流回答，而这条流**没有家**。实测（只读回放，SQLite +
mock 证据服务）：

| 患者/系统动作 | 落下的审计事件 |
| --- | --- |
| 一次上传 → 确认（confirm 会链到 assess） | `confirmed` ×**2**、`assessed` ×**2** |
| `POST /report/{id}/assess`（重试生成提示） | `assessed` ×**2** |
| 一次解析（成功） | `extraction_completed` ×1（只走 `transition`） |
| 解析已成功、worker 在 ack 前重启 → `recover_stale_jobs` | `status_recovered` ×1，**没有任何状态迁移** |

根因不是「有人忘了合并两处」，而是**两个互不相识的生产者**：`report_status.transition()`
（唯一的状态迁移入口，顺手写一条审计）与 `app/api/report.py` 的私有 `_audit()`（被 worker
跨模块 `import`）。两边都不知道对方会为同一件事再写一条。

同一件事的两条记录**内容不同**，而且同一条 `assessed` 有**两个 actor**：
`report.py:1021` 写 `actor="system:evidence-service"` 带三个计数，`report.py:1033` 的
`transition(..., action="assessed", actor="system:report-assessor")` 再写一条 `detail={}`。
`confirmed` 同理：`transition` 写空壳，紧跟的 `_audit` 写 `confirmed_metric_ids`。
**读的人无法判断该信哪一条。**

两个方向的缺陷都在。**多写**：一次事件两条，名字相同、actor 可能不同、detail 一空一实。
**少走**：`uploaded` 只有审计、没有迁移（状态在对象落库前就设好，那一刻还没有 id 可写
审计）；worker 的「解析已提交、只差 ack」也只有审计、**没有迁移**（报告状态已经离开
`processing`，那一支刻意不再 `transition`）。审计流于是断言了一次「状态被恢复」，而状态机
从未动过 —— 两条路在流里**长得完全一样**。

**词表没有主人。** 全部 action 名只出现在 `transition` 的 docstring 里。契约把它出域成
`list[dict[str, Any]]`，列是 `Column(String(64))`：没有类型、没有约束、没有枚举。拼错一个
action 不会有任何东西发现。

这正是本仓库已经解决过、唯独漏掉这一处的问题：准入结论、确认决策、解读失败**全部由一处
声明派生**，审计事件是唯一一份没派生的。

## 决定

**「审计事件」是一个服务端拥有的领域概念：一次状态变化恰好留下一条带类型的事件，由一处
写出，其动作名来自一份声明。**

1. **一个生产者。** 全仓库产生 `ReportAuditEvent(` 的地方**只有一处**（
   `report_status._write_event`）。API 模块私有的 `_audit` 删除，worker 不再从
   `app.api.report` import 任何审计写入口。
2. **两个入口，按「状态动过没有」分。** 状态变了走 `transition(..., action=...)`，每次
   迁移**恰好一条**事件；状态没变、只是记一笔走 `record_event(...)`，且它的 action 必须
   取自**审计专用**那一组 —— 传一个迁移类取值会被拒。这是本记录要保住的形状：读者必须能
   一眼分辨「状态动过」与「状态没动、只是记了一笔」。
3. **一份词表，两组取值。** `app/service/audit_vocabulary.py` 声明
   `TRANSITION_ACTIONS_ORDERED` 与 `AUDIT_ONLY_ACTIONS_ORDERED`（两者不相交、并集即词表），
   契约的 action 由它 `Literal[*...]` 派生。
4. **detail 在调用处一次给全。** `transition` 承担明细；「写空壳 + 补实体」这种双写消失。
   确认与评估从此各只有一条，`assessed` 的 actor 只有一个取值。
5. **「只有审计、没有迁移」的两件事各自具名。** `uploaded` 与「解析已提交、只差 ack」
   成为**审计专用**事件（`uploaded`、`extraction_committed_unacked`），不再冒充迁移。
   新增第三个：任务指向一份**已经离开 `processing` 的报告**（这一轮什么都没做）——
   它与上一条是两件事，此前共用一个名字。
6. **契约类型化。** `audit_events` 从 `list[dict[str, Any]]` 改为带 `action: AuditActionLiteral`
   的模型；`detail` 刻意保持自由形状（每条事件要带的明细不同，统一模型只会逼出一堆全可选
   的字段）。
7. **不改列形状、不改写历史行。** `action` 列仍是 `String(64)`（沿用报告状态列的做法，词表
   由代码持有）；旧行里那两条曾叫 `status_recovered` 的「状态没动」事件**保留原样** ——
   审计是「发生过什么」的证据，把历史改成新口径是编辑记录而不是描述记录。全部历史写入
   实测只有 8 个取值，都是词表成员，所以读得懂。

## 被否掉的方案

- **只删 `_audit`、把它的调用点全改成 `transition`，词表继续留在 docstring 里。** 这是最小
  改动，也确实消灭了「一次两条」。它输在第二半：`uploaded` 与「已提交未 ack」**没有迁移**，
  硬塞进 `transition` 会退化成一次**假的**状态迁移（把一个没发生的状态变化写进状态机），
  而 action 词表仍然没有类型、拼错仍然没人发现。多写与少走是两个缺陷，只修一个会把另一个
  变成代码里的谎。
- **给 `status_recovered` 加一个布尔字段区分「真迁移」与「只记录」。** 比现状强，但把
  「这两件事不同类」表达成一个**读者需要先知道才看得懂**的字段。两组取值让这件事落在
  **名字**上，读流的人不必先读文档。
- **把词表做成数据库枚举列 / CHECK 约束。** 更硬，但要一次迁移，且历史行与列形状都要动
  （沿用报告状态列的做法是本仓既有取向）。收益与代码侧的词表 + 写入点校验基本相同。
- **给 `detail` 也建模型。** 每条事件的明细本来就不同（抽取轨迹、确认过的指标 id、任务
  id），统一模型会逼出一堆全可选字段 —— 那是把「每条自己的形状」换成「一个谁都不完整的
  形状」。类型化的收益在 `action` 上，因为它是被**判读**的字段，而 detail 是被**查证**的。
- **保留双写一段时间作为兼容期。** 改动更小、可回滚，代价是「一次确认到底发生了几次」
  在契约里同时有两个答案 —— 本记录存在的理由就是那个多答案状态。

## 后果

更容易的：

- 同一件事在审计流里只有一条记录，不必先猜哪一条可信；同一次评估只有一个 actor。
- 「状态被恢复」与「状态从未变过、只记了一笔」在流里长得不一样。
- 新增一条迁移路径时不需要记得在别处补一条同名事件；拼错 action 在写入点与契约校验两处
  被挡住。
- worker 不再依赖 API 模块的私有函数。

更难的、被限制了的：

- **契约变了**：出域的 `audit_events` 数组会变短（一次确认+评估从 4 条降到 2 条），每个
  条目由自由 dict 变成带类型的对象。前端今天不渲染这块（`grep audit frontend/src/` 无命中），
  所以没有界面回归，但这是一个对外契约变化。
- **`record_event` 是一个新的、必须被记得使用的入口**：以后有人想记一笔「状态没动的事」，
  他要先给这件事取个名并加进审计专用那一组 —— 那正是本记录想要的摩擦。
- **历史与当前口径在库里并存**：旧行的 `status_recovered` 与新的
  `extraction_committed_unacked` 说的是同一件事的两个名字。读流的人需要知道这个断代，
  本记录就是那个地方。
- 报告状态**仍然是**「有没有评估过」的唯一答案：审计流依旧不是状态机的一部分，
  `app/api` 不得读它回答业务问题（那条守卫不变）。

## 验证

| 断言 | 结果 |
| --- | --- |
| 改动前实测：一次 confirm+assess | `confirmed` ×2、`assessed` ×2，`assessed` 有 2 个 actor |
| 改动后：一次迁移 | 恰好 1 条，detail 在同一条上 |
| 改动后：`recover_stale_jobs` 那一支 | 报告状态不变；事件是 `extraction_committed_unacked`（审计专用那一组） |
| 全仓库 `ReportAuditEvent(` 构造点 | 1 处（`app/service/report_status.py`） |
| 写入点负控 | `transition(action="confirmd")` 与 `record_event(action="confirmed")` 都抛 |
| 历史行兼容 | 全部历史写入的 8 个取值 ⊆ 词表；旧 `status_recovered` 仍校验通过 |
| `test_report_status.py` 的两条回收用例 | 由 `status_recovered` 改为 `extraction_committed_unacked`（它们编码的正是本次修掉的缺陷） |

**验证边界如实声明**：审计流**不出现在患者界面上**（前端不渲染），因此这不是
`verify-healthflow` 的覆盖面 —— 证据是服务端回放与结构守卫，不是浏览器截图。
