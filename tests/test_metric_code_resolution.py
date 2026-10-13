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


def test_a_name_that_carries_the_alias_plus_its_chinese_full_name_resolves():
    """「英文别名 + 中文全称」是**别名表能归一化的正常形态**，不是「只能靠目录」。

    这条断言此前是反的：它把 `Non-HDL 非高密度脂蛋白胆固醇` **解析不出来**当成
    「目录是唯一事实来源」的证据。那是把缺陷读成了美德 —— 抽取模型对这种行经常只给出
    组合名（`LDL-C 低脂蛋白(坏)胆固醇`），名字里明明带着正确的别名前缀，规则却因为
    后缀是中文而拒绝，于是报告 54 唯一的那条真异常连不到知识卡（#235）。

    目录仍然是权威：它给出不同的答案时以它为准（见下一条）。
    """
    assert metric_code_for_name("Non-HDL 非高密度脂蛋白胆固醇") == "non_hdl_c"
    assert metric_code_for_name("LDL-C 低脂蛋白(坏)胆固醇") == "ldl_c"
    assert metric_code_for_name("Total Chol 总胆固醇") == "total_cholesterol"
    # 嵌套括号是真实的抽取形态（报告 57）：单次剥括号会切坏后缀，于是这一行谁也认不出。
    assert metric_code_for_name("LDL-C (低脂蛋白(坏)胆固醇)") == "ldl_c"
    # 目录里若有归一化后的同名编码，则解析成功（这里用归一化后的形态表达该契约）。
    assert resolve_metric_code("nonhdl非高密度脂蛋白胆固醇", ["nonhdl非高密度脂蛋白胆固醇"]) is not None


def test_a_mixed_script_suffix_is_not_treated_as_a_synonym():
    """**只认「整段中文」，不认中英混排** —— 这条是本判据的负控。

    放行「任意粘连」就等于承认任意以同一个别名开头的名字都是同义词，那会让
    `LDL-C低密度…` 这类行静默连到错的知识卡上。中文那一支要求后缀**整段都是中文**，
    所以它只覆盖「同一项的中文写法」这一件事。
    """
    assert resolve_metric_code("LDL-C低density", None) is None
    assert resolve_metric_code("LDL-C 低 density", None) is None
    # **中文别名不行**：中文没有词间空格，「中文别名 + 中文」与「两项粘连」在字面上不可分。
    # 评审在 #237 指出 `空腹血糖餐后血糖` 会被判成「空腹血糖」的中文写法 —— 而它其实是
    # **两项**（空腹 + 餐后），连到一张错的知识卡。可分的是「拉丁字母 + 汉字」那种拼接。
    assert resolve_metric_code("空腹血糖餐后血糖", None) is None
    # 反过来，「英文缩写 + 中文全称」照常认 —— 那正是本票要修的那一类。
    assert resolve_metric_code("LDL-C 低脂蛋白(坏)胆固醇", None) == "ldl_c"
    # 括号里的英文全称**剥掉**之后后缀为空，认得出来；带括号的不是「后缀」。
    assert resolve_metric_code("LDL-C (Low Density Lipoprotein)", None) == "ldl_c"
    # 斜杠开头那一种老形态照旧（`LDL-C/HDL-C` 是复合项，别名表管不着它）。
    assert resolve_metric_code("空腹血糖/餐后", None) == "fasting_glucose"

    # 上限写在明处：**纯 ASCII 后缀那一条是本次之前就有的**，它比中文那一条宽 ——
    # `LDL-C Target Level` 会被归一成 `ldl_c`。真实报告里风险目标行的名字都以
    # 「High/Low/Moderate CV Risk Target」**开头**，别名因此落在名字中间而不是前缀，
    # 所以这条宽口子今天没有咬到东西。要收它得先有一份「哪些后缀不是同义词」的依据，
    # 那不在 #235 的射程内（本票修的是中文后缀被拒，不是 ASCII 后缀被放行）。
    assert resolve_metric_code("LDL-C Target Level", None) == "ldl_c"


def test_the_longest_matching_alias_wins():
    """表里短别名是长别名的前缀（`Non-HDL` / `Non-HDL-C`）时，取**最长**命中的那个。

    按表序取第一个命中的话，命中的是哪一个取决于字面顺序 —— 一个随每次编辑别名表
    都会变的隐式判据。
    """
    assert metric_code_for_name("Non-HDL-C") == "non_hdl_c"
    assert metric_code_for_name("Non HDL 非高密度脂蛋白") == "non_hdl_c"
    assert metric_code_for_name("Total Cholesterol") == "total_cholesterol"


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
