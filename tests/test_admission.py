"""解读准入：一份词表、一个判定入口、同一行两侧同名（#183）。

本文件钉住的是**外部可观测行为**：判定入口给出的结论、门禁分出的桶、以及词表与出域
契约之间的恒等关系。它不测实现细节 —— 词表放在哪个模块、函数叫什么名字都可以改，
「只有一份」「两侧同名」「行数可加总」不能。

三条断言的分量不同，值得先说清楚哪条最要紧：

1. **同一行在两侧同名。** 这是本票的核心。此前空值一行在证据门禁是 `invalid_value`、
   在判定守卫是 `missing_value` —— 同一个事实两个名字，而两侧都是「承重墙」，谁也不
   知道对方怎么叫。把两侧改成调同一个函数之后，这条断言才有意义：它在改回去时会红。
2. **词表只有一份。** 出域契约的 `Literal` 是从词表**派生**的（`Literal[*X]`），所以
   值集恒等；再加一条静态守卫，禁止第二个文件重新声明一套名字。
3. **每一条已解析的行都有结论。** `pending` / `excluded` 原本落在三桶之外、由前端从
   原始 `confirmation_status` 猜；现在它们有名字，且台账能加总。
"""

from __future__ import annotations

import ast
import re
import typing
from pathlib import Path

import pytest

from app.data.models import MetricRecord as MetricModel
from app.schema.evidence import Skipped, Unmatched
from app.service.admission import (
    admission_reason,
    parse_reference_range,
    reference_reason,
    single_number,
    tally,
    value_level_reason,
    value_reason,
)
from app.service.admission_vocabulary import (
    ADMITTED_STATUSES,
    NOT_EVALUATED_REASONS,
    SKIPPED_REASONS_ORDERED,
    UNMATCHED_REASONS_ORDERED,
    vocabulary,
)
from app.service.evidence_bridge import abnormal_flag_reason, build_observations_with_unmatched

REPO_ROOT = Path(__file__).resolve().parents[1]


def _row(**overrides):
    """一条「本来就该进入解读」的行，用 overrides 把它推到某个边界上。

    **默认取异常值 6.5**，不是 5.2：门禁会因为「在参考区间内」把 5.2 跳过，于是每个
    用例都会撞上 `within_reference_range` 而不是它自己要测的那条边界。

    `evidence_text` 默认**由值推导**，因为门禁要求原文证据里真的含这个值 —— 手写一个
    与值不符的字符串会让几乎每个用例都以 `missing_source_evidence` 失败，而那是测试
    自己造出来的假象，不是被测行为。
    """
    value = overrides.get("metric_value", "6.5")
    reference = overrides.get("reference_range", "3.9-6.1")
    values = {
        "id": 1,
        "report_id": 1,
        "metric_name": "空腹血糖",
        "metric_value": value,
        "unit": "mmol/L",
        "reference_range": reference,
        "page_number": 1,
        "evidence_text": f"空腹血糖 {value} mmol/L ({reference})",
        "source_file_index": 1,
        "confirmation_status": "confirmed",
        "metric_code": "fasting_glucose",
    }
    values.update(overrides)
    if "evidence_text" not in overrides and ("metric_value" in overrides or "reference_range" in overrides):
        values["evidence_text"] = f"空腹血糖 {values['metric_value']} mmol/L ({values['reference_range']})"
    return MetricModel(**values)


# ── 1. 同一行两侧同名（本票的核心） ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # 值这一类：两侧以前**不同名**的那一条，现在是同一条。
        ({"metric_value": ""}, "missing_value"),
        ({"metric_value": "<20"}, "invalid_value"),
        ({"metric_value": "6.5/7.2"}, "invalid_value"),
        ({"reference_range": None}, "missing_reference_range"),
        ({"reference_range": "阴性"}, "missing_reference_range"),
    ],
)
def test_the_gate_and_the_guard_give_the_same_name_for_the_same_row(overrides, expected):
    """值/参考范围这一类原因，门禁的跳过理由与判定守卫的原因必须**逐字相同**。

    这里断言的是**相等**，不是各自等于某个字面量：把两侧拆回两份实现时，`expected`
    在两侧会分叉（空值一行历史上就是 `invalid_value` vs `missing_value`），这条会红。
    """
    metric = _row(**overrides)
    _, skipped, _ = build_observations_with_unmatched([metric])
    gate_reasons = {item["reason"] for item in skipped}
    guard_reason = abnormal_flag_reason(metric)

    assert guard_reason == expected, (overrides, guard_reason)
    assert gate_reasons == {expected}, (overrides, gate_reasons)


