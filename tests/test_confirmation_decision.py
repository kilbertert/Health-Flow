"""指标确认决策：一份词表、一个入口、请求不再能静默排除（#194）。

本文件钉住的是**外部可观测行为**与**声明之间的关系**，不是实现细节：

1. 同一行在「持久化状态」与「生效值来源」两侧给出**同一个名字**（此前 `pending` 与
   `extracted` 分家）。
2. 闸门只有一处判定来源（此前一个正面枚举 + 一个反向排除）。
3. 准入词表对决策的读出是**派生**的，不是第三份抄写。
4. 一次确认**必须覆盖全部**已解析指标，缺谁就 422 点名。

第 4 条是本票最要紧的行为变化：此前「没提供」被静默当成「排除」—— 一条漏提交的已解析指标
会被置成 `excluded`，与患者明确排除不可区分，而患者与客户端都无从知道。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.service.confirmation_decision import (
    effective_source,
    is_admitted,
    is_excluded,
    request_decision,
    require_full_coverage,
)
from app.service.confirmation_vocabulary import (
    ADMITTED_DECISIONS,
    DECISIONS,
    EXCLUDED_DECISIONS,
    REQUEST_DECISIONS,
    UNDECIDED_DECISIONS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 1. 同一事实只有一个名字 ─────────────────────────────────────────────────


def test_the_persisted_status_and_the_effective_source_use_one_vocabulary():
    """`source` 与 `confirmation_status` 是同一份词汇的两个读法。

    这条是本票的核心断言：此前 `pending` 在生效值那一侧改叫 `extracted`，于是「按 source
    分支」与「按 status 分支」的消费者写出的是同一份逻辑的两份实现。
    """
    from app.data.models import MetricRecord
    from app.service.metric_effective_value import effective_value

    for status in sorted(DECISIONS):
        metric = MetricRecord(metric_name="x", metric_value="1", confirmation_status=status)
        effective = effective_value(metric)
        assert effective.source == status, (status, effective.source)


def test_every_decision_maps_to_itself_as_a_source():
    for decision in sorted(DECISIONS):
        assert effective_source(decision) == decision


def test_an_unknown_decision_is_rejected_instead_of_silently_becoming_pending():
    """词表外的状态必须当场报错，不能被静默当成「还没处理」。"""
    with pytest.raises(ValueError):
        effective_source("something-from-the-future")


def test_a_row_with_no_status_at_all_is_undecided_not_an_error():
    """**没有状态**的行是「尚未决定」，不是异常。

    这条边界有两个真实来源：确认页在评估之前发送的指标（`confirmation_status: null`）与
    更旧的响应/行。DB 列是 `NOT NULL DEFAULT 'pending'`，但读路径不该因为收到 `None` 就
    崩 —— 词表外的**字面量**要报错（见下一条），而「没给」是「还没决定」的合法写法。
    """
    from app.data.models import MetricRecord
    from app.service.metric_effective_value import effective_value

    metric = MetricRecord(metric_name="x", metric_value="1", confirmation_status=None)
    assert effective_value(metric).source == "pending"
    assert not is_excluded(None)
    assert not is_admitted(None)


def test_the_predicates_fail_closed_on_an_unknown_value():
    """词表外的值在**三个判定入口**上一律报错，不 fail open。

    fail open 的代价不对称：`is_admitted("Confrimed")` 若返回 True，一条**患者从未确认过**
    的行会被当成已确认参与解读；`is_excluded("excluuded")` 若返回 False，一条患者明确排除的
    行会重新进入解读。两个方向都是「静默地按错误的一方行事」，而报错是可查的：读路径会
    「报告打不开」，而不是「报告里多了几行」。
    """
    for unknown in ("Confrimed", "excluuded", "bogus"):
        for call in (is_admitted, is_excluded, effective_source):
            with pytest.raises(ValueError):
                call(unknown)


def test_an_unknown_literal_status_is_rejected_not_silently_treated_as_undecided():
    """词表外的**值**要当场报错 —— 不静默降级。

    DB 列是 `VARCHAR(16)` 且**没有 CHECK 约束**，所以一个拼错的字面量能落库。此前它会被
    静默当成 `extracted`（= 患者还没处理）—— 一条「已确认」的行因为拼错而被当成未确认，
    而没有任何东西会因此报错。现在它在读路径当场失败。
    """
    from app.data.models import MetricRecord
    from app.service.metric_effective_value import effective_value

    metric = MetricRecord(metric_name="x", metric_value="1", confirmation_status="confrimed")
    with pytest.raises(ValueError):
        effective_value(metric)


# ── 2. 一份词表：请求是它的子集，不是另写一份 ────────────────────────────────


def test_the_request_vocabulary_can_say_undecided_too():
    """请求**必须**能表达「这条我还没动」。

    它必须能，因为服务端要求**覆盖全部**已解析指标（否则分不清「没提交」与「排除了」）。
    而它表达的不是「患者决定成还没决定」—— 那是「患者对这条还没表态」，是一个诚实的表态，
    由服务端解成该行已落定的状态（见 `test_an_undecided_request_row_keeps_what_the_server_already_decided`）。
    """
    assert REQUEST_DECISIONS == DECISIONS
    assert "pending" in REQUEST_DECISIONS


def test_an_undecided_request_row_keeps_what_the_server_already_decided():
    """请求里的「还没动」解出来是什么 —— 由**服务端**决定，不由客户端猜。

    这是 #195 的另一半：客户端可以说「我没动这一行」，但**不能**说「所以它应该是 X」。
    落定过的沿用上次的决定（患者在界面上没动它＝「和上次一样」，不是「撤回上次」）；
    从没落定过的仍是「尚未决定」。
    """

    assert request_decision("pending", status="pending") == "pending"
    assert request_decision("pending", status=None) == "pending"
    # 重入确认：患者上次排除了它，这次没动它 —— 仍然是排除。
    assert request_decision("pending", status="excluded") == "excluded"
    assert request_decision("pending", status="corrected") == "corrected"
    # 患者明确表态时，他的表态优先于落定状态。
    assert request_decision("confirmed", status="excluded") == "confirmed"
    # 坏数据仍然报错（与其余入口一致）。
    with pytest.raises(ValueError):
        request_decision("pending", status="confimed")


def test_the_contracts_derive_their_literals_from_the_vocabulary():
    """请求与持久化两份契约的 Literal 恰好是词表的那两个子集。"""
    import typing

    from app.schema.report import MetricConfirmation, MetricRecord

    request = set(typing.get_args(MetricConfirmation.model_fields["decision"].annotation))
    persisted = set(typing.get_args(MetricRecord.model_fields["confirmation_status"].annotation))
    assert request == set(REQUEST_DECISIONS)
    assert persisted == set(DECISIONS)


# ── 3. 闸门只有一处来源 ─────────────────────────────────────────────────────


def test_the_gate_reads_one_declaration():
    """准入闸门与判定守卫读**同一份声明**。

    此前门禁写的是正面枚举（`status in ADMITTED_STATUSES`），判定守卫写的是反向排除
    （`status != "excluded"`）—— 同一条判定的两份写法。它们的差别在**新增一种决策**时
    才显形：反向那个会默默放行新值。
    """
    from app.service.admission_vocabulary import ADMITTED_STATUSES
    from app.service.evidence_bridge import _decidable

    assert set(ADMITTED_DECISIONS) == ADMITTED_STATUSES
    for decision in sorted(DECISIONS):
        metric = type("M", (), {"confirmation_status": decision})()
        admitted_by_enum = decision in ADMITTED_DECISIONS
        assert is_admitted(decision) == admitted_by_enum
        # 判定守卫对 `pending` 也作答（那正是确认页要患者看到异常候选的场景），
        # 所以它与门禁的差别**只有** excluded 那一个 —— 而那个差别由 `is_excluded`
        # 这一个入口给出，不是另写一遍「不等于 excluded」。
        assert _decidable(metric) is not is_excluded(decision)


def test_admitted_and_excluded_partition_the_decisions():
    """「进入解读」与「不进入解读」恰好把词表分完，没有第三种去向。"""
    assert ADMITTED_DECISIONS | UNDECIDED_DECISIONS | EXCLUDED_DECISIONS == DECISIONS


# ── 4. 请求全量性：未提供不再等于排除 ───────────────────────────────────────


def test_coverage_reports_the_missing_ids_by_name():
    gap = require_full_coverage([1, 3], [1, 2, 3])
    assert not gap.ok
    assert gap.missing_ids == (2,)
    assert "2" in gap.detail()


def test_coverage_reports_unknown_ids_too():
    gap = require_full_coverage([1, 9], [1])
    assert gap.extra_ids == (9,)
    assert "9" in gap.detail()


def test_coverage_is_order_independent_and_accepts_a_full_set():
    assert require_full_coverage([3, 1, 2], [1, 2, 3]).ok
    assert require_full_coverage([], []).ok


def _owner():
    return type("O", (), {"storage_id": "account:t:u", "subject": "a"})()


def _fake_request():
    """`confirm_report` 只从 Request 上取 header（以及 `resolve_owner` 被打桩后就不取了）。"""
    return type("R", (), {"headers": {}})()


def _confirmation_fixture(**overrides):
    """一份报告 + 两条指标，用来构造「只提交一部分」的确认请求。"""
    import tests.test_report_confirmation as T

    session, report = T._assessment_fixture(**overrides)
    from app.data.models import MetricRecord as MetricModel

    session.add(
        MetricModel(
            report_id=report.id,
            metric_name="总胆固醇",
            metric_value="5.5",
            unit="mmol/L",
            reference_range="3.9-5.2",
            abnormal_flag="H",
            page_number=1,
            evidence_text="总胆固醇 5.5 mmol/L 3.9-5.2",
            source_file_index=1,
            confirmation_status="pending",
        )
    )
    session.commit()
    return session, report


def test_a_partial_confirmation_is_refused_instead_of_silently_excluding_the_rest():
    """只提交一部分 → 422，并**点名**缺了哪些。

    这是本票最要紧的行为变化。此前缺的那些行被静默置成 `excluded`，与患者明确排除
    不可区分（`confirmed_at` 照写、审计把两者合在一个数组里）。
    """
    from app.api.report import confirm_report
    from app.schema.report import MetricConfirmation, ReportConfirmationRequest

    session, report = _confirmation_fixture()
    report.owner_id = "account:t:u"
    session.commit()
    ids = sorted(metric.id for metric in report.metrics)
    assert len(ids) >= 2, "这条用例需要一个以上的已解析指标"

    request = ReportConfirmationRequest(
        observations=[MetricConfirmation(metric_id=ids[0], decision="confirmed")],
        subject_consistency="same",
    )
    with (
        patch("app.api.report.resolve_owner", return_value=_owner()),
        pytest.raises(HTTPException) as excinfo,
    ):
        asyncio.run(confirm_report(report.id, _fake_request(), request, db=session))

    assert excinfo.value.status_code == 422
    assert "确认列表不完整" in excinfo.value.detail
    assert str(ids[1]) in excinfo.value.detail, "缺的那条要被点名"
    session.close()


def test_an_untouched_row_keeps_its_status_instead_of_being_excluded():
    """**本票的核心**：患者没动过的行，落库时**保持原样**，不再被静默置成 excluded。

    此前客户端把「界面默认」当患者的表态提交，于是「患者没看过的正常行」被记成「患者已
    排除」。现在客户端说 `pending`（「我没动这一行」），服务端把它解成**该行已落定的状态**：
    没落定过 → 仍是 `pending`（不是 `excluded`）。
    """
    from app.api.report import confirm_report
    from app.schema.report import MetricConfirmation, ReportConfirmationRequest
    from app.service.evidence_bridge import EvidenceBridgeError

    # 夹具的第一条是 `confirmed`（夹具默认）、第二条是 `pending`（本文件加的）——
    # 两种「没动过」都要保持原样，这正是这条用例要覆盖的：**落定过的沿用、没落定的仍是未决**。
    session, report = _confirmation_fixture()
    report.owner_id = "account:t:u"
    session.commit()
    ids = sorted(metric.id for metric in report.metrics)
    before = {metric.id: metric.confirmation_status for metric in report.metrics}
    assert set(before.values()) == {"confirmed", "pending"}, before

    request = ReportConfirmationRequest(
        observations=[MetricConfirmation(metric_id=metric_id, decision="pending") for metric_id in ids],
        subject_consistency="same",
    )
    with (
        patch("app.api.report.fetch_metric_catalog", side_effect=EvidenceBridgeError("目录暂不可用")),
        patch("app.api.report._assess_report", side_effect=EvidenceBridgeError("证据服务暂不可用")),
        patch("app.api.report.resolve_owner", return_value=_owner()),
        pytest.raises(HTTPException) as excinfo,
    ):
        asyncio.run(confirm_report(report.id, _fake_request(), request, db=session))

    assert excinfo.value.status_code == 503, "走到评估那一步，说明覆盖面与解算都过了"
    from app.data.models import MetricRecord as MetricModel

    saved = session.query(MetricModel).filter(MetricModel.report_id == report.id).all()
    assert {metric.id: metric.confirmation_status for metric in saved} == before, (
        "没动过的行必须保持原样（落定过的沿用、没落定的仍是未决），不得被记成「患者已排除」"
    )
    session.close()


def test_a_reentry_row_keeps_the_decision_the_patient_made_last_time():
    """重入确认：患者上次的决定，在他这次没动它时**不被撤回**。"""
    from app.api.report import confirm_report
    from app.schema.report import MetricConfirmation, ReportConfirmationRequest
    from app.service.evidence_bridge import EvidenceBridgeError

    session, report = _confirmation_fixture(confirmation_status="excluded")
    report.owner_id = "account:t:u"
    session.commit()
    ids = sorted(metric.id for metric in report.metrics)
    before = {metric.id: metric.confirmation_status for metric in report.metrics}

    request = ReportConfirmationRequest(
        observations=[MetricConfirmation(metric_id=metric_id, decision="pending") for metric_id in ids],
        subject_consistency="same",
    )
    with (
        patch("app.api.report.fetch_metric_catalog", side_effect=EvidenceBridgeError("目录暂不可用")),
        patch("app.api.report._assess_report", side_effect=EvidenceBridgeError("证据服务暂不可用")),
        patch("app.api.report.resolve_owner", return_value=_owner()),
        pytest.raises(HTTPException),
    ):
        asyncio.run(confirm_report(report.id, _fake_request(), request, db=session))

    from app.data.models import MetricRecord as MetricModel

    saved = session.query(MetricModel).filter(MetricModel.report_id == report.id).all()
    assert {metric.id: metric.confirmation_status for metric in saved} == before
    session.close()


def test_a_full_confirmation_still_goes_through():
    """全覆盖的请求照常通过 —— 这条约束不该拒掉正常调用（前端本来就发全部）。"""
    from app.api.report import confirm_report
    from app.schema.report import MetricConfirmation, ReportConfirmationRequest
    from app.service.evidence_bridge import EvidenceBridgeError

    session, report = _confirmation_fixture()
    report.owner_id = "account:t:u"
    session.commit()
    ids = sorted(metric.id for metric in report.metrics)
    request = ReportConfirmationRequest(
        observations=[MetricConfirmation(metric_id=metric_id, decision="confirmed") for metric_id in ids],
        subject_consistency="same",
    )
    with (
        patch("app.api.report.fetch_metric_catalog", side_effect=EvidenceBridgeError("目录暂不可用")),
        patch("app.api.report._assess_report", side_effect=EvidenceBridgeError("证据服务暂不可用")),
        patch("app.api.report.resolve_owner", return_value=_owner()),
        pytest.raises(HTTPException) as excinfo,
    ):
        asyncio.run(confirm_report(report.id, _fake_request(), request, db=session))

    # 走到评估那一步（证据服务被打桩成不可用 → 503），说明**覆盖面这一关过了**；
    # 若覆盖面被拒，这里会是 422 而不是 503。
    assert excinfo.value.status_code == 503
    from app.data.models import MetricRecord as MetricModel

    saved = session.query(MetricModel).filter(MetricModel.report_id == report.id).all()
    assert all(metric.confirmation_status == "confirmed" for metric in saved)
    session.close()
