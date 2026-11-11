"""Regression cases from the H5 acceptance run; no model or working database."""

import pytest

from app.data.models import MetricRecord
from app.service.admission import admission_reason, infer_abnormal_flag
from app.service.evidence_bridge import build_observations_with_unmatched


@pytest.mark.parametrize(("value", "reference", "flag"), [("5.2", "<5.2", "H"), ("90", ">90", "L")])
def test_open_boundary_is_not_sent_as_an_inclusive_v3_range(value, reference, flag):
    metric = MetricRecord(
        id=1,
        report_id=1,
        metric_name="空腹血糖",
        metric_code="fasting_glucose",
        metric_value=value,
        unit="mmol/L",
        reference_range=reference,
        evidence_text=f"空腹血糖 {value} mmol/L {reference}",
        page_number=1,
        source_file_index=1,
        confirmation_status="confirmed",
    )
    assert infer_abnormal_flag(value, reference) == flag
    assert admission_reason(metric, code=metric.metric_code) == "unsupported_reference_boundary"
    observations, skipped, unmatched = build_observations_with_unmatched([metric])
    assert observations == unmatched == []
    assert skipped == [{"observation_id": "health-flow-metric-1", "reason": "unsupported_reference_boundary"}]


@pytest.mark.parametrize("reference", ["男:3.5-5.5 女:2.5-4.5", "<3 或 >5", "<3 or >5"])
def test_conditional_ranges_are_not_guessed(reference):
    assert infer_abnormal_flag("3", reference) is None