def test_an_empty_value_is_named_the_same_on_both_sides():
    """空值一行两侧同名（历史上是 `invalid_value` vs `missing_value`）。

    单独写一条、不放进上面的参数化里，因为它是本票存在的最直接理由 —— 它值得一条
    能指名道姓地失败的断言。
    """
    metric = _row(metric_value="")
    _, skipped, _ = build_observations_with_unmatched([metric])
    assert {item["reason"] for item in skipped} == {"missing_value"}
    assert abnormal_flag_reason(metric) == "missing_value"


def test_the_two_sides_call_one_shared_function():
    """两侧必须调**同一个**函数，而不是各自碰巧给出同一个字面量。

    这条用「同一个输入 → 同一个答案」的方式断言共享：直接对判定入口与它消费的纯函数
    比较。两条断言都真而实现是两份抄写时，这条不会红 —— 所以它配合上面的「逐字相同」
    一起看，两条一起才封住「各写一份、各自正确」。
    """
    assert value_level_reason("", "3.9-6.1") == "missing_value"
    assert value_level_reason("<20", "3.9-6.1") == "invalid_value"
    assert value_level_reason("5.2", None) == "missing_reference_range"
    assert value_level_reason("5.2", "3.9-6.1") is None


# ── 2. 词表只有一份 ─────────────────────────────────────────────────────────


def test_the_contract_literals_are_derived_from_the_vocabulary():
    """出域契约的两个 `Literal` 恰好是词表的两个子集 —— 逐字相等，无漂移。"""
    skipped = set(typing.get_args(Skipped.model_fields["reason"].annotation))
    unmatched = set(typing.get_args(Unmatched.model_fields["reason"].annotation))
    assert skipped == set(SKIPPED_REASONS_ORDERED)
    assert unmatched == set(UNMATCHED_REASONS_ORDERED)
    # 两个投影**不相交**，且都是总词表的真子集。
    assert not (skipped & unmatched)
    assert skipped | unmatched <= vocabulary()


def test_the_dead_value_is_gone_from_skipped():
    """`unknown_metric_code` 不在 `Skipped` 的词表里，`missing_value` 在。

    这两个是「词表没有家」的两个漂移点：前者在 `Skipped` 的 Literal 里待过很久却**从
    不产生**（它是 `Unmatched` 的名字），后者被兄弟函数产生却**不在** `Skipped` 里。
    """
    skipped = set(SKIPPED_REASONS_ORDERED)
    assert "unknown_metric_code" not in skipped
    assert "missing_value" in skipped
    assert "unknown_metric_code" in set(UNMATCHED_REASONS_ORDERED)


def test_no_second_vocabulary_is_declared_anywhere():
    """静态守卫：全仓库只有一处声明这些原因名。

    没有这一条时，「词表归一」会被下一个人在一个新文件里重新写一份 `Literal[...]`
    悄悄推翻 —— 而那正是本票要修的病。守卫只查**声明**（`Literal` / 集合字面量里的
    取值），不查使用：`reason="missing_value"` 这种消费是正常的。
    """
    reason_names = vocabulary()
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.joinpath("app").rglob("*.py")):
        if path.name == "admission_vocabulary.py":
            continue  # 它的家
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # `Literal["a", "b"]` / `Literal[*SOMETHING]` 里出现的名字字面量
            if isinstance(node, ast.Subscript) and _is_literal(node.value):
                literals = {
                    elt.value
                    for elt in getattr(node.slice, "elts", [])
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
                if len(literals & reason_names) >= 3:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "这些位置重新声明了一份原因词表（应改为从 app/service/admission_vocabulary.py 取）："
        + ", ".join(offenders)
    )


