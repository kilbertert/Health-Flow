"""解读准入的词表（GLOSSARY.md 的「解读准入」）。

一条**已解析出的**结构化指标，能不能进入健康风险提示生成？不能的话，**唯一**的原因
是什么？

这是那份词表的家，**且只有这一个家**。价值全在「只有一处」上，所以本模块刻意：

- 不 import 任何项目模块。它必须能被**出域契约**（`app/schema/evidence.py`）导入，
  而契约在最底层 —— 一旦这里依赖了 service 层，导入就成了环（实测：`schema.evidence`
  在模块级 import `service.admission` 会拉起 `service.__init__` → `vision_encoder` →
  `schema.report` → 半初始化的 `schema.evidence`，报 `ImportError`）。
- 只用标准库、只放**声明**，不放函数。判定逻辑在 `app/service/admission.py`，它在
  这份词表之上写字。

此前这个问题由四套互不同步的词汇回答，各自在自己的文件里声明自己认识的名字：证据门禁的
`Skipped.reason`（七值）、同一个投影里的 `Unmatched.reason`（二值、另一套名字）、不出域的
`abnormal_flag_reason`（四值）、以及前端的一句兜底加一处再推导。两个漂移点曾经是「词表
没有家」的物证：`unknown_metric_code` 在 `Skipped` 里**从不产生**，而 `missing_value` 被
兄弟函数产生却**不在** `Skipped` 的词表里 —— 同一个事实在两侧两个名字。
"""

from __future__ import annotations

from typing import Literal, get_args

from app.service import confirmation_vocabulary as confirmation_decisions
from app.service.confirmation_vocabulary import ADMITTED_DECISIONS_ORDERED

# ── 唯一词表 ────────────────────────────────────────────────────────────────
#
# 新增一种准入判定时，名字只加在这里 —— 三个消费点（门禁 / 判定守卫 / 出域契约）
# 都从这里取，不会再有第三个文件声明自己的一套。
AdmissionReason = Literal[
    # 尚未核对与患者已排除：两种显式的准入结论，不是「没有原因」。
    "pending",
    "excluded",
    # 值本身不可用。「还没解析出来」与「不是一个数」是两句不同的话：前者让人去等
    # 解析，后者让人去修正。
    "missing_value",
    "invalid_value",
    # 证据不完备（跨不过证据边界）。
    "missing_unit",
    "missing_source_evidence",
    "missing_source_page",
    # 参考范围缺失 —— 值本身完全正常，修正数值救不了它。
    "missing_reference_range",
    # 判定过了，在参考区间内。这一条是「正常」，**不是**「未能解读」。
    "within_reference_range",
    # 没进解读，但原因是「没有对应的知识卡」而不是「证据不足」。
    "unknown_metric_code",
    "no_published_knowledge_card",
]

# 出域投影的可取值，按投影数组分开。它们是**同一份词表的子集**，不是第二份词表：
# 每一条都由上面派生，`Literal[*SKIPPED_REASONS_ORDERED]` 直接消费它们，所以值集
# 恒等 —— 改词表只改一处。
#
# `unknown_metric_code` 只在 `Unmatched` 里出现。它在 `Skipped` 的 Literal 里待过
# 很久却从不产生，那正是「词表没有家」的物证之一。
#
# 元组而非 frozenset：`Literal[*X]` 的展开顺序应当稳定，而集合的迭代顺序不是。
SKIPPED_REASONS_ORDERED: tuple[str, ...] = (
    "missing_value",
    "invalid_value",
    "missing_unit",
    "missing_source_evidence",
    "missing_source_page",
    "missing_reference_range",
    "within_reference_range",
)
UNMATCHED_REASONS_ORDERED: tuple[str, ...] = ("unknown_metric_code", "no_published_knowledge_card")

# 同一组取值的集合视图，给运行期的成员判断用（门禁把结论分桶）。
SKIPPED_REASONS: frozenset[str] = frozenset(SKIPPED_REASONS_ORDERED)
UNMATCHED_REASONS: frozenset[str] = frozenset(UNMATCHED_REASONS_ORDERED)

# 三桶都看不到的那两类（尚未决定 / 患者已排除）现在有名字了；它们不出现在
# `skipped` / `unmatched` 里，而是随指标行逐条出域（见 PRD #176 的第二张票）。
#
# **这是对确认决策的一个读出，不是第三份抄写**：这两句话由决策词表派生 ——
# 准入说「没进解读」的两个理由，正是决策说「患者没表态」与「患者排除了」的那两个决定。
NOT_EVALUATED_ORDERED: tuple[str, ...] = (
    *confirmation_decisions.UNDECIDED_ORDERED,
    *confirmation_decisions.EXCLUDED_ORDERED,
)
NOT_EVALUATED_REASONS: frozenset[str] = frozenset(NOT_EVALUATED_ORDERED)


# 参与解读的确认状态 —— **派生自「指标确认决策」的那一份声明**，不是手写的第二处。
# 它此前是一个手写集合，而同一个判定在 `evidence_bridge._decidable` 里又写了一遍反向
# 表述（「不等于 excluded」）。正面枚举与反向排除的差别在新增一种决策时才显形：反向那个
# 会默默放行新值。现在两处都读 `confirmation_decision.is_admitted`。
ADMITTED_STATUSES: frozenset[str] = frozenset(ADMITTED_DECISIONS_ORDERED)


# 出域契约用的 `Literal`。`MedicalReportResponse` 的逐行准入结论与报告级台账都从
# 它派生 —— 与 `Skipped` / `Unmatched` 同一个做法：值集恒等靠**派生**，不靠两处抄写。
# `Literal[*X]` 是 Python 3.11+ 的写法；CI 与部署都钉在 3.13。
AdmissionReasonLiteral = Literal[*SKIPPED_REASONS_ORDERED, *UNMATCHED_REASONS_ORDERED, *NOT_EVALUATED_ORDERED]

# 这些原因是「判定过，在参考区间内」—— **正常**，不是「没能进入解读」。它们必须从
# 「未进入解读」那类说法里排除，否则每一份报告都会说「有 N 项未进入解读」而 N 里
# 大半是正常指标，与同一张卡片上方的摘要直接矛盾。
NORMAL_REASONS_ORDERED: tuple[str, ...] = ("within_reference_range",)
NORMAL_REASONS: frozenset[str] = frozenset(NORMAL_REASONS_ORDERED)


def vocabulary() -> frozenset[str]:
    """词表的全部取值。供守卫使用：它必须与各子集的并集完全相等。"""
    return frozenset(get_args(AdmissionReason))
