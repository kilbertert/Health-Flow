"""#294：系统分组装配 —— `system_groups` 的唯一产生处。

钉住的是**装配口径**：映射命中按声明序产出、解析不出的归「其他」、同组
「异常」与「待核对」并存不被一个组级词压掉、无检测数据的系统不出现、
未评估的报告没有分组结论（`None`，与准入台账同一约定）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.service.report_system_groups import system_groups


def _metric(id: int, *, code=None, name="", value="5.2", reference="3.9-6.1", status="confirmed", flag=None):
    """一条指标行：分组只读 id / metric_code / metric_name，计数只读值/范围/决策。"""
    return SimpleNamespace(
        id=id,
        metric_code=code,
        metric_name=name,
        metric_value=value,
        unit="mmol/L",
        reference_range=reference,
        confirmation_status=status,
        abnormal_flag=flag,
    )


def test_mapped_metrics_land_in_their_groups_in_declaration_order():
    """产出顺序是映射声明序，不是指标行序 —— `other` 恒在最后。"""
    groups = system_groups(
        [
            _metric(1, code="uncorrected_vision"),  # eye
            _metric(2, code="fasting_glucose"),  # cardiovascular_metabolic
            _metric(3, code="alt"),  # hepatobiliary
        ]
    )
    assert [g.key for g in groups] == [
        "cardiovascular_metabolic",
        "hepatobiliary",
        "eye",
    ]
    assert groups[0].name == "心血管与代谢"
    assert groups[0].metric_ids == [2]


def test_unmapped_metrics_fall_into_other_and_other_stays_last():
    """解析不出 canonical 码的行归「其他」—— 不猜归属（含目录里没有的编码）。"""
    groups = system_groups(
        [
            _metric(1, code="psa"),  # special_marker
            _metric(2, code="no_such_code"),  # 不在映射表
            _metric(3, code=None, name="不认识的指标X"),  # 无码且名称解析不出
        ],
        catalog=["psa"],
    )
    assert [g.key for g in groups] == ["special_marker", "other"]
    assert groups[-1].metric_ids == [2, 3]


def test_unsettled_code_resolves_by_name_with_the_same_priority_as_the_bridge():
    """落定码为空时按名称重裁决 —— 与证据桥同一优先级（落定码 > 目录解析），不另起判据。"""
    # 目录含 fasting_glucose：名称解析命中 → 归组而非「其他」。
    resolved = system_groups([_metric(1, code=None, name="空腹血糖")], catalog=["fasting_glucose"])
    assert [g.key for g in resolved] == ["cardiovascular_metabolic"]
    # 目录里查无此码：不猜，落「其他」（与证据门禁「目录不可用就不匹配」同一条规矩）。
    unresolved = system_groups([_metric(1, code=None, name="空腹血糖")], catalog=["creatinine"])
    assert [g.key for g in unresolved] == ["other"]


def test_abnormal_and_undecided_coexist_in_one_group_without_a_group_state():
    """同组异常与待核对并存：两个都在计数里，组本身没有单一状态可压掉它们。"""
    groups = system_groups(
        [
            _metric(1, code="fasting_glucose", value="6.5"),  # 判定 H
            _metric(2, code="hba1c", value="6.5/7.2"),  # 判不了
        ]
    )
    assert len(groups) == 1
    group = groups[0]
    assert group.metric_ids == [1, 2]
    assert group.counts.abnormal == 1
    assert group.counts.undecided == 1
    assert group.counts.parsed == 2


def test_systems_without_measurements_do_not_appear():
    """组里没有指标就不产出 —— 不在无检测数据时宣称器官健康（含「其他」）。"""
    groups = system_groups([_metric(1, code="alt"), _metric(2, code="ast")])
    assert [g.key for g in groups] == ["hepatobiliary"]
    assert "other" not in {g.key for g in groups}


def test_excluded_metric_stays_a_member_but_is_not_counted():
    """患者排除的行仍是组成员（它还在报告上），但计数口径与全报告同一判据。"""
    groups = system_groups(
        [
            _metric(1, code="fasting_glucose", value="9.9", status="excluded"),
            _metric(2, code="hba1c", value="5.2"),
        ]
    )
    assert groups[0].metric_ids == [1, 2]
    assert groups[0].counts.parsed == 2
    assert groups[0].counts.abnormal == 0  # 排除的不计异常
    assert groups[0].counts.in_range == 1


# ---------------------------------------------------------------------------
# 响应级：出域形状与「无结论 → None」的约定
# ---------------------------------------------------------------------------


def _assessed_report(**metric_overrides):
    """一份真实跑过评估的报告（sqlite + 真实 `_assess_report`，证据上游打桩）。"""
    import tests.test_report_confirmation as T
    from app.api.report import _assess_report, _ordered_metrics, _report_response

    session, report = T._assessment_fixture(**metric_overrides)
    with patch("app.api.report.match_published_evidence", return_value=T._evidence_result()):
        asyncio.run(_assess_report(report, session))
    session.refresh(report)
    metrics = _ordered_metrics(session, report.id).all()
    return session, _report_response(report, metrics)


def test_assessed_report_carries_groups_with_metric_backlinks():
    """装配进响应：每组带 `metric_ids` 回链 —— 指标自己的原文/页码随响应出域。"""
    session, response = _assessed_report(metric_code="fasting_glucose", metric_value="6.5")
    try:
        assert response.system_groups is not None
        assert [g.key for g in response.system_groups] == ["cardiovascular_metabolic"]
        assert response.system_groups[0].metric_ids == [m.id for m in response.metrics]
        assert response.system_groups[0].counts.parsed == 1
        assert response.system_groups[0].counts.abnormal == 1
    finally:
        session.close()


def test_pre_assessment_report_has_no_grouping_conclusion():
    """还没评估 → `system_groups` 是 `None`：与准入台账「没跑过就没有结论」同一约定。"""
    import tests.test_report_confirmation as T
    from app.api.report import _ordered_metrics, _report_response

    session, report = T._assessment_fixture(metric_code="fasting_glucose", metric_value="6.5")
    try:
        assert report.status == "confirmed"
        response = _report_response(report, _ordered_metrics(session, report.id).all())
        assert response.system_groups is None
    finally:
        session.close()


def test_unsettled_metric_without_catalog_is_other():
    metric = SimpleNamespace(
        id=1,
        metric_code=None,
        metric_name="总胆固醇",
        metric_value="5.2",
        unit="mmol/L",
        reference_range="<5.2",
        confirmation_status="confirmed",
        abnormal_flag="H",
    )
    assert system_groups([metric], catalog=None)[0].key == "other"


def test_pending_provisional_code_is_not_a_verified_group():
    for catalog in (None, ["fasting_glucose"], ["creatinine"]):
        groups = system_groups([_metric(1, code="fasting_glucose", name="空腹血糖", status="pending")], catalog=catalog)
        assert groups[0].key == "other"


def test_assessment_persists_resolved_code_for_reopening():
    import tests.test_report_confirmation as T
    from app.api.report import _assess_report, _ordered_metrics, _report_response

    session, report = T._assessment_fixture(metric_code=None, metric_name="空腹血糖", metric_value="6.5")
    try:
        with (
            patch(
                "app.api.report.fetch_metric_catalog", return_value=[{"code": "fasting_glucose", "label": "空腹血糖"}]
            ),
            patch("app.api.report.match_published_evidence", return_value=T._evidence_result()),
        ):
            first = asyncio.run(_assess_report(report, session))
        session.expire_all()
        reread = _report_response(report, _ordered_metrics(session, report.id).all())
        assert first.system_groups == reread.system_groups
        assert reread.metrics[0].metric_code == "fasting_glucose"
        assert reread.system_groups[0].key == "cardiovascular_metabolic"
    finally:
        session.close()