def _is_literal(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "Literal") or (
        isinstance(node, ast.Attribute) and node.attr == "Literal"
    )


# ── 3. 每一个已核对的行都有结论，且台账可加总 ────────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # 进入解读：`None`（6.5 判 H，跨过边界）。
        ({}, None),
        # 证据不完备 —— 值判得出来，却跨不过证据边界。
        ({"unit": None}, "missing_unit"),
        ({"evidence_text": None}, "missing_source_evidence"),
        ({"page_number": None}, "missing_source_page"),
        # 值这一类。
        ({"metric_value": ""}, "missing_value"),
        ({"metric_value": "6.5/7.2"}, "invalid_value"),
        ({"reference_range": None}, "missing_reference_range"),
        # 判定过、在区间内 —— 「正常」，不是「没能解读」。
        ({"metric_value": "5.0"}, "within_reference_range"),
        # 没有可匹配的编码（值必须判成异常，否则先撞上 within_reference_range）。
        ({"metric_code": None}, "unknown_metric_code"),
    ],
)
def test_every_admitted_row_gets_exactly_one_conclusion(overrides, expected):
    assert admission_reason(_row(**overrides), code=_row(**overrides).metric_code) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [("pending", "pending"), ("excluded", "excluded")],
)
def test_unreviewed_and_excluded_rows_have_their_own_conclusions(status, expected):
    """`pending` / `excluded` 是**显式结论**，不是「没有原因」。

    此前它们落在三桶之外，患者侧只能从原始 `confirmation_status` 猜 —— 而那是
    「同一概念两处判定」的另一个入口。
    """
    assert admission_reason(_row(confirmation_status=status), code="fasting_glucose") == expected
    assert expected in NOT_EVALUATED_REASONS


def test_the_tally_covers_every_row_exactly_once():
    """台账必须加总：进入解读 + skipped + unmatched + 未评估 == 行数。

    这份 PRD 的硬指标就是这个等式 —— 此前 `pending` / `excluded` 的行落在等式之外，
    所以「三桶相加 != 行数」永远成立，而没有任何东西会因此报错。
    """
    rows = [
        _row(id=1),  # 进入解读
        _row(id=2, metric_value="5.0"),  # within_reference_range
        _row(id=3, metric_code=None),  # unknown_metric_code
        _row(id=4, metric_value=""),  # missing_value
        _row(id=5, confirmation_status="pending"),  # pending
        _row(id=6, confirmation_status="excluded"),  # excluded
        _row(id=7, unit=None, evidence_text=None),  # missing_unit（值本身可解析）
    ]
    reasons = [admission_reason(metric, code=metric.metric_code) for metric in rows]
    counts = tally(reasons)

    assert counts.total == len(rows)
    # 进入解读 1 / skipped 3（within_range + missing_value + missing_unit）/
    # unmatched 1 / 未评估 2（pending + excluded）。
    assert (counts.included, counts.skipped, counts.unmatched, counts.not_evaluated) == (1, 3, 1, 2)


def test_a_row_shown_as_abnormal_but_kept_out_of_the_reading_carries_a_reason():
    """「显示 H、历史摘要计入异常、却因缺单位没进解读」的行，从此带一个明确原因。

    这是患者可见的后果里最刺眼的一条：报告单说 H，而它从未参与解读，且没有任何地方
    说明为什么。两个计数分叉是**刻意的**（异常判定只看值与参考范围，准入还看证据
    完备性），但分叉的原因必须能被说出来。
    """
    metric = _row(unit=None, metric_value="6.5")
    assert admission_reason(metric, code="fasting_glucose") == "missing_unit"
    # 异常判定照旧给出 H —— 本票不动它（历史摘要计数因此不变）。
    from app.service.evidence_bridge import infer_abnormal_flag_for_metric

    assert infer_abnormal_flag_for_metric(metric) == "H"


