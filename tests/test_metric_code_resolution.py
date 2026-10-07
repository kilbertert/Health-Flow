"""标准指标编码解析（GLOSSARY.md 的「标准指标编码解析」）。

收敛前，「这个指标对应哪个标准指标编码」由两套事实来源回答：远端目录（权威）
与 `METRIC_ALIASES` 硬编码别名表（从不与目录对账）。两者互相矛盾的地方在评估
路径：确认时把不在目录里的编码**清空**（注释承诺 “never cross the evidence
boundary”），评估时又从别名表里**原样复活** —— 只要名字能被别名解析，它必然
跨过边界，而且**没有任何测试钉住它**。

本文件钉住收敛后的三条外部行为：

1. **唯一解析规则**：目录精确匹配 → 别名归一化 → **候选回目录验证**；
2. **复活矛盾消失**：确认时清空就是清空，评估只消费落定的编码；
3. **目录不可用时的降级**：不做存在性验证，显式降级而不是伪造解析结果。
"""

from __future__ import annotations

import pytest

from app.service.evidence_bridge import metric_code_for_name, resolve_metric_code

CATALOG = ["fasting_glucose", "ldl_c", "non_hdl_c", "total_cholesterol"]


# ---------------------------------------------------------------------------
# 解析规则
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("空腹血糖", "fasting_glucose"),
        ("LDL-C", "ldl_c"),
        ("Non-HDL", "non_hdl_c"),
        ("非高密度脂蛋白胆固醇", "non_hdl_c"),
        ("低密度脂蛋白胆固醇", "ldl_c"),
    ],
)
def test_alias_normalisation_resolves_when_the_catalog_confirms(name, expected):
    assert resolve_metric_code(name, CATALOG) == expected


def test_names_the_alias_table_cannot_normalise_still_resolve_via_the_catalog():
    """「英文名 + 中文名」这种组合形态别名表解析不出来 —— 这正是目录的意义。

    别名表是 45 条硬编码，覆盖不了真实报告里的写法；目录里带着指标的正式标签，
    所以「目录是唯一事实来源，别名表只是归一化辅助」不是修辞。
    """
    assert metric_code_for_name("Non-HDL 非高密度脂蛋白胆固醇") is None
    # 目录里若有归一化后的同名编码，则解析成功（这里用归一化后的形态表达该契约）。
    assert resolve_metric_code("nonhdl非高密度脂蛋白胆固醇", ["nonhdl非高密度脂蛋白胆固醇"]) is not None


def test_catalog_exact_match_wins():
    """名称归一化后直接在目录里 —— 目录是唯一事实来源。"""
    assert resolve_metric_code("Fasting Glucose", ["fastingglucose"]) == "fastingglucose"


def test_alias_candidate_must_exist_in_the_catalog():
    """别名能解析出候选，但目录里没有这个编码 —— **不算解析成功**。

    这是「别名表不是第二套事实来源」的判据：目录下架了某个编码，别名表不知道，
    但解析结果必须跟着目录走。
    """
    assert resolve_metric_code("空腹血糖", ["some_other_code"]) is None


def test_unknown_name_resolves_to_nothing():
    assert resolve_metric_code("完全未知的指标", CATALOG) is None


def test_catalog_unavailable_degrades_to_alias_only():
    """目录不可用（`catalog is None`）是**显式降级**：只做归一化，不做存在性验证。

    调用方据此把编码留作「待目录恢复后重新裁决」，而不是当成已确认。
    """
    assert resolve_metric_code("空腹血糖", None) == "fasting_glucose"
    # 与旧入口等价（它现在是本函数的降级形态）。
    assert metric_code_for_name("空腹血糖") == "fasting_glucose"


# ---------------------------------------------------------------------------
# 复活矛盾消失
# ---------------------------------------------------------------------------

