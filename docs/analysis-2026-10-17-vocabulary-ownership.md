# 三个 architecture-review issue 背后的同一个问题

日期：2026-10-17 · 范围：#245 / #246 / #247（+ 一条类级不变式）

## 一句话

三个 issue 不是三个 bug，是**一个类**的三个实例：**一个领域事实的含义，由「恰好写了它的
那段代码」携带，而不是由一份所有生产者与消费者共享的声明携带。**

## 症状怎么长出来的

| # | 概念 | 「这个值是什么」由谁定义 | 患者/读者可观察的后果 |
| --- | --- | --- | --- |
| 245 | 报告指标计数 | 三个调用点各自的谓词（`{H,L,A}` / `isAbnormal(displayFlag)` / `needsReview`） | 同一份报告在报告单说「未见异常指标」、在确认页说「N 项需要确认」；一行没判出来的指标让报告单印出「均在参考区间内」这句假话 |
| 246 | 取货为空的原因 | 服务端 `Literal` 三个值 + ADR-0005 的一段散文 + 前端自造的第四个值 | 值取决于「服务端那句话里有没有 3 位数字」；404 被说成「商城不可用」；会话失效被叫去「重新登录」一个不存在的页面 |
| 247 | 审计事件 | 两个互不相识的生产者；action 词表只活在 docstring 里 | 一次确认落 4 条；同一条 `assessed` 有两个 actor；「状态没动、只记了一笔」与「状态动过」长得一样 |

四个后果逐条对应，而且**不是巧合**：

- **N 个生产者 → 漂移**（245、247）。两边都不知道对方会写第二条，也没有任何东西会因为
  它们不一致而失败。
- **契约不承载这个事实 → 前端复算或发明**（245 的 `abnormal_count` 只存在于历史接口、
  246 的第四种取值不在任何 `Literal` 里）。模型是封闭的，而**语言是穷尽的**：写不出一个
  能被校验的取值，就写一个谁都校验不到的字符串。
- **词表住在散文里 → 拼错、漏值无人发现**（247 的 8 个 action 名，0 个类型约束）。
- **守卫是事后手写的、一次一个概念**（`test_report_status_vocabulary.py`、
  `test_admission.py::test_no_second_vocabulary_is_declared_anywhere`、
  `test_mall_goods.py::test_response_contract_matches_produced_reasons`）。
  **覆盖面等于「有人想起来写了一份」** —— 这就是为什么这一类会反复长出来。

三个 issue 的 Problem Statement 最后一段是同一句话：**「GLOSSARY.md 里没有这个词条」**。
概念没有被命名，所以无处收口。

## 量出来的普遍性

改前对 `app/` 下全部 `Literal[...]` 做 AST 扫描：

- 34 处 `Literal`，**只有 8 处是派生的**（`Literal[*X]`），其余 26 处手写；
- 值域**完全相同**的重复声明 **8 组**，其中 `same/different/uncertain` 三处、
  `processing/pending_confirmation/confirmed/assessed/failed` 两处、
  `routine/soon/urgent/emergency` 三处……

也就是说：这三个 issue 是**尚未迁移的实例**，而这个类是**无界的** —— 没有机械判据能发现
新成员，只能靠每天一次的 architecture-review agent 去读代码。三个 issue 各自还在
Out of Scope 里列着 4 个同类的候补（抽取队列状态词表、`{"confirmed","assessed"}` 业务组合、
前端生效值回退链、前端主体一致性闸门）。

## 为什么「修三处」不够

修三处会让今天的三个症状消失，而**发现机制不变**：下一个同类概念仍然要被 agent 读出来，
仍然要等三个 issue 中的某一个。所以本次的做法是：

1. **每一处按「一个概念一个家」收敛**（第 1 步是给概念命名，GLOSSARY + ADR）；
2. **把「一次一个概念的手写守卫」换成一条覆盖全类的机械不变式**；
3. **收敛掉现存的 8 组重复声明**，让它转绿 —— 否则新加的检查一上来就是红的，而一个开局
   就红的门禁会被关掉。

## 修完之后的形状

| 概念 | 家 | 派生关系 |
| --- | --- | --- |
| 审计事件的 action 词表 | `app/service/audit_vocabulary.py` | 契约 `audit_events[].action` 由它派生；两个子集（迁移 / 审计专用）不相交 |
| 报告指标计数 | `app/service/report_metric_counts.py` | 两个契约各带同一个计数块 |
| 取货为空的原因 | `app/service/recommendation_empty_reason.py` | 契约、商城子集、加购契约三处都从它派生 |

三处都是「只有声明、不 import 任何项目模块」的词表模块 + 一个判定/计数模块的结构 ——
与 `admission_vocabulary.py` / `confirmation_vocabulary.py` / `interpretation_failure.py`
同一个形状。这条形状本身是这个仓库已经验证过的。

## 还没做完的（如实列出）

- 类级不变式那条检查尚未落地（见下一条 ADR）。
- 现存 8 组重复声明的收敛尚未落地。
- 三个 issue 各自 Out of Scope 里列的 4 个候补未动。
- **`Auto-Test` 仓库有一个 50MB 的损坏 packfile**（`.git/objects/pack/pack-8dd2f5ff….pack`：
  它自己的 trailer sha 与内容不符，idx 是 8 月 16 日的、pack 是 10 月 15 日 09:12 改写的）。
  同一个时间点，health-flow 有一个 root 所有、被截断的 loose object（已修复）。
  两处都是**宿主级事件**的痕迹，另一个仓库的损坏不在本任务射程内，未动。
