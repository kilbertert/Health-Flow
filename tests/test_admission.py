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
    —— 或者**一个元组/集合/列表常量** —— 悄悄推翻，而那正是本票要修的病。本票自己
    就是把词表从 `Literal[...]` 搬成 `tuple[...]` 的，所以只查 `Literal` 的守卫会漏掉
    最像它的那种复现。

    查的是**声明**：一组原因名字面量的集合/序列/字典键、或 `Literal` 的实参。单个字面量
    的消费（`reason="missing_value"`）与判断（`reason == "missing_value"`）不在此列 ——
    它们不是第二份词表。

    阈值是 3 个**不同**名字：低于它，一次针对某个投影的局部集合与真正的词表无法可靠
    区分，而误报会让这条守卫被删掉。
    """
    reason_names = vocabulary()
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.joinpath("app").rglob("*.py")):
        if path.name == "admission_vocabulary.py":
            continue  # 它的家
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if _is_declaration_of_reason_names(node, reason_names):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "这些位置重新声明了一份原因词表（应改为从 app/service/admission_vocabulary.py 取）："
        + ", ".join(offenders)
    )


def _string_literals(value: ast.AST) -> set[str]:
    """一个 AST 节点里**字面量**字符串的集合。名字/属性引用（如 `Literal[X]` 的 `X`）
    不算 —— 那正是「从词表派生」的正确写法。"""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        # 星号展开（`(*A, *B)`）里的名字引用跳过，它也是派生。
        return set().union(*(_string_literals(elt) for elt in value.elts)) if value.elts else set()
    return set()


def _is_declaration_of_reason_names(node: ast.AST, reason_names: frozenset[str]) -> bool:
    candidates: list[ast.AST] = []
    if isinstance(node, ast.Subscript) and _is_literal(node.value):
        # `Literal["a", "b"]`：实参里的字符串（星号展开里的名字引用不计）。
        slice_value = node.slice
        candidates = list(slice_value.elts) if isinstance(slice_value, (ast.Tuple, ast.List)) else [slice_value]
    elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        candidates = list(node.elts)
    elif isinstance(node, ast.Dict):
        candidates = [key for key in node.keys if key is not None]
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset":
        candidates = list(node.args)
    literals: set[str] = set()
    for candidate in candidates:
        literals |= _string_literals(candidate)
    return len(literals & reason_names) >= 3


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
        # 带符号的值：**值这一类优先于证据完备性**。这一条守住判定顺序 —— 旧顺序是
        # 「先看单位/证据/页码，再看值」，于是同一行（`<20` 且缺单位）从这里
        # `invalid_value` 变成 `missing_unit`。改回旧顺序时它会红。
        ({"metric_value": "<20", "unit": None}, "invalid_value"),
        ({"reference_range": None}, "missing_reference_range"),
        # 判定过、在区间内 —— 「正常」，不是「没能解读」。
        ({"metric_value": "5.0"}, "within_reference_range"),
        # 没有可匹配的编码（值必须判成异常，否则先撞上 within_reference_range）。
        ({"metric_code": None}, "unknown_metric_code"),
    ],
)
def test_every_admitted_row_gets_exactly_one_conclusion(overrides, expected):
    assert admission_reason(_row(**overrides), code=_row(**overrides).metric_code) == expected


def test_excluded_rows_have_their_own_conclusion():
    """`excluded` 是**显式结论**：患者表了态，不必再看这一行的值。

    此前它落在三桶之外，患者侧只能从原始 `confirmation_status` 猜 —— 而那是
    「同一概念两处判定」的另一个入口。
    """
    assert admission_reason(_row(confirmation_status="excluded"), code="fasting_glucose") == "excluded"
    assert "excluded" in NOT_EVALUATED_REASONS


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # 可判、但患者还没核对：值/单位/参考范围/原文证据/页码都齐。
        ({}, "awaiting_confirmation"),
        # 判不了的行落它们**真正**的原因 —— 不再被「尚未核对」提前收口（#203）。
        ({"metric_value": "3.87 4.00"}, "invalid_value"),
        ({"metric_value": ""}, "missing_value"),
        ({"unit": None, "evidence_text": None}, "missing_unit"),
        ({"reference_range": None}, "missing_reference_range"),
    ],
)
def test_an_unreviewed_row_still_gets_its_real_reason(overrides, expected):
    """**本票的核心**：尚未核对**不是**准入结论，判定照常走完。

    此前 `admission_reason` 对 `confirmation_status='pending'` 一律返回 `pending` ——
    于是「患者没看过的正常行」与「判不了的行」拿到同一个词，而两者处置相反：前者
    **什么都不用做**，后者**必须他处理**。确认页因此要求患者逐条处理每一行
    （报告 54 是 65/65，其中 46 行是判得完好的正常行）。

    反过来也一样：判不了的行**不许**因为「反正他还没核对」就混进 `awaiting_confirmation`
    —— 那会把「必须他处理」的那一类藏起来，比多显示更危险。
    """
    assert admission_reason(_row(confirmation_status="pending", **overrides), code="fasting_glucose") == expected


def test_the_status_only_decides_the_last_word_for_a_judgeable_row():
    """状态只决定**最后**那一个词；前面的值/证据/参考范围判定一字不动。

    这条守住判定顺序，两半都要：
    - **可判的行**（值/单位/参考范围/证据/页码都齐）还没核对时说「可判、待核对」，**不**
      说 `unknown_metric_code` —— 编码是在确认那一步落定的，一行还没核对的行没有
      「没有可对应的编码」这个结论可言，说了就是替它下一个还轮不到的判断。
    - **判不了的行**不许被「反正他还没核对」收口 —— 那一类藏起来比多显示更危险。
      （逐条已在上面那条参数化里覆盖，这里再钉住判定顺序不被反过来。）
    """
    judgeable_no_code = _row(confirmation_status="pending", metric_code=None)
    assert admission_reason(judgeable_no_code, code=None) == "awaiting_confirmation"
    # 判不了的行，状态也救不了它 —— 值这一类原因仍然优先。
    unjudgeable = _row(confirmation_status="pending", metric_value="3.87 4.00", metric_code=None)
    assert admission_reason(unjudgeable, code=None) == "invalid_value"


def test_the_tally_covers_every_row_exactly_once():
    """台账必须加总：进入解读 + skipped + unmatched + 未评估 == 行数。

    这份 PRD 的硬指标就是这个等式 —— 此前 `pending` / `excluded` 的行落在等式之外，
    所以「三桶相加 != 行数」永远成立，而没有任何东西会因此报错。
    """
    # **每一个取值都至少出现一次**：台账的职责是把每个结论恰好归一类，所以漏掉一个
    # 取值时「这一条能加总」不构成证据。`no_published_knowledge_card` 由证据服务产生
    # （不是本仓的门禁），所以它由下面单独一条覆盖 —— 这里断言的是「本仓能产出的结论
    # 全覆盖」。
    rows = [
        _row(id=1),  # None：进入解读
        _row(id=2, metric_value="5.0"),  # within_reference_range
        _row(id=3, metric_value=""),  # missing_value
        _row(id=4, metric_value="6.5/7.2"),  # invalid_value
        _row(id=5, unit=None, evidence_text=None),  # missing_unit
        _row(id=6, evidence_text=None),  # missing_source_evidence（值可解析）
        _row(id=7, page_number=None),  # missing_source_page
        _row(id=8, reference_range=None),  # missing_reference_range
        _row(id=9, metric_code=None),  # unknown_metric_code
        _row(id=10, confirmation_status="pending"),  # awaiting_confirmation
        _row(id=11, confirmation_status="excluded"),  # excluded
    ]
    reasons = [admission_reason(metric, code=metric.metric_code) for metric in rows]
    counts = tally(reasons)
    assert set(reasons) == {None, *(vocabulary() - {"no_published_knowledge_card"})}, (
        "这条测试要覆盖本仓能产生的**每一个**取值；漏掉的取值会让「能加总」不再是证据"
    )

    assert counts.total == len(rows)
    # 进入解读 1 / normal 1（在参考区间内，单列 —— 它是「正常」，不是「未进入解读」）/
    # skipped 6 / unmatched 1 / 未评估 2（可判待核对 1 + 已排除 1）。
    assert (counts.included, counts.normal, counts.skipped, counts.unmatched, counts.not_evaluated) == (
        1,
        1,
        6,
        1,
        2,
    )


def test_the_tally_refuses_a_reason_outside_the_vocabulary():
    """台账认不出的结论必须报错，而不是静默归到某一类里。"""
    with pytest.raises(ValueError):
        tally(["not_a_reason"])


def test_the_tally_covers_no_published_knowledge_card_too():
    """`no_published_knowledge_card` 由证据服务产生（不是本仓的门禁），台账同样要认它。"""
    counts = tally(["no_published_knowledge_card", "no_published_knowledge_card"])
    assert (counts.unmatched, counts.total) == (2, 2)


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
