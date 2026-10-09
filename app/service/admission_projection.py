"""把「解读准入」的结论**逐行送到患者侧**（GLOSSARY.md 的「解读准入」）。

`app/service/admission.py` 是那份判定的家。本模块负责另一半：**回答可以出域的那两份
形状** —— 逐行的结论，以及报告级的台账。它在 `app/api` 与 `app/service` 之上都没有
别的职责，也不决定患者看到什么字（那是前端与 `build_patient_notices` 的事）。

为什么需要**两份**形状，而不是一个字段就够：

- **逐行**（`MetricRecord.admission_reason`）回答「这一行为什么没进解读」。它的 `None`
  表示服务端没有拦下这一行。
- **报告级**（`MedicalReportResponse.admission`）回答「这份报告里各类各有多少行」。
  逐行那个 `None` 兼了两件事 —— 「成功进入解读」与「这份报告还没跑过评估」—— 而前端
  要区分它们才能说对的话。所以台账是报告级的**唯一**答案：`assessed_at` 为空时它没有
  意义，前端据此显示「还没有准入结论」。

一份报告里四个桶的和必须等于已解析行数。这是「全量性」这条硬指标的落点：此前
`pending`（尚未核对）与 `excluded`（患者已排除）的行落在三桶之外，于是「三桶相加 !=
行数」永远成立，却没有任何东西会因此报错。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.schema.report import AdmissionLedger
from app.service.admission import admission_reason, tally
from app.service.admission_vocabulary import ADMITTED_STATUSES
from app.service.evidence_bridge import resolve_metric_code


def metric_reasons(metrics: list[Any], *, catalog: Iterable[str] | None = None) -> list[str | None]:
    """逐行的准入结论，顺序与输入一致。``None`` = 服务端没有拦下这一行。

    编码的解析规则与证据门禁**一致**（`resolve_metric_code`）：落定的编码优先，为空时
    在权威目录**可用**的前提下重新裁决一次。传 ``catalog=None`` 时（目录不可用的降级
    路径）不猜 —— 与门禁同一条规矩。

    为什么要与门禁用同一条规则：两份形状回答的是同一个问题（「这一行进没进解读」），
    规则一旦分叉，台账的 `unmatched` 会成为一个既不等于 `unmatched` 数组、也不等于
    `skipped` 数组的**第三个数** —— 读的人无法判断该信哪个。此前这里只看落定的编码，
    于是「确认那刻目录不可用、评估时目录恢复」的行会同时显示「没有可对应的编码」并被
    实际送进了匹配。
    """
    reasons: list[str | None] = []
    for metric in metrics:
        code = getattr(metric, "metric_code", None)
        if not code and catalog is not None:
            code = resolve_metric_code(getattr(metric, "metric_name", "") or "", catalog)
        reasons.append(admission_reason(metric, code=code))
    return reasons


def ledger(reasons: list[str | None]) -> AdmissionLedger:
    """把逐行结论汇成报告级台账。四类之和由这里算一次，前端不再求和。

    它**不**带时间戳，也不带任何「有没有跑过评估」的旁证：那份信息由报告状态
    （`assessed`）给出，而状态在 GLOSSARY 里已经是单一事实来源。这里再盖一个时间戳，
    等于给同一个问题留第二个答案 —— 两者一旦不一致，读的人无法判断该信哪个。
    所以「还没评估」用 `None` 表达（没有台账），不用「一份全 0 的台账」表达。
    """
    counts = tally(reasons)
    return AdmissionLedger(
        included=counts.included,
        normal=counts.normal,
        skipped=counts.skipped,
        unmatched=counts.unmatched,
        not_evaluated=counts.not_evaluated,
        total=counts.total,
    )


def metrics_with_reasons(
    metrics: list[Any], *, assessed: bool, catalog: Iterable[str] | None = None
) -> list[tuple[Any, str | None]]:
    """给逐行契约用：``(数据库行, 准入结论)`` 的配对。

    只在 ``assessed`` 时给结论 —— 「还没评估」与「进入了解读」是两个不同的答案，
    而逐行字段只有一个 `None` 能表达「没有拦下」。区分它们的责任在报告级台账上，
    所以这里不硬塞一个词进去。
    """
    reasons = metric_reasons(metrics, catalog=catalog) if assessed else [None] * len(metrics)
    return list(zip(metrics, reasons, strict=True))


def admission_shapes(
    metrics: list[Any],
    *,
    assessed: bool,
    catalog: Iterable[str] | None = None,
) -> tuple[list[tuple[Any, str | None]], AdmissionLedger | None]:
    """一次给出两份出域形状：逐行结论与报告级台账。

    **还没评估时台账是 `None`，不是一份全 0 的台账。** 全 0 与「没有一行被拦下」在
    数值上一模一样，靠旁证（比如一个时间戳）去区分它们等于给同一个问题留第二个答案
    —— 报告状态 `assessed` 才是那个答案的唯一来源（GLOSSARY 的「报告状态」）。

    逐行结论同理：那时没有任何结论可言，写一个 `pending` 会把「患者还没核对那一行」
    与「整份报告还没评估」混成同一个词。
    """
    pairs = metrics_with_reasons(metrics, assessed=assessed, catalog=catalog)
    if not assessed:
        return pairs, None
    return pairs, ledger([reason for _, reason in pairs])


def has_conclusion(report_status: str | None) -> bool:
    """这份报告是否已经跑过评估 —— 逐行结论与台账都以它为前提。

    `assessed` 是唯一产生准入结论的状态（见 `app/service/report_status.py`）。往前
    一步的 `confirmed` 还没有结论，往后一步的 `failed` 也不该有。
    """
    return (report_status or "") == "assessed"


def admitted(metric: Any) -> bool:
    """这一行会不会进入解读（按状态判断，不解析编码）。

    供调用方在**没有目录**时也能回答「这条是不是本来该被送出去」—— 例如日志与审计。
    它不替代 `admission_reason`：真正的结论要看证据完备性与编码。
    """
    return (getattr(metric, "confirmation_status", None) or "pending") in ADMITTED_STATUSES
