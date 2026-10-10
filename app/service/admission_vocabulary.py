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
    # 患者已排除：一个表态，也是结论本身。
    "excluded",
    # **可判、但患者还没核对**：值、单位、参考范围、原文证据都齐，这一行判得出 H/L/N，
    # 缺的只是患者那一次核对。它此前叫 `pending` —— 与「指标确认决策」词表里那个
    # `pending`（患者的表态：还没决定）同名，于是两个正交的概念在数据上不可分辨：
    # **患者从来没打开过的正常行**与**判不了的行**拿到同一个词，而两者的处置相反
    # （前者不用他做任何事，后者必须他处理）。改名之后，准入说的这句「可判、待核对」
    # 与决策说的「尚未决定」不再互相冒充 —— 见 `NOT_EVALUATED_ORDERED` 的说明。
    "awaiting_confirmation",
    # 值本身不可用。「还没解析出来」与「不是一个数」是两句不同的话：前者让人去等
    # 解析，后者让人去修正。
    "missing_value",
    "invalid_value",
    # 同一行里出现了**两个来源不同的数值**（页面上印了一行、患者又手写一行，OCR 如实
    # 转录了两个）。它不是「数值读不出来」：两个数都在，系统缺的是「哪个才是这一项的
    # 当前值」这个判断，而那个判断只有患者有（#204）。名字分开的理由与 `awaiting_
    # confirmation` 一样 —— 压进 `invalid_value` 会让页面问一个他已经答过的问题
    # （「这条的数值是多少」），而他真正要做的选择是「用哪个」。
    "two_values",
    # 证据不完备（跨不过证据边界）。
    "missing_unit",
    "missing_source_evidence",
    "missing_source_page",
    # 参考范围缺失 —— 值本身完全正常，修正数值救不了它。
    "missing_reference_range",
    # 判定过了，在参考区间内。这一条是「正常」，**不是**「未能解读」。
    "within_reference_range",
<<<<<<< HEAD
    # 这一项**没有「是否异常」这个概念**：描述项（血型、尿液外观与透明度、检验日期这类
    # 记录项）以及判据是比值/阈值的项（比值型由分子分母决定，本来就没有自己的区间）。
    # 它不是「判不了」，也不是「值坏了」，而是这个判断对它所问的问题不存在（#205/#206）。
    # 患者既不用为它表态，它也不进解读。
    #
    # 与 `missing_reference_range` 的分别：那一条说的是「本该有判据而系统没有」，这一条
    # 说的是「这一项本来就没有判据这回事」—— 前者是可修的，后者不是。与
    # `missing_value` / `invalid_value` 的分别是患者的**动作**不同：那两条要他去等解析或
    # 重新给一个值；定性项的值本来就是一个词，系统读得出它，只是不需要它参与判定。
=======
    # 这一项**没有「是否异常」这个概念**（血型、尿液外观与透明度、检验日期这类描述或
    # 记录项）：它不是「判不了」，也不是「值坏了」，而是这个判断对它所问的问题不存在
    # （#206）。患者既不用为它表态，它也不进解读。
    #
    # 与 `missing_reference_range` 的分别：那一条说的是「本该有判据而系统没有」，这一条
    # 说的是「这一项本来就没有判据这回事」—— 前者是可修的，后者不是。
>>>>>>> origin/main
    "no_reference_concept",
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
    "two_values",
    "missing_unit",
    "missing_source_evidence",
    "missing_source_page",
    "missing_reference_range",
    "within_reference_range",
    "no_reference_concept",
)
UNMATCHED_REASONS_ORDERED: tuple[str, ...] = ("unknown_metric_code", "no_published_knowledge_card")

# 同一组取值的集合视图，给运行期的成员判断用（门禁把结论分桶）。
SKIPPED_REASONS: frozenset[str] = frozenset(SKIPPED_REASONS_ORDERED)
UNMATCHED_REASONS: frozenset[str] = frozenset(UNMATCHED_REASONS_ORDERED)

# 三桶都看不到的那两类（**可判、但患者还没核对** / 患者已排除）现在有名字了；它们不
# 出现在 `skipped` / `unmatched` 里，而是随指标行逐条出域（见 PRD #176 的第二张票）。
#
# **这是对确认决策的一个读出，不是第三份抄写** —— 但两处名字不同名，且**刻意**不同名：
# 准入说的 `awaiting_confirmation` 是「这一行判得出，缺的只是患者那一次核对」，决策说的
# `pending` 是「患者还没就它表态」。它们是同一件事的两个视角（一个讲这一行的可判性，一个
# 讲患者的动作），而不是同一个问题在两处判定。此前两者共用一个字符串 `pending`，于是
# 「没看过的正常行」与「判不了的行」在数据上不可分辨 —— 本票把那个同名拆开。
#
# 「患者已排除」没有这个问题：它是一个表态，`excluded` 在两处说的是同一件事。
NOT_EVALUATED_ORDERED: tuple[str, ...] = (*confirmation_decisions.EXCLUDED_ORDERED, "awaiting_confirmation")
NOT_EVALUATED_REASONS: frozenset[str] = frozenset(NOT_EVALUATED_ORDERED)


