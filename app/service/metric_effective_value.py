"""指标生效值（GLOSSARY.md 的「指标生效值」）。

一条结构化指标在患者核对之后，当前参与解读、展示与异常判定的值是哪一个？
此前这个问题由 ``evidence_bridge.build_observations_with_unmatched`` 里的一段
无名局部规则回答 —— ``metric.confirmed_value or metric.metric_value`` 等四条
回退链。它决定哪些值跨过证据边界，却从未被命名、也没有透出到响应契约，于是前端
只能自己抄一遍（报告单三个列），或者在别处漏抄（确认页指标卡、修正草稿初始化）。

本模块把那条规则收成一个有名字的函数：**修正值 > 确认时落定的 confirmed 值 >
模型抽取值**；``excluded`` 的指标没有生效值。

``source`` 的取值就是**确认决策的名字**，一份词汇两个读法（GLOSSARY.md 的「指标确认
决策」）。它此前把 ``pending`` 改叫 ``extracted`` —— 同一个事实两个名字，于是「按
``source`` 分支」与「按 ``confirmation_status`` 分支」的消费者写出的是同一份逻辑的
两份实现。现在按来源分支就是按决策分支。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.service.confirmation_decision import effective_source
from app.service.confirmation_vocabulary import DECISIONS_ORDERED

# 生效值的来源。它回答「这个值是谁给的」，而答案就是患者对这条指标作的**决策**本身：
#   pending   —— 患者还没处理，模型值是临时生效值
#   confirmed —— 患者确认过但没改（confirmed 列写的就是模型值）
#   corrected —— 患者改过（修正值）
#   excluded  —— 患者明确排除，没有生效值
#
# **派生自决策词表**，不是第二份字面量：两者各写一份就会重新长出
# `pending`/`extracted` 那种「同一事实两个名字」。
EffectiveSource = Literal[*DECISIONS_ORDERED]


@dataclass(frozen=True)
class MetricEffectiveValue:
    """一条指标当前的生效值四元组，以及它是谁给的。"""

    value: str | None
    unit: str | None
    reference_range: str | None
    evidence_text: str | None
    source: EffectiveSource

    @property
    def is_decidable(self) -> bool:
        """有没有生效值可言（``excluded`` 没有）。"""
        return self.source != "excluded"


def effective_value(metric: Any) -> MetricEffectiveValue:
    """全仓库唯一的「当前生效值」解析。

    规则：``confirmed_x or x`` —— 修正/确认过的行取 confirmed 列，否则取模型列。
    确认时 confirmed 列被写成模型值（见 ``app/api/report.py`` 的确认处理器），
    所以「确认但没改」与「还没确认」的取值相同，区别只在 ``source`` 上。

    ``excluded`` 是唯一的例外：患者明确表态不要它参与解读，因此**没有生效值**
    （四元组全为 None）。这不是「取不到值」，而是「这个概念上不存在」。
    """
    status = getattr(metric, "confirmation_status", None)
    if status == "excluded":
        return MetricEffectiveValue(None, None, None, None, "excluded")
    # 来源就是决策本身（`pending` 不再改叫 `extracted`）。走一遍 `effective_source`
    # 让词表外的状态在写路径当场报错，而不是被静默当成 `pending`。
    source: EffectiveSource = effective_source(status)
    return MetricEffectiveValue(
        value=getattr(metric, "confirmed_value", None) or getattr(metric, "metric_value", None),
        unit=getattr(metric, "confirmed_unit", None) or getattr(metric, "unit", None),
        reference_range=(
            getattr(metric, "confirmed_reference_range", None) or getattr(metric, "reference_range", None)
        ),
        evidence_text=(
            getattr(metric, "confirmed_evidence_text", None) or getattr(metric, "evidence_text", None)
        ),
        source=source,
    )
