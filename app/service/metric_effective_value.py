"""指标生效值（GLOSSARY.md 的「指标生效值」）。

一条结构化指标在患者核对之后，当前参与解读、展示与异常判定的值是哪一个？
此前这个问题由 ``evidence_bridge.build_observations_with_unmatched`` 里的一段
无名局部规则回答 —— ``metric.confirmed_value or metric.metric_value`` 等四条
回退链。它决定哪些值跨过证据边界，却从未被命名、也没有透出到响应契约，于是前端
只能自己抄一遍（报告单三个列），或者在别处漏抄（确认页指标卡、修正草稿初始化）。

本模块把那条规则收成一个有名字的函数：**修正值 > 确认时落定的 confirmed 值 >
模型抽取值**；``excluded`` 的指标没有生效值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# 生效值的来源。它回答「这个值是谁给的」，供展示与审计区分：
#   corrected —— 患者改过（修正值）
#   confirmed —— 患者确认过但没改（confirmed 列写的就是模型值）
#   extracted —— 患者还没处理（pending），模型值是临时生效值
#   excluded  —— 患者明确排除，没有生效值
EffectiveSource = Literal["corrected", "confirmed", "extracted", "excluded"]


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
    source: EffectiveSource = (
        "corrected" if status == "corrected" else "confirmed" if status == "confirmed" else "extracted"
    )
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
