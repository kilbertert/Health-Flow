from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("value", "reference", "expected"),
    [
        ("5.2", "<5.2", (5.2, None, 5.2, False)),
        ("0", "-1-1", (0, -1, 1, True)),
        ("<0.1", "0.1-1", None),
        ("1/2", "0-3", None),
    ],
)
def test_range_display_preserves_values_and_comparators(value, reference, expected):
    from app.service.admission import range_display

    metric = SimpleNamespace(
        metric_value=value, unit="mmol/L", reference_range=reference, confirmation_status="confirmed"
    )
    display = range_display(metric)
    if expected is None:
        assert display is None
    else:
        assert (display["value"], display["low"], display["high"], display["high_inclusive"]) == expected


def test_range_display_uses_correction_and_omits_excluded_rows():
    from app.service.admission import range_display

    metric = SimpleNamespace(
        metric_value="8", confirmed_value="4", unit="mmol/L", reference_range="3-6", confirmation_status="corrected"
    )
    assert range_display(metric)["value"] == 4
    metric.confirmation_status = "excluded"
    assert range_display(metric) is None
