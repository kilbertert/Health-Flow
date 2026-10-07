"""指标生效值（GLOSSARY.md 的「指标生效值」）。

收敛前，「这条指标当前生效的是哪一组值」由证据桥里一段无名局部规则回答，
前端只能自己抄一遍或漏抄 —— 于是同一份报告在报告单显示修正值、在确认页显示
模型值。本文件钉住收敛后的规则与契约：

1. **四态各自的生效值**：corrected 取修正值、confirmed/extracted 取模型值、
   excluded 没有生效值；
2. **响应契约**：`effective_*` 与证据边界用的是同一函数、同一输入 —— 患者看到
   的值与跨边界的值必须一致。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.service.metric_effective_value import effective_value


def _metric(**overrides):
    fields = {
        "metric_value": "6.5",
        "unit": "mmol/L",
        "reference_range": "3.9-6.1",
        "evidence_text": "空腹血糖 6.5 mmol/L",
        "confirmed_value": None,
        "confirmed_unit": None,
        "confirmed_reference_range": None,
        "confirmed_evidence_text": None,
        "confirmation_status": "pending",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_corrected_row_uses_the_corrected_values():
    metric = _metric(
        confirmed_value="6.4",
        confirmed_unit="mmol/L",
        confirmed_reference_range="3.9-6.0",
        confirmed_evidence_text="空腹血糖 6.4 mmol/L",
        confirmation_status="corrected",
    )
    effective = effective_value(metric)
    assert (effective.value, effective.unit, effective.reference_range, effective.evidence_text) == (
        "6.4",
        "mmol/L",
        "3.9-6.0",
        "空腹血糖 6.4 mmol/L",
    )
    assert effective.source == "corrected"


def test_confirmed_row_uses_the_model_values_it_was_confirmed_with():
    """确认但没改：confirmed 列写的就是模型值，取值相同、来源不同。"""
    metric = _metric(
        confirmed_value="6.5",
        confirmed_unit="mmol/L",
        confirmed_reference_range="3.9-6.1",
        confirmed_evidence_text="空腹血糖 6.5 mmol/L",
        confirmation_status="confirmed",
    )
    effective = effective_value(metric)
    assert effective.value == "6.5"
    assert effective.source == "confirmed"


def test_pending_row_uses_the_extracted_values():
    effective = effective_value(_metric())
    assert effective.value == "6.5"
    assert effective.source == "extracted"
    assert effective.is_decidable


@pytest.mark.parametrize("status", ["excluded"])
def test_excluded_row_has_no_effective_value(status):
    """患者排除的指标**没有**生效值 —— 四元组全空，不是「取不到值」。"""
    metric = _metric(confirmed_value="6.5", confirmed_unit="mmol/L", confirmation_status=status)
    effective = effective_value(metric)
    assert (effective.value, effective.unit, effective.reference_range, effective.evidence_text) == (
        None,
        None,
        None,
        None,
    )
    assert effective.source == "excluded"
    assert not effective.is_decidable


def test_partial_confirmed_columns_fall_back_per_field():
    """confirmed 列逐字段回退：只改了值、没改单位时，单位取模型值。"""
    metric = _metric(confirmed_value="6.4", confirmation_status="corrected")
    effective = effective_value(metric)
    assert effective.value == "6.4"
    assert effective.unit == "mmol/L"
    assert effective.reference_range == "3.9-6.1"


# ---------------------------------------------------------------------------
# 契约一致性：患者看到的值 == 证据边界上用的值
# ---------------------------------------------------------------------------

def test_response_effective_value_matches_the_evidence_boundary():
    """响应里的 `effective_*` 与 `build_observations_with_unmatched` 用的是同一份值。

    这是本规格的验收线：两套界面之所以会对同一指标显示不同的结果，就是因为
    「响应中患者看到的值」与「边界上使用的值」曾经是两条独立的路径。
    """
    from app.data.models import MetricRecord as MetricModel
    from app.service.evidence_bridge import build_observations_with_unmatched

    metric = MetricModel(
        id=1,
        report_id=1,
        metric_name="空腹血糖",
        metric_value="6.5",
        unit="mmol/L",
        reference_range="3.9-6.1",
        confirmed_value="7.1",
        confirmed_unit="mmol/L",
        confirmed_reference_range="3.9-6.1",
        confirmed_evidence_text="空腹血糖 7.1 mmol/L (3.9-6.1)",
        evidence_text="空腹血糖 6.5 mmol/L (3.9-6.1)",
        page_number=1,
        source_file_index=1,
        confirmation_status="corrected",
        metric_code="fasting_glucose",
    )
    observations, _, _ = build_observations_with_unmatched([metric])
    effective = effective_value(metric)

    assert observations, "修正后的异常指标应当跨过证据边界"
    assert observations[0]["value"] == float(effective.value)
    assert observations[0]["unit"] == effective.unit
    assert observations[0]["evidence_text"] == effective.evidence_text


def test_excluded_row_never_crosses_the_boundary():
    from app.data.models import MetricRecord as MetricModel
    from app.service.evidence_bridge import build_observations_with_unmatched

    metric = MetricModel(
        id=1,
        report_id=1,
        metric_name="空腹血糖",
        metric_value="7.5",
        unit="mmol/L",
        reference_range="3.9-6.1",
        page_number=1,
        evidence_text="空腹血糖 7.5 mmol/L",
        source_file_index=1,
        confirmation_status="excluded",
        metric_code="fasting_glucose",
    )
    observations, skipped, unmatched = build_observations_with_unmatched([metric])
    assert observations == []
    assert skipped == [] and unmatched == []
    assert effective_value(metric).value is None
