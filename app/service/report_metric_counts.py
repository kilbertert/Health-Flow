"""报告指标计数（GLOSSARY.md 的「报告指标计数」）。

「这份报告有多少项指标、其中有几项是异常的、有几项要患者处理」是患者界面上重复出现的
一句话，而它此前由**三处各自计算的计数**回答，判据互不同步：

- **历史摘要**（`app/api/auth.py`）数判定为 `{H, L, A}` 的行，渲染成「· N 项异常」；
- **报告单总览**（`frontend/src/pages/ReportDetail.jsx`）自己再数一遍，而且先剔掉
  `excluded` 行，用的是只认 `{H,L,A}` 的 `isAbnormal(displayFlag(...))`；
- **确认页页头**（`frontend/src/pages/Upload.jsx`）数的是**另一个谓词** `needsReview`，
  它**把「待核对」也算进去**。

于是同一次会话里，同一份报告在报告单上说「未见异常指标」并给出结论句「当前报告的指标均在
参考区间内」，在确认页上却在数「其中 1 项需要确认」。

**这不是理论分歧，是一条可达的行。** 模型标了 `H`、而值是一个解析不出单值的行
（`two_values`）：`inferred_abnormal_flag` 为空、`displayFlag` 落「待核对」。这一行不计入
历史摘要、不计入报告单的「异常指标 N 项」（它还会把 `abnormalMetrics.length` 压到 0，
于是报告单**印出一句假话**「当前报告的指标均在参考区间内」），却计入确认页的
「其中 N 项需要确认」。

本模块是那三个数的**唯一**产生处：每个计数各有唯一判据，由服务端算好、随响应一起出域，
患者侧只渲染。

## 每个计数各自的名字与判据

- ``parsed`` —— **已解析行数**：这一份报告解析出了多少行。它是另外三个数的分母。
- ``abnormal`` —— **判定为异常的行数**：``infer_abnormal_flag_for_metric`` 为 ``H``/``L``/
  ``A`` 的行。判据是「异常判定」词条本身（``A`` 是分类阳性，也是异常，不能漏 —— ADR-0014）。
- ``needs_review`` —— **需要患者处理的行数**：异常的行，**加上**「判定不出来、但抽取模型
  宣称它异常」的行（后者显示为「待核对」）。这是**另一个谓词**，不是 ``abnormal`` 的
  第二种读法：一行可以是 `abnormal` 而 `needs_review` 也为真，也可以只落在后者。
- ``undecided`` —— **还没有判定的行数**：``infer_abnormal_flag_for_metric`` 为空、且患者
  没有排除它的行。它回答的是「这份报告里有没有我们没说清的行」，因此**结论句以它为前提**：
  有它的时候不许宣告「均在参考区间内」——那是一句假话，那些行是**没判出来**，不是「在区间内」。

``undecided`` 与 ``needs_review`` 刻意分开：一行 `two_values` 同时落进两桶（它没判出来、
也要患者处理），而一行「模型没标异常、也没有参考范围」只落进 ``undecided``（没判出来，
但没什么可让患者做的）。把它们并成一个数会让结论句重新有机会说谎。

判据本身**不在这里重新定义**：``abnormal`` 调 ``infer_abnormal_flag_for_metric``，
``needs_review`` 复用「异常判定」与原始标记的既有名字。本模块只负责**数**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.service.evidence_bridge import infer_abnormal_flag_for_metric

#: 判定为异常的取值。`A` 是分类结果的阳性（`Detected`）—— 它同样是异常，只是没有方向，
#: 数漏它会让一个阳性检验在摘要里消失（#236）。
ABNORMAL_FLAGS: frozenset[str] = frozenset({"H", "L", "A"})

#: 抽取模型写下的原始异常标记里，哪些算「它宣称这一行异常」。
#:
#: 这一条与前端 `Upload.jsx` 的 `isAbnormal` / `admissionText.js` 的 `isAbnormalLike`
#: 是**同一个判定**（后者对全角/小写一视同仁，因为抽取模型两种写法都产出过）。它只在一个
#: 位置被用到：判定为空、患者又没排除这一行时，决定它是不是「待核对」。两侧一致由
#: `tests/test_metric_counts.py` 的跨语言断言钉住。
_RAW_ABNORMAL_MARKERS: frozenset[str] = frozenset({"H", "HIGH", "高", "L", "LOW", "低", "A", "*"})


def raw_flag_claims_abnormal(raw_flag: str | None) -> bool:
    """抽取模型写下的原始标记是否**宣称**这一行异常。

    这是展示层的纯映射判据，不是异常判定 —— 异常判定只认 `infer_abnormal_flag_for_metric`。
    """
    return str(raw_flag or "").upper() in _RAW_ABNORMAL_MARKERS


@dataclass(frozen=True)
class MetricCounts:
    """一份报告的患者可见计数。四个数各有唯一判据，见模块 docstring。"""

    parsed: int
    abnormal: int
    needs_review: int
    undecided: int


def is_excluded(metric: Any) -> bool:
    """患者明确排除了这一行 —— 它既不异常、也不需要他再处理一次。

    判据取自「指标确认决策」词表的唯一入口，不在这里自带一份「不等于 excluded」的反向表述。
    """
    from app.service.confirmation_decision import is_excluded as _is_excluded

    return _is_excluded(getattr(metric, "confirmation_status", None))


def needs_review(metric: Any) -> bool:
    """这一行要患者看一眼吗（= 异常，或判定不出来但模型宣称异常）。

    患者已排除的行**不再需要他处理** —— 他已经表过态了。这条排除是必须的：一行被排除后
    原始标记仍是 `H`，把它继续算作「待处理」会让确认页反复请他处理同一行。
    """
    if is_excluded(metric):
        return False
    inferred = infer_abnormal_flag_for_metric(metric)
    if inferred in ABNORMAL_FLAGS:
        return True
    if inferred is None:
        # 判不出来，而模型宣称它异常：这正是「待核对」那一类（#129 要保住的场景）。
        return raw_flag_claims_abnormal(getattr(metric, "abnormal_flag", None))
    return False


def undecided(metric: Any) -> bool:
    """这一行还没有判定（既不是 H/L/N/A，也不是患者排除）。

    历史行里 `abnormal_flag` 可能存着 `A` 而没有可解析的范围；那也判不出来，同样落在这里
    ——「没判出来」是一个诚实的回答，不必猜。
    """
    if is_excluded(metric):
        return False
    return infer_abnormal_flag_for_metric(metric) is None


def metric_counts(metrics: list[Any]) -> MetricCounts:
    """**全仓库唯一**给出这四个数的地方。"""
    return MetricCounts(
        parsed=len(metrics),
        abnormal=sum(1 for metric in metrics if infer_abnormal_flag_for_metric(metric) in ABNORMAL_FLAGS),
        needs_review=sum(1 for metric in metrics if needs_review(metric)),
        undecided=sum(1 for metric in metrics if undecided(metric)),
    )
