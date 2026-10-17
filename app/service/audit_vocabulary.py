"""审计事件的 action 词表（GLOSSARY.md 的「审计事件」）。

「这份报告发生过什么」由审计流回答。这条流此前**没有家**：两个互不相识的生产者各自
写同一个 ``ReportAuditEvent`` 表、用同一批 action 名字，而**全部 action 名只出现在
``report_status.transition`` 的 docstring 里** —— 契约把它出域成
``list[dict[str, Any]]``，列是 ``Column(String(64))``。没有类型、没有约束、没有枚举，
拼错一个 action 不会有任何东西发现。

本模块是那份词表的家，**且只有这一个家**。与 ``admission_vocabulary.py`` /
``confirmation_vocabulary.py`` 同样的约束：

- 不 import 任何项目模块。它必须能被**出域契约**（``app/schema/report.py``）导入，
  而契约在最底层 —— 一旦这里依赖了 service 层，导入就成了环（先例见
  ``admission_vocabulary.py`` 的模块 docstring，那条教训是实测来的）。
- 只用标准库、只放**声明**，不放函数。写入在 ``app/service/report_status.py``。

## 两个子集，不是一个词表的两种读法

事件按「报告状态动过没有」分成两组，**这件事必须能被一眼分辨**：

- ``TRANSITION_ACTIONS_ORDERED`` —— 伴随一次状态迁移发生的事件。它们由
  ``report_status.transition()`` 在**同一次事务里**写下，每次迁移**恰好一条**。
- ``AUDIT_ONLY_ACTIONS_ORDERED`` —— 报告状态**没有动**，只是记了一笔。它们由
  ``report_status.record_event()`` 写下。

此前这两组**同形**：``uploaded`` 与「解析已提交、任务未 ack 的恢复」都写成一条与
迁移事件长得一样的事件，于是审计流断言了一次「状态被恢复」，而状态机从未动过 ——
读的人无法分辨「状态动过」与「状态没动、只是记了一笔」。那正是本词表存在的理由。

``status_recovered`` **留在迁移那一组**：worker 把卡住的任务放回队列时，它确实迁了
状态（``→ processing``）。它与审计专用那两条同名过一次，是本记录要消灭的形状；改名
之后历史行里的旧值仍然是词表成员，所以契约读得懂（见下）。
"""

from __future__ import annotations

from typing import Literal, get_args

# ── 唯一词表 ────────────────────────────────────────────────────────────────
#
# 新增一条审计事件时，名字只加在这里 —— 写入点与出域契约都从这里取。
#
# 元组而非 frozenset：`Literal[*X]` 的展开顺序应当稳定，而集合的迭代顺序不是。
TRANSITION_ACTIONS_ORDERED: tuple[str, ...] = (
    # 上传后第一次解析：部分文件成功 / 全部成功 / 全部失败 / 放回队列重试。
    "extraction_partial",
    "extraction_completed",
    "extraction_failed",
    "extraction_retry_queued",
    # 患者确认指标；评估产出健康风险提示。
    "confirmed",
    "assessed",
    # 卡住的抽取任务被回收、报告状态被迁回 processing 重新排队。
    "status_recovered",
)

# 报告状态**没有动**的那些事实。它们各自有自己的名字，不与迁移事件同形。
#
# 历史行里这两条曾经写成 `status_recovered`（与真的状态迁移同名）—— 那个名字仍是
# 词表成员（`status_recovered` 在迁移那一组里继续被 worker 的重排队路径使用），所以
# 既有行照旧读得懂，不需要改写记录：审计是「发生过什么」的证据，把历史改成新口径是
# 编辑记录而不是描述记录。
AUDIT_ONLY_ACTIONS_ORDERED: tuple[str, ...] = (
    # 一份报告被接收（文件已落库、抽取任务已入队）。**不是**一次迁移：状态在对象
    # 落库前就由一次不带 `db` 的 `transition` 设成了 processing，那时还没有 id 可写
    # 审计。所以这一笔在 flush 之后单独记，且它记的不是「状态变了」。
    "uploaded",
    # 解析**已经提交**（报告的指标行已落库、报告已离开 processing），只是 worker 在
    # ack 任务之前重启了 —— 回收时把任务标完成、不入队。报告的状态没有被碰过。
    "extraction_committed_unacked",
    # 一个抽取任务指向一份**已经离开 processing 的报告**（患者已确认或评估过），
    # 于是这一轮什么都不解析、任务直接 ack。这一轮没有产出任何东西 —— 与上一条
    # 「解析已提交、只差 ack」是两件事，读的人要能分开。
    "extraction_dropped_for_settled_report",
)

AUDIT_ACTIONS_ORDERED: tuple[str, ...] = (*TRANSITION_ACTIONS_ORDERED, *AUDIT_ONLY_ACTIONS_ORDERED)

TRANSITION_ACTIONS: frozenset[str] = frozenset(TRANSITION_ACTIONS_ORDERED)
AUDIT_ONLY_ACTIONS: frozenset[str] = frozenset(AUDIT_ONLY_ACTIONS_ORDERED)
AUDIT_ACTIONS: frozenset[str] = frozenset(AUDIT_ACTIONS_ORDERED)

#: 一次审计事件的 action。取值与上面那份声明**派生**、不抄写 —— 出域契约
#: （`app/schema/report.py` 的 `ReportAuditEventRecord`）直接消费它，所以两侧不可能漂移。
AuditAction = Literal[*AUDIT_ACTIONS_ORDERED]

#: 出域契约用的 `Literal`。`Literal[*X]` 是 Python 3.11+ 的写法；CI 与部署都钉在 3.13。
AuditActionLiteral = Literal[*AUDIT_ACTIONS_ORDERED]


def vocabulary() -> frozenset[str]:
    """词表的全部取值。供守卫使用：它必须与两个子集的并集完全相等。"""
    return frozenset(get_args(AuditAction))
