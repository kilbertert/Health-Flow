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


def displays_as_review(metric: Any) -> bool:
    """这一行**会显示成**「异常」或「待核对」吗 —— 前端 `displayFlag` 的镜像。

    与 `undecided` 的关系见那个函数的说明：`undecided` 是「我们给出判定了吗」（关于判定的
    事实），本函数是「页面上会请他看一眼吗」（关于显示的镜像）。两者在「患者已表态、模型也
    没标异常、又判不出来」这一类上必然分开。

    **它必须逐字镜像 `displayFlag`**，因为它决定页头那个数，而那个数站在**行列表**上方：
    两者分叉就会出现「数字说有 3 项，列表里只有 2 条」。等价性由
    `tests/test_metric_counts.py` 驱动**真实** `displayFlag` 逐行断言 —— 不是靠这段注释。

    镜像的是前端那四条分支（`displayFlag` 与 `admissionText.admissionBeforeAssessment`）：

    - 患者已排除 → 不请他；
    - 判得出来 → 只有异常的那几个请他（正常行不请）；
    - 判不出来、患者**还没表态** → `admissionBeforeAssessment` 给「待核对」，请他。
      注意这一条**不看**原始标记、也不看是不是描述项 —— 前端的顺序就是先给「待核对」；
    - 判不出来、患者**已表态** → 只有模型宣称异常时才显示「待核对」，否则如实显示原始标记。
    """
    if is_excluded(metric):
        return False
    inferred = infer_abnormal_flag_for_metric(metric)
    if inferred is not None:
        return inferred in ABNORMAL_FLAGS
    if (getattr(metric, "confirmation_status", None) or "pending") == "pending":
        return True
    return _raw_claims(getattr(metric, "abnormal_flag", None))


def _raw_claims(raw_flag: str | None) -> bool:
    return str(raw_flag or "").upper() in _RAW_ABNORMAL_MARKERS


def raw_flag_claims_abnormal(raw_flag: str | None) -> bool:
    """抽取模型写下的原始标记是否**宣称**这一行异常。

    这是展示层的纯映射判据，不是异常判定 —— 异常判定只认 `infer_abnormal_flag_for_metric`。
    """
    return _raw_claims(raw_flag)


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
    """这一行要患者看一眼吗（= 会显示成异常或「待核对」）。

    它就是 `displays_as_review` —— 两个名字是因为调用点的读法不同（这里是「要请他处理
    吗」，那里是「会显示成什么」），而它们是同一个判定，不是两份实现。
    """
    return displays_as_review(metric)


def undecided(metric: Any) -> bool:
    """这一行**没有判定**（既不是 H/L/N/A，也不是患者排除）。

    它与 `needs_review` 是**两个不同的问题**，刻意不合并：

    - `undecided` 说的是「我们给出判定了吗」—— 一个**关于判定的事实**。它驱动结论句：
      有它的时候不许说「均在参考区间内」，因为那些行是**没判出来**，不是「在区间内」。
    - `needs_review` 说的是「页面上会请他看一眼吗」—— 一个**关于显示的镜像**。它驱动
      页头那个数。

    两者在「患者已表态、模型也没标异常、又判不出来」这一类上**必然分开**：那一行我们确实
    没判出来（`undecided` 为真，结论句因此不说假话），但页面上它显示的是原始标记而非
    「待核对」，所以不需要再请他一次（`needs_review` 为假）。把它们并成一个数，就会在
    「页头诚实」与「结论句诚实」之间二选一。

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