# ── 4. 值、参考范围、单值的判定本身 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", "missing_value"), ("   ", "missing_value"), ("<20", "invalid_value"), ("6.5/7.2", "invalid_value")],
)
def test_value_reason_separates_not_parsed_yet_from_not_a_number(text, expected):
    """「还没解析出来」与「不是一个数」是两句不同的话 —— 患者要做的动作不同。"""
    assert value_reason(text) == expected


def test_reference_reason_does_not_blame_the_value():
    """参考范围缺失与值无关（定性指标没有可解析范围是常态）。"""
    assert reference_reason(None) == "missing_reference_range"
    assert reference_reason("阴性") == "missing_reference_range"
    assert reference_reason("3.9-6.1") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("5.2", 5.2), ("-1.5", -1.5), ("", None), ("6.5/7.2", None), ("5.2 5.3", None), ("<20", 20.0)],
)
def test_single_number_is_the_one_place_that_decides(text, expected):
    """`<20` 解析出 20 —— 单值解析**不做**符号判断，那是 `value_reason` 的事。

    这条分界是刻意的：`single_number` 回答「文本里有几个数」，`value_reason` 回答
    「这个值能不能当测量值用」。把符号判断塞进单值解析会让「门禁先拒带符号的值」
    与「异常判定拒绝带符号的值」两处漂移。
    """
    assert single_number(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3.9-6.1", (3.9, 6.1)),
        ("3.9~6.1", (3.9, 6.1)),
        ("<5.0", (None, 5.0)),
        ("≥3.9", (3.9, None)),
        ("阴性", (None, None)),
        # 逐字搬自 evidence_bridge：上界小于下界视为解析不出（历史上就是如此）。
        ("6.1-3.9", (None, None)),
    ],
)
def test_reference_range_parsing_is_unchanged(text, expected):
    assert parse_reference_range(text) == expected


def test_the_gate_still_produces_the_same_buckets_as_before():
    """分桶没变：需要匹配的行仍然进 observations，缺编码的仍然进 unmatched。

    本票只把「原因从哪来」收敛，**不动**三桶怎么分。这条是那条边界的行为断言 ——
    否则「顺手也把分桶改了」不会被任何东西发现。
    """
    good = _row(id=1)
    no_code = _row(id=2, metric_code=None)
    normal = _row(id=3, metric_value="5.0")
    observations, skipped, unmatched = build_observations_with_unmatched([good, no_code, normal])

    assert [item["metric_code"] for item in observations] == ["fasting_glucose"]
    assert [item["reason"] for item in skipped] == ["within_reference_range"]
    assert [item["reason"] for item in unmatched] == ["unknown_metric_code"]
    # unmatched 的行仍然带着 source_observation（患者要看的是哪一行、定位在哪）。
    assert unmatched[0]["source_observation"]["evidence_text"]


def test_admitted_statuses_are_the_two_the_gate_uses():
    """入口守卫用哪两个状态，就是词表说的那两个 —— 不是各写一份。"""
    assert {"confirmed", "corrected"} == ADMITTED_STATUSES
    rows = [_row(id=i, confirmation_status=status) for i, status in enumerate(("confirmed", "corrected", "pending"))]
    observations, skipped, unmatched = build_observations_with_unmatched(rows)
    assert len(observations) + len(skipped) + len(unmatched) == 2, "pending 的行不该进三桶"


def test_no_module_imports_the_vocabulary_at_runtime_by_regex():
    """守卫：词表里没有重复的名字字面量被写第二遍（同一条取值的两种拼法）。

    `reason_names` 逐字比较，所以像 `missing_unit` 与 `missing-units` 这种「看起来
    一样但不是同一个」的拼写不会被静默接受。
    """
    source = (REPO_ROOT / "app" / "service" / "admission_vocabulary.py").read_text(encoding="utf-8")
    declared = re.findall(r'"([a-z_]+)"', source)
    reason_names = {name for name in declared if name in vocabulary()}
    assert reason_names == vocabulary(), (
        "词表的字面量与 `AdmissionReason` 的取值必须一一对应；"
        f"只在字面量里出现的：{sorted(reason_names - vocabulary())}；"
        f"只在 Literal 里的：{sorted(vocabulary() - reason_names)}"
    )
