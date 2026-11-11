"""判定边界用例（#293）：比较符语义、复合双界、中文定性词。

`test_abnormality.py` 钉的是「值 × 区间」的主干形态；本文件钉**比较符本身**的语义——
报告印的 `<5.2` 与 `≤5.2` 不是同一个判据：前者的界上值是**异常**，后者是**正常**
（FHIR R4 `Quantity.comparator`：比较符是值的一部分，丢掉它等于把判据换掉）。

三类修复前会错的形态：

- **界上值**：`5.2 (<5.2)` 旧实现把比较符丢掉、只剩 `high=5.2`，`5.2 > 5.2` 为假 →
  判成 N，把一个「达到上限即异常」的行说成正常。`>90` 对称。
- **复合双界**：`>5.2, <8.0` 旧实现按正则命中顺序只保留一个界（`<8.0` 先命中 →
  丢掉下界），`3.0` 会被判成 N 而不是 L。
- **中文定性词**：检验报告的分类结果词不止英文——`阴性`/`未检出`/`阳性`/`弱阳性`
  是同一封闭规范集合里的词（HL7 v2 OBX-8），缺它们会把可判的行退回「判不了」。
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
# 参考范围的比较符是判据的一部分：界上值按「含/不含」分两侧
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reference", "expected"),
    [
        # 上界不含：界上值即异常（修复前判 N —— 比较符被丢掉）
        ("5.2", "<5.2", "H"),
        ("5.19", "<5.2", "N"),
        # 上界含：界上值在范围内
        ("5.2", "≤5.2", "N"),
        ("5.2", "<=5.2", "N"),
        ("5.21", "≤5.2", "H"),
        # 下界不含：界上值即异常（修复前判 N）
        ("90", ">90", "L"),
        ("91", ">90", "N"),
        # 下界含
        ("90", "≥90", "N"),
        ("90", ">=90", "N"),
        ("89.9", "≥90", "L"),
        # 单点区间仍是区间：界上算范围内
        ("5.2", "5.2-5.2", "N"),
        ("5.3", "5.2-5.2", "H"),
    ],
)
def test_reference_comparator_decides_the_boundary_value(value, reference, expected):
    assert infer_abnormal_flag(value, reference) == expected


# ---------------------------------------------------------------------------
# 复合双界：同一格印两个判据时，两个都参与；倒置即空集，判不了
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reference", "expected"),
    [
        ("6", ">5.2, <8.0", "N"),
        ("3.0", ">5.2, <8.0", "L"),  # 修复前只剩 <8.0 → 误判 N
        ("8.5", ">5.2, <8.0", "H"),
        # 复合界的含/不含同样生效
        ("5.2", ">5.2, <8.0", "L"),  # 修复前误判 N
        ("5.2", "≥5.2, <8.0", "N"),
        ("8.0", ">5.2, <8.0", "H"),  # 修复前误判 N
        ("8.0", ">5.2, ≤8.0", "N"),
        # 两个判据互斥成空集 —— 判不了，而不是拿半个界硬判（修复前拿 <5.2 判成 N）
        ("5.2", "<5.2, >8.0", None),
    ],
)
def test_compound_reference_uses_both_bounds(value, reference, expected):
    assert infer_abnormal_flag(value, reference) == expected


# ---------------------------------------------------------------------------
# 值自带界：判得出当且仅当值的界**蕴含于**参考界（同一数值、方向被包含）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reference", "expected"),
    [
        # 同一界、逐字相同 —— #235 已钉，防回归
        ("<20", "<20", "N"),
        # 严格界 ⊂ 宽松界：值 `<0.1` 必在 `≤0.1` 之内 → 判得出且正常
        ("<0.1", "≤0.1", "N"),
        (">90", "≥90", "N"),
        # 反向不成立：值含界上值、参考不含 → 证不了「必在界内」，不猜
        ("≤0.1", "<0.1", None),
        ("≥90", ">90", None),
        # 数值不同的两个界 —— 「值的界更严」是另一种证明，不做：不猜
        ("<0.1", "<0.05", None),
        ("<0.1", "≤0.05", None),
        # 区间参考范围对带符号的值：值可能比下界还低 → 证不了「在区间内」，不猜
        ("<0.1", "0-0.1", None),
        ("≤5.2", "3.9-6.1", None),
        # 方向相反
        (">20", "<20", None),
    ],
)
def test_signed_value_decides_only_when_its_bound_is_inside_the_reference(value, reference, expected):
    assert infer_abnormal_flag(value, reference) == expected


# ---------------------------------------------------------------------------
# 中文规范分类结果词：与英文词同一个封闭集合
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("阴性", "N"),
        ("未检出", "N"),
        ("阳性", "A"),
        ("弱阳性", "A"),
        ("检出", "A"),
        # 「未检测」说的是「没有测」，不是一个阴性结果 —— 判成 N 会替一个根本没测
        # 的项断言正常；落「判不了」才诚实。
        ("未检测", None),
    ],
)
def test_chinese_qualitative_words_carry_the_same_verdicts(value, expected):
    assert infer_abnormal_flag(value, None) == expected


def test_report_printed_word_domain_still_governs_chinese_words():
    """报告印了取值域时**报告的判据**仍然优先：中文词与英文词同一规则（#239 同一条）。"""
    assert infer_abnormal_flag("阴性", "(阴性)") == "N"
    assert infer_abnormal_flag("阴性", "(阳性)") is None


# ---------------------------------------------------------------------------
# 不可判定的形态给出同源原因；可判的不给
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metric", "expected_reason"),
    [
        # 倒置区间 → 解析不出 → 缺参考范围
        (_metric(metric_value="5.2", reference_range="8.0-3.9"), "missing_reference_range"),
        # 空集复合界同理 —— 不是拿半个界
        (_metric(metric_value="5.2", reference_range="<5.2, >8.0"), "missing_reference_range"),
        # 值带符号、判据又不蕴含 → 值这一类（invalid_value），不是「缺范围」
        (_metric(metric_value="<0.1", reference_range="0-0.1"), "invalid_value"),
        # 两个值并排 —— 仍判不了
        (_metric(metric_value="6.5 7.2", reference_range="3.9-6.1"), "two_values"),
    ],
)
def test_undecidable_boundary_shapes_carry_the_shared_reason(metric, expected_reason):
    assert abnormal_flag_reason(metric) == expected_reason
    assert infer_abnormal_flag_for_metric(metric) is None


# ---------------------------------------------------------------------------
# 计数层的镜像：推断出的判定压过抽取模型写下的原始标记
# ---------------------------------------------------------------------------


def test_inferred_flag_wins_over_the_raw_marker_in_counts():
    """「判为正常的行不再待核对」——服务端判定是唯一口径，原始标记只在判不出时作展示依据。"""
    from app.service.report_metric_counts import needs_review

    # 模型标 H、值在范围内 → 判定 N → 不请他（原始标记翻不了盘）。
    assert not needs_review(_metric(metric_value="5.2", reference_range="3.9-6.1", abnormal_flag="H"))
    # 判不出 + 已表态 + 模型宣称异常 → 「待核对」，请他（原始标记仅有的用途）。
    assert needs_review(_metric(metric_value="6.5/7.2", reference_range="3.9-6.1", abnormal_flag="H"))
    # 判不出 + 已表态 + 模型没标异常 → 如实显示原始标记，不再请他。
    assert not needs_review(_metric(metric_value="<0.1", reference_range="0-0.1", abnormal_flag=""))