# 参与解读的确认状态 —— **派生自「指标确认决策」的那一份声明**，不是手写的第二处。
# 它此前是一个手写集合；同一个判定在 `evidence_bridge._decidable` 里另有一份反向表述
# （「不等于 excluded」），两者在**新增一种决策**时才给出不同答案：反向那个会默默放行。
# 现在两份表述都从决策词表取（`ADMITTED_DECISIONS_ORDERED` 与
# `confirmation_decision.is_excluded`）—— 注意它们**不是**同一个判定：`_decidable` 还要为
# `pending` 作答（确认页要患者看到异常候选），所以那是两个问题、两份真值，但**没有第二份
# 词表**。
ADMITTED_STATUSES: frozenset[str] = frozenset(ADMITTED_DECISIONS_ORDERED)


# 出域契约用的 `Literal`。`MedicalReportResponse` 的逐行准入结论与报告级台账都从
# 它派生 —— 与 `Skipped` / `Unmatched` 同一个做法：值集恒等靠**派生**，不靠两处抄写。
# `Literal[*X]` 是 Python 3.11+ 的写法；CI 与部署都钉在 3.13。
AdmissionReasonLiteral = Literal[*SKIPPED_REASONS_ORDERED, *UNMATCHED_REASONS_ORDERED, *NOT_EVALUATED_ORDERED]

# 「判定过、在参考区间内」—— **正常**，不是「没能进入解读」。它必须从「未进入解读」那类
# 说法里排除，否则每一份报告都会说「有 N 项未进入解读」而 N 里大半是正常指标，与同一张
# 卡片上方的摘要直接矛盾。
#
# `awaiting_confirmation` **不**在这一桶：它判得出正常，但**患者还没核对过**，所以它确实
# 没有参与解读（台账的 `not_evaluated` 收它）。两者在报告单上是两句不同的话：一句是「都
# 在参考区间内」（结论已给，无需动作），一句是「尚未核对，未参与解读」（等他核对）。
# 「这一项没有异常概念」与「判定过、在区间内」是**两句不同的话**，所以分两桶（报告单上
# 一句是「均在参考区间内」，另一句是「N 项没有异常概念，未参与解读」）。它们同属
# `NO_ACTION_REASONS` —— 都不需要患者做任何事。
NORMAL_REASONS_ORDERED: tuple[str, ...] = ("within_reference_range",)
NORMAL_REASONS: frozenset[str] = frozenset(NORMAL_REASONS_ORDERED)

# 没有异常概念的项：同样不必患者做任何事，但它**不是**「正常」—— 说「正常」是在一项
# 根本没测的东西上下了结论。它单列，供报告单说明用。
NO_CONCEPT_REASONS_ORDERED: tuple[str, ...] = ("no_reference_concept",)
NO_CONCEPT_REASONS: frozenset[str] = frozenset(NO_CONCEPT_REASONS_ORDERED)

# 判定过、且**患者不必为它做任何事**的两条结论。它们与 `skipped` 的差别是：那一条说的是
# 「这一行没能进入解读」，而这两条说的是「这一行没问题」（正常 / 可判到只差他核对一次）。
# 逐行的橙色提示与「待处理」集合都按这一桶排除，否则报告单上每一行都挂着一条结论。
#
# 与前端 `NO_ACTION_REASONS` 同一份口径（由 `test_admission` 的守卫钉住两者相等）：
# 分叉会让同一行在两处得到不同的说法，那正是本 PRD 要消灭的形状。
NO_ACTION_REASONS_ORDERED: tuple[str, ...] = (
    *NORMAL_REASONS_ORDERED,
    *NO_CONCEPT_REASONS_ORDERED,
    "awaiting_confirmation",
)
NO_ACTION_REASONS: frozenset[str] = frozenset(NO_ACTION_REASONS_ORDERED)


def vocabulary() -> frozenset[str]:
    """词表的全部取值。供守卫使用：它必须与各子集的并集完全相等。"""
    return frozenset(get_args(AdmissionReason))
