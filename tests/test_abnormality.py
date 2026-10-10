"""异常判定（GLOSSARY.md 的「异常判定」）的边界形态。

本文件钉住的是**判定规则本身**，不是它的调用方：一条指标当前是偏高 H、偏低 L
还是正常 N，由「当前最佳值 × 当前参考范围」确定性推断；输入不足以判定时返回空，
不猜测。

先例：`test_reference_range_deterministically_normalizes_abnormality` 曾在
`tests/test_report_confirmation.py` 里钉过同一批边界，该用例已在 #117（账号体系
退役）中被删除。本文件把那份回归保护重建起来，并覆盖 #90 收敛后的**唯一入口**
`infer_abnormal_flag_for_metric`（确认值优先、确认参考范围优先）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.service.evidence_bridge import (
    abnormal_flag_reason,
    infer_abnormal_flag,
    infer_abnormal_flag_for_metric,
)


def _metric(**overrides):
    """一条指标行（ORM 行的最小替身：判定只读这几个属性）。"""
    fields = {
        "metric_value": None,
        "reference_range": None,
        "confirmed_value": None,
        "confirmed_reference_range": None,
        "confirmation_status": "confirmed",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


# ---------------------------------------------------------------------------
# 边界形态：值 × 参考范围 → H / L / N / 空
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "reference", "expected"),
    [
        # 区间
        ("5.2", "3.9-6.1", "N"),
        ("6.2", "3.9-6.1", "H"),
        ("3.5", "3.9-6.1", "L"),
        ("3.9", "3.9-6.1", "N"),  # 边界值含在范围内
        ("6.1", "3.9-6.1", "N"),
        # 只有一个上限
        ("5.5", "<5.2", "H"),
        ("5.0", "<5.2", "N"),
        ("5.0", "≤5.2", "N"),
        # 只有一个下限
        ("1.50", ">1.00", "N"),
        ("0.90", ">1.00", "L"),
        ("1.00", "≥1.00", "N"),
        # 值本身带比较符 —— 不可判定，不猜测
        ("<20", "<20", None),
        (">20", "<20", None),
        ("≤5.2", "3.9-6.1", None),
        # 多数字值（如「6.5/7.2」这种未拆分形态）—— 不可判定
        ("6.5/7.2", "3.9-6.1", None),
        # 参考范围缺失或无法解析 —— 不可判定
        ("5.2", None, None),
        ("5.2", "", None),
        ("5.2", "阴性", None),
        ("5.2", "见报告", None),
        # 值缺失 —— 不可判定
        ("", "3.9-6.1", None),
        (None, "3.9-6.1", None),
        # 前后空白不影响判定
        (" 5.2 ", " 3.9-6.1 ", "N"),
        # 中文分隔符
        ("5.2", "3.9至6.1", "N"),
        ("5.2", "3.9~6.1", "N"),
    ],
)
def test_reference_range_deterministically_decides_abnormality(value, reference, expected):
    assert infer_abnormal_flag(value, reference) == expected


# ---------------------------------------------------------------------------
# 唯一入口：输入优先级（确认值/确认范围优先）
# ---------------------------------------------------------------------------

def test_confirmed_value_and_range_win_over_model_values():
    """患者修正过的值参与判定，而不是模型抽取值。"""
    metric = _metric(
        metric_value="6.5",
        reference_range="3.9-6.1",
        confirmed_value="5.5",
        confirmed_reference_range="3.9-6.1",
    )
    assert infer_abnormal_flag_for_metric(metric) == "N"

    # 参考范围同理：模型范围判 H，患者确认的范围判 N。
    metric = _metric(
        metric_value="5.5",
        reference_range="3.9-5.0",
        confirmed_reference_range="3.9-6.1",
    )
    assert infer_abnormal_flag_for_metric(metric) == "N"


def test_confirmed_range_alone_is_enough():
    """只有确认范围、没有确认值时，仍按模型值 + 确认范围判定。"""
    metric = _metric(metric_value="6.5", confirmed_reference_range="3.9-6.1")
    assert infer_abnormal_flag_for_metric(metric) == "H"


def test_model_flag_is_ignored_by_inference():
    """抽取模型写下的原始 abnormal_flag 不参与判定（判定覆盖它）。"""
    # 模型标 H，但数值在参考范围内 —— 判定为 N。
    assert infer_abnormal_flag_for_metric(_metric(metric_value="5.2", reference_range="3.9-6.1")) == "N"
    # 模型什么都没标，但数值超范围 —— 判定为 H。
    assert infer_abnormal_flag_for_metric(_metric(metric_value="6.5", reference_range="3.9-6.1")) == "H"


# ---------------------------------------------------------------------------
# 不可判定时给出原因，且原因词汇与证据门禁同源
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("metric", "expected_reason"),
    [
        (_metric(metric_value="", reference_range="3.9-6.1"), "missing_value"),
        (_metric(metric_value="<20", reference_range="3.9-6.1"), "invalid_value"),
        # 两个数都在（#204）：值这一类，但原因的名字说的是「选一个」而不是「修一个」。
        (_metric(metric_value="6.5/7.2", reference_range="3.9-6.1"), "two_values"),
        (_metric(metric_value="5.2", reference_range=None), "missing_reference_range"),
        (_metric(metric_value="5.2", reference_range="阴性"), "missing_reference_range"),
    ],
)
def test_undecidable_rows_carry_a_reason_from_the_shared_vocabulary(metric, expected_reason):
    assert abnormal_flag_reason(metric) == expected_reason
    assert infer_abnormal_flag_for_metric(metric) is None


def test_decidable_rows_have_no_reason():
    assert abnormal_flag_reason(_metric(metric_value="5.2", reference_range="3.9-6.1")) is None


# ---------------------------------------------------------------------------
# 契约一致性：响应里算出的判定与证据门禁用的是同一个答案
# ---------------------------------------------------------------------------

def test_response_flag_and_evidence_gate_agree_on_the_same_row():
    """`inferred_abnormal_flag` 与门禁的跳过理由是同一个判定。

    门禁只跳过「判定为 N」的行（reason `within_reference_range`）；判成 H/L 的
    行会继续往下走（可能因缺编码等原因落到 unmatched），而本字段给出的是 H/L。
    所以两者对「是否在参考范围内」必须给出一致答案——这是 #90 的验收线之一。
    """
    from app.data.models import MetricRecord as MetricModel
    from app.service.evidence_bridge import build_observations_with_unmatched

    def row(value: str, reference: str):
        return MetricModel(
            id=1,
            report_id=1,
            metric_name="某指标",
            metric_value=value,
            unit="mmol/L",
            reference_range=reference,
            page_number=1,
            evidence_text=f"某指标 {value} mmol/L ({reference})",
            source_file_index=1,
            confirmation_status="confirmed",
            metric_code="fasting_glucose",
        )

    for value, reference in (("5.2", "3.9-6.1"), ("6.5", "3.9-6.1"), ("3.5", "3.9-6.1")):
        metric = row(value, reference)
        flag = infer_abnormal_flag_for_metric(metric)
        _, skipped, _ = build_observations_with_unmatched([metric])
        reasons = {item["reason"] for item in skipped}
        assert (flag == "N") == ("within_reference_range" in reasons), (value, reference, flag, reasons)


# ---------------------------------------------------------------------------
# 只对「已核对过」的行判定：pending / excluded 不进入异常口径
# ---------------------------------------------------------------------------

def test_excluded_rows_are_not_decided_and_pending_rows_still_are():
    """患者排除的指标不进入异常口径；还没确认的指标仍然进入。

    排除：门禁的入口守卫不处理 excluded 的行，解读看不到它，摘要也就不该为它计数。
    保留 pending：一份待确认的报告，患者在确认页上必须看到模型标出的异常候选 ——
    把 pending 一起排除，报告在确认前就会显示「未见异常指标」。
    """
    excluded = _metric(metric_value="6.5", reference_range="3.9-6.1", confirmation_status="excluded")
    assert infer_abnormal_flag_for_metric(excluded) is None
    assert abnormal_flag_reason(excluded) == "not_decidable"

    for status in ("pending", "confirmed", "corrected"):
        metric = _metric(metric_value="6.5", reference_range="3.9-6.1", confirmation_status=status)
        assert infer_abnormal_flag_for_metric(metric) == "H", status

    # 值还没解析出来（空值）的行没有判定可言，与确认状态无关。
    empty = _metric(metric_value="", reference_range="3.9-6.1", confirmation_status="pending")
    assert infer_abnormal_flag_for_metric(empty) is None
    assert abnormal_flag_reason(empty) == "missing_value"


# ---------------------------------------------------------------------------
# 历史摘要口径：排除的不计数，模型误标的不计数，模型漏标的计数
# ---------------------------------------------------------------------------

def test_history_count_uses_the_decision_not_the_model_flag():
    """摘要数的是「判定为 H/L」，且不数患者排除掉的行。"""
    from app.api.auth import _abnormal_metric_count

    class _Report:
        def __init__(self, metrics):
            self.metrics = metrics

    report = _Report(
        [
            # 模型误标偏高、值在范围内 —— 不计数
            _metric(metric_value="5.2", reference_range="3.9-6.1"),
            # 模型漏标、值超范围 —— 计数
            _metric(metric_value="6.5", reference_range="3.9-6.1"),
            # 异常但被患者排除 —— 不计数
            _metric(metric_value="7.5", reference_range="3.9-6.1", confirmation_status="excluded"),
            # 不可判定（多数字值）—— 不计数
            _metric(metric_value="6.5/7.2", reference_range="3.9-6.1", confirmation_status="pending"),
        ]
    )
    assert _abnormal_metric_count(report) == 1
