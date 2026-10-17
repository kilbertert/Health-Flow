"""审计事件是单点产出的一条具名记录（GLOSSARY.md 的「审计事件」，#247）。

收敛前，审计流**没有家**：两个互不相识的生产者各写同一张表、用同一批 action 名字，
而全部 action 名只活在 `report_status.transition` 的 docstring 里。实测的四条后果，
本文件逐条钉住：

1. **一次确认落下两条 `confirmed`**（一条 `detail={}` 的空壳 + 一条带实体的），同一条
   `assessed` 有**两个 actor**；读的人无法判断该信哪一条。
2. **`uploaded` 只写事件、不走迁移**，worker 的「解析已提交、只差 ack」也写成一条与
   真的迁移同名的 `status_recovered` —— 审计流断言了一次「状态被恢复」，而状态机从未
   动过。两条在流里长得**完全一样**。
3. **action 词表没有主人**：契约是 `list[dict[str, Any]]`，列是 `String(64)`，
   拼错一个 action 不会有任何东西发现。
4. **worker 从 API 模块 import 私有写入口**（`from app.api.report import _audit`）。

两条实测（本文件的前两条用例）在改动前**必须是红的** —— 它们数的是真实的落库事件，
不是断言源码里出现了几次字符串。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base, MedicalReport, ReportAuditEvent, ReportExtractionJob
from app.service.audit_vocabulary import (
    AUDIT_ACTIONS,
    AUDIT_ACTIONS_ORDERED,
    AUDIT_ONLY_ACTIONS,
    TRANSITION_ACTIONS,
)
from app.service.report_status import record_event, transition

REPO_ROOT = Path(__file__).resolve().parents[1]
APP = REPO_ROOT / "app"


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _actions(factory, report_id: int) -> list[tuple[str, str]]:
    """(action, actor) 按落库顺序 —— 排障者读到的就是这一串。"""
    with factory() as db:
        return [
            (event.action, event.actor)
            for event in db.query(ReportAuditEvent)
            .filter(ReportAuditEvent.report_id == report_id)
            .order_by(ReportAuditEvent.id)
            .all()
        ]


# ── 1. 一次事件一条 ────────────────────────────────────────────────────────


def test_a_single_transition_writes_exactly_one_event_carrying_its_detail():
    """一次迁移 = 一条事件，且 detail 由调用处一次给全。

    改动前这里是两条：`transition(action=...)` 写 `detail={}`，紧跟的 `_audit` 再写
    一条带实体的同名事件。
    """
    factory = _factory()
    with factory() as db:
        report = MedicalReport(patient_id="P", owner_id="account:t:u", status="pending_confirmation")
        db.add(report)
        db.commit()
        report_id = report.id
        transition(
            report,
            "confirmed",
            db=db,
            action="confirmed",
            actor="account:t:u",
            detail={"confirmed_metric_ids": [1, 2]},
        )
        db.commit()

    assert _actions(factory, report_id) == [("confirmed", "account:t:u")]
    with factory() as db:
        event = db.query(ReportAuditEvent).one()
        assert event.detail == {"confirmed_metric_ids": [1, 2]}, "detail 必须在同一条上，不能再补一条"


# ── 2. 「状态动过」与「状态没动」必须长得不一样 ──────────────────────────────


def test_a_recorded_event_never_claims_a_transition():
    """`record_event` 只接受**审计专用**取值，迁移类取值被拒。

    这是那份「读者必须能分辨」的结构的落点：`uploaded` 与「已提交未 ack」不得再冒充
    一次迁移。
    """
    factory = _factory()
    with factory() as db:
        report = MedicalReport(patient_id="P", owner_id="account:t:u", status="processing")
        db.add(report)
        db.commit()
        before = report.status
        record_event(db, report, "uploaded", actor="account:t:u", detail={"file_count": 1})
        assert report.status == before, "记一笔不得改变状态"
        db.commit()

    assert AUDIT_ONLY_ACTIONS.isdisjoint(TRANSITION_ACTIONS), (
        "两个子集必须不相交：交集里的名字会让「状态动过没有」这个问题在数据上重新变得不可分辨"
    )
    with pytest.raises(ValueError, match="不是审计专用事件"), factory() as db:
        report = MedicalReport(patient_id="P", owner_id="account:t:u", status="processing")
        db.add(report)
        db.flush()
        record_event(db, report, "confirmed", actor="system")


def test_every_word_in_the_vocabulary_has_exactly_one_class():
    """每个取值恰好属于一组 —— 并集就是词表本身，两组不相交。"""
    assert set(AUDIT_ACTIONS_ORDERED) == AUDIT_ONLY_ACTIONS | TRANSITION_ACTIONS
    assert len(AUDIT_ACTIONS_ORDERED) == len(set(AUDIT_ACTIONS_ORDERED))
    assert set(AUDIT_ACTIONS_ORDERED) == AUDIT_ACTIONS


def test_an_action_outside_the_vocabulary_is_refused_at_the_write_point():
    """拼错一个 action 在写入点就被挡住（列是 String(64)，数据库不会替我们挡）。"""
    factory = _factory()
    with factory() as db:
        report = MedicalReport(patient_id="P", owner_id="account:t:u", status="pending_confirmation")
        db.add(report)
        db.flush()
        with pytest.raises(ValueError, match="不在审计事件词表里"):
            transition(report, "confirmed", db=db, action="confirmd", actor="system")


# ── 3. 结构守卫：一个生产者 ────────────────────────────────────────────────


def test_only_one_place_in_the_whole_app_constructs_an_audit_event():
    """**全仓库**产生 `ReportAuditEvent(` 的地方只有一处。

    按字面量计数而不是按名字：另一个模块重新 import 那个类再自己 `db.add` 一份，正是
    本次要消灭的形状（API 模块私有的 `_audit` 就是这么活下来的）。
    """
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if path.name in {"models.py", "audit_vocabulary.py"}:
            continue  # 模型定义本身；词表模块只有声明
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ReportAuditEvent":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == ["app/service/report_status.py:147"], (
        "审计事件的构造点必须只有 report_status 那一处，实际：" + str(offenders)
    )


def test_the_worker_does_not_import_a_private_write_entry_from_the_api_module():
    """worker 不得从 `app.api.report` import 私有写入口。

    它此前 `from app.api.report import ParseOutcome, _audit, ...` —— 一个 service 模块
    靠 API 模块的私有函数写审计。这里只钉审计那一条：`ParseOutcome` / `_parse_report`
    是解析职责，不是审计写入。
    """
    source = (APP / "service" / "report_worker.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "app.api.report":
            imported |= {alias.name for alias in node.names}
    assert "_audit" not in imported, "worker 仍在 import API 模块的私有审计写入口"


# ── 4. 契约类型化 ──────────────────────────────────────────────────────────


def test_the_contract_carries_a_typed_action_derived_from_the_one_vocabulary():
    """`audit_events` 不再是 `list[dict[str, Any]]`，action 与词表**派生**自同一处。"""
    from typing import get_args

    from app.schema.report import MedicalReportResponse, ReportAuditEventRecord

    field = MedicalReportResponse.model_fields["audit_events"]
    assert "dict[str, Any]" not in str(field.annotation), "契约仍是自由 dict"
    assert ReportAuditEventRecord.model_fields["action"].annotation is not None
    assert set(get_args(ReportAuditEventRecord.model_fields["action"].annotation)) == AUDIT_ACTIONS

    # 契约拒绝一个词表外的 action —— 一个旧前端或手写 payload 伪造不出来。
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReportAuditEventRecord(action="confirmd", actor="system", created_at="2026-01-01T00:00:00")


def test_history_rows_still_validate_against_the_vocabulary():
    """既有行里的 action 仍然读得懂 —— 这次改动**不改写历史**。

    实测：全部历史写入只有 8 个取值，与词表逐字相等。旧行里那两条曾经叫
    `status_recovered` 的「状态没动」事件仍是词表成员，所以契约读得懂它们，不需要
    改写库里的记录（审计是「发生过什么」的证据，改历史是编辑记录）。
    """
    recorded_history = {
        "uploaded",
        "extraction_partial",
        "extraction_completed",
        "extraction_failed",
        "extraction_retry_queued",
        "confirmed",
        "assessed",
        "status_recovered",
    }
    assert recorded_history <= AUDIT_ACTIONS, (
        "词表必须容得下已经写进库的每一个取值，否则历史报告会因为契约校验失败而打不开"
    )


def test_an_unacked_but_committed_parse_is_recorded_as_its_own_named_event():
    """「解析已提交、只差 ack」在流里不再冒充一次状态迁移。

    这条走的是**真实的** `recover_stale_jobs`（不是断言源码里出现了哪个字符串）：
    报告已经离开 `processing`，回收只动任务、不碰报告状态。
    """
    from datetime import UTC, datetime, timedelta

    from app.service.report_worker import recover_stale_jobs

    factory = _factory()
    stale_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
    with factory() as db:
        report = MedicalReport(patient_id="P", owner_id="account:t:u", status="pending_confirmation")
        db.add(report)
        db.flush()
        db.add(
            ReportExtractionJob(
                report_id=report.id, status="running", started_at=stale_at, updated_at=stale_at
            )
        )
        db.commit()
        report_id = report.id

    assert recover_stale_jobs(factory) == 0

    with factory() as db:
        report = db.get(MedicalReport, report_id)
        assert report.status == "pending_confirmation", "报告状态不该被碰"
        job = db.query(ReportExtractionJob).one()
        assert job.status == "completed"

    assert _actions(factory, report_id) == [("extraction_committed_unacked", "system:report-worker")]
    assert "extraction_committed_unacked" in AUDIT_ONLY_ACTIONS, "它必须是审计专用那一类"
