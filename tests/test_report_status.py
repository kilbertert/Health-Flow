"""报告状态机（GLOSSARY.md 的「报告状态」）。

收敛前，状态由四个平行通道回答：``MedicalReport.status`` 列、``parsed_content``
JSON、``ReportExtractionJob.status``、审计 action 词汇。写入点散在 API 与 worker
共八处，闸门从 JSON 重新推导，``recover_stale_jobs`` 改状态时不写审计。

本文件钉住收敛后的三条外部行为：

1. **合法迁移通过、非法迁移被拒**（例如已评估的报告不能退回待确认）；
2. **每次迁移都留下一条审计事件**，包括回收卡住的任务；
3. **闸门由状态决定**：仍有文件未解析完的报告状态是 ``processing``，走不进确认。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.service.report_status import PATIENT_STATUSES, IllegalTransition, transition


class _Report:
    """报告行的最小替身：迁移只读 status，审计只读 id 与 correlation_id。"""

    def __init__(self, status: str | None = None) -> None:
        self.status = status
        self.id = 7
        self.evidence_correlation_id = "corr-1"


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (None, "processing"),                     # 上传:构造后的第一次迁移
        ("processing", "processing"),             # 重试再入队
        ("processing", "pending_confirmation"),   # 解析完成
        ("processing", "failed"),                 # 解析失败
        ("pending_confirmation", "confirmed"),    # 患者确认
        ("confirmed", "assessed"),                # 生成健康提示
        ("assessed", "assessed"),                 # 重新生成
        ("failed", "processing"),                 # 失败后重新上传解析
    ],
)
def test_allowed_transitions(current, target):
    report = _Report(current)
    transition(report, target)
    assert report.status == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("assessed", "pending_confirmation"),  # 已生成提示的报告不能退回待确认
        ("assessed", "confirmed"),
        ("assessed", "processing"),
        ("confirmed", "pending_confirmation"),
        ("confirmed", "processing"),
        ("pending_confirmation", "assessed"),  # 没确认过就不能生成提示
        ("pending_confirmation", "processing"),
        ("failed", "pending_confirmation"),    # 失败后要走重新解析,不能直接待确认
    ],
)
def test_illegal_transitions_are_rejected(current, target):
    report = _Report(current)
    with pytest.raises(IllegalTransition):
        transition(report, target)
    # 被拒绝时状态**不变** —— 半途改状态会让报告卡在一个无人认领的阶段。
    assert report.status == current


def test_patient_status_vocabulary_is_closed():
    """患者可见的状态就是这五个。"""
    assert set(PATIENT_STATUSES) == {"processing", "pending_confirmation", "confirmed", "assessed", "failed"}


def test_transition_writes_exactly_one_audit_event():
    """每次迁移伴随一条审计事件，action 由调用方给出（它是对应的领域事件）。"""
    added: list[object] = []
    db = SimpleNamespace(add=lambda obj: added.append(obj))

    report = _Report("pending_confirmation")
    transition(report, "confirmed", db=db, action="confirmed", actor="account:t:u", detail={"n": 1})

    assert report.status == "confirmed"
    assert len(added) == 1
    event = added[0]
    assert event.report_id == report.id
    assert event.action == "confirmed"
    assert event.actor == "account:t:u"
    assert event.detail == {"n": 1}


def test_transition_without_db_only_sets_status():
    """上传路径在对象落库前设置状态，那时还没有 id 可写审计事件。"""
    report = _Report(None)
    transition(report, "processing")
    assert report.status == "processing"


def test_recover_stale_jobs_leaves_an_audit_trail():
    """回收卡住的任务不再是无声的批量 update。

    收敛前 `recover_stale_jobs` 用一次 `update()` 把 running 翻回 queued，
    **不写任何审计事件** —— 事后无法回答「这份报告为什么又被解析了一遍」。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.data.models import Base, MedicalReport, ReportAuditEvent, ReportExtractionJob
    from app.service.report_worker import recover_stale_jobs

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as db:
        report = MedicalReport(patient_id="P", owner_id="account:t:u", status="processing")
        db.add(report)
        db.flush()
        db.add(
            ReportExtractionJob(
                report_id=report.id,
                status="running",
                started_at=datetime.now(UTC) - timedelta(hours=2),
                updated_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
        db.commit()

    assert recover_stale_jobs(SessionLocal) == 1

    with SessionLocal() as db:
        job = db.query(ReportExtractionJob).one()
        assert job.status == "queued"
        events = db.query(ReportAuditEvent).all()
        assert [event.action for event in events] == ["status_recovered"]
        assert events[0].detail["reason"] == "stale_running_job"


def test_recover_stale_jobs_leaves_a_finished_report_alone():
    """回收卡住的任务时，不把**已经解析完**的报告退回解析中。

    这条是评审发现的：解析成功、worker 在 ack 之前重启时，任务仍是 running，
    但报告已经是 pending_confirmation。把它退回 processing 会让患者核对过的行
    被重新解析清空 —— 迁移表拒绝这次迁移是对的。回收只动任务，报告只在它确实
    还没离开解析阶段时才改；无论哪种情况都留审计。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.data.models import Base, MedicalReport, ReportAuditEvent, ReportExtractionJob
    from app.service.report_worker import recover_stale_jobs

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    stale_at = datetime.now(UTC) - timedelta(hours=2)
    with SessionLocal() as db:
        finished = MedicalReport(patient_id="P", owner_id="account:t:u", status="pending_confirmation")
        db.add(finished)
        db.flush()
        db.add(ReportExtractionJob(report_id=finished.id, status="running", started_at=stale_at, updated_at=stale_at))
        db.commit()

    assert recover_stale_jobs(SessionLocal) == 1

    with SessionLocal() as db:
        assert db.query(ReportExtractionJob).one().status == "queued"
        report = db.query(MedicalReport).one()
        assert report.status == "pending_confirmation"  # 没有被退回解析中
        events = db.query(ReportAuditEvent).all()
        assert [event.action for event in events] == ["status_recovered"]
        assert events[0].detail["report_status"] == "pending_confirmation"


def test_parse_writes_exactly_one_audit_event_per_run():
    """一次解析只留一条 extraction_* 事件（迁移入口与旧的手写审计不重复）。"""
    import inspect

    from app.api import report as report_api

    source = inspect.getsource(report_api._parse_report)
    assert source.count('action="extraction_partial" if warnings else "extraction_completed"') == 1
    assert "_audit(\n                db,\n                report,\n                \"extraction_partial\"" not in source