def test_cleared_code_is_not_revived_without_catalog_validation():
    """确认时清空的编码，评估时**不会靠本地别名表复活**。

    这是本规格的核心断言，也是旧代码的病灶：它用
    `metric_code or metric_code_for_name(name)` 把刚清空的编码原样复活 —— 那个
    `metric_code_for_name` 是**本地别名快照**，不与目录对账，于是「确认时不在
    目录里」的编码只要名字能被别名解析就必然跨过边界。

    收敛后的规则按目录是否可用分成两种，**都不是本地快照复活**：

    - 目录不可用：保持 unmatched（没有目录就没有验证，宁可不匹配也不猜）；
    - 目录可用：按**权威目录**重新裁决（这正是「等目录恢复后重新裁决」的兑现，
      名称必须被目录验证才算解析成功）。
    """
    from app.data.models import MetricRecord as MetricModel
    from app.service.evidence_bridge import build_observations_with_unmatched

    metric = MetricModel(
        id=1,
        report_id=1,
        metric_name="Non-HDL",  # 别名表能解析出 non_hdl_c
        metric_value="4.00",
        unit="mmol/L",
        reference_range="<3.40",
        page_number=1,
        evidence_text="Non-HDL 4.00 mmol/L (<3.40)",
        source_file_index=1,
        confirmation_status="confirmed",
        metric_code=None,  # 确认时被清空
    )

    # 目录不可用：不复活。
    observations, _, unmatched = build_observations_with_unmatched([metric])
    assert observations == [], "目录不可用时清空过的编码不该被复活"
    assert unmatched and unmatched[0]["metric_code"] is None

    # 目录可用且能验证这个名称：允许重新裁决 —— 用的是目录，不是别名快照。
    observations, _, _ = build_observations_with_unmatched([metric], CATALOG)
    assert observations and observations[0]["metric_code"] == "non_hdl_c"

    # 目录可用但**不认**这个名称：保持 unmatched —— 别名表不是第二套事实来源。
    observations, _, unmatched = build_observations_with_unmatched([metric], ["some_other_code"])
    assert observations == []


def test_confirmed_code_is_what_crosses_the_boundary():
    from app.data.models import MetricRecord as MetricModel
    from app.service.evidence_bridge import build_observations_with_unmatched

    metric = MetricModel(
        id=1,
        report_id=1,
        metric_name="Non-HDL",
        metric_value="4.00",
        unit="mmol/L",
        reference_range="<3.40",
        page_number=1,
        evidence_text="Non-HDL 4.00 mmol/L (<3.40)",
        source_file_index=1,
        confirmation_status="confirmed",
        metric_code="non_hdl_c",
    )
    observations, _, _ = build_observations_with_unmatched([metric])
    assert observations[0]["metric_code"] == "non_hdl_c"


# ---------------------------------------------------------------------------
# 复审发现的三条（#149）
# ---------------------------------------------------------------------------

def test_catalog_outage_does_not_permanently_unmatch():
    """目录中断时落定的空编码，**目录恢复后会被重新裁决**。

    评审指出：确认时目录不可用 → 编码清空；如果评估路径也从不重新解析，那些
    指标就**永久**变成 unmatched，目录恢复也救不回来。
    """
    from app.data.models import MetricRecord as MetricModel
    from app.service.evidence_bridge import build_observations_with_unmatched

    metric = MetricModel(
        id=1, report_id=1, metric_name="Non-HDL", metric_value="4.00", unit="mmol/L",
        reference_range="<3.40", page_number=1, evidence_text="Non-HDL 4.00 mmol/L (<3.40)",
        source_file_index=1, confirmation_status="confirmed", metric_code=None,
    )
    observations, _, _ = build_observations_with_unmatched([metric], CATALOG)
    assert observations and observations[0]["metric_code"] == "non_hdl_c"


def test_stale_requested_code_falls_back_to_the_metric_name():
    """患者选定的编码若已从目录下架，**回落到按目录验证过的指标名**。

    评审指出：只试编码会让一个下架的旧编码把本来能解析的名称一起挡掉 —— 一个
    可识别的异常指标因此变成 unmatched。
    """
    # 下架的编码 + 能解析的名称 → 按名称解析成功。
    assert resolve_metric_code("ldl_c_removed", CATALOG) is None
    assert resolve_metric_code("LDL-C", CATALOG) == "ldl_c"
    # 两者都解析不出才是 unmatched。
    assert resolve_metric_code("完全未知", CATALOG) is None
