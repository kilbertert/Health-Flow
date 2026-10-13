from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.report import ParseOutcome
from app.data.models import Base, MedicalReport, MetricRecord, ReportExtractionJob, ReportFile
from app.service.report_worker import claim_next_job, recover_stale_jobs, run_next_job


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_report_job_is_claimed_once_and_persisted(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        report = MedicalReport(patient_id="p1", status="processing")
        db.add(report)
        db.flush()
        db.add(ReportExtractionJob(report_id=report.id, status="queued"))
        db.commit()

    job_id = claim_next_job(factory)
    assert job_id is not None
    assert claim_next_job(factory) is None
    with factory() as db:
        job = db.get(ReportExtractionJob, job_id)
        assert job.status == "running"
        assert job.attempt_count == 1


def test_stale_report_job_returns_to_queue(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        report = MedicalReport(patient_id="p1", status="processing")
        db.add(report)
        db.flush()
        db.add(
            ReportExtractionJob(
                report_id=report.id,
                status="running",
                started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
                updated_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
            )
        )
        db.commit()

    assert recover_stale_jobs(factory) == 1
    with factory() as db:
        assert db.query(ReportExtractionJob).one().status == "queued"


def test_worker_reads_saved_files_and_completes_job(tmp_path):
    factory = _factory(tmp_path)
    file_path = Path(tmp_path) / "report.png"
    file_path.write_bytes(b"report")
    with factory() as db:
        report = MedicalReport(patient_id="p1", status="processing")
        db.add(report)
        db.flush()
        db.add(
            ReportFile(
                report_id=report.id,
                file_index=1,
                original_filename="report.png",
                media_type="image/png",
                stored_path=str(file_path),
                page_count=1,
            )
        )
        db.add(ReportExtractionJob(report_id=report.id, status="queued"))
        db.commit()

    with patch(
        "app.service.report_worker._parse_report",
        return_value=ParseOutcome(succeeded=True),
    ) as parse:
        assert run_next_job(factory) is not None
    parse.assert_called_once()
    with factory() as db:
        assert db.query(ReportExtractionJob).one().status == "completed"


def test_retryable_report_job_is_requeued_then_completed(tmp_path):
    """可重试的成因 → 重排队 → 下一次成功。**重试判定的唯一输入是成因**。

    收敛前这一条驱动的是 `report.parsed_content = {"retryable": ...}` —— 一个由子串
    嗅探得出的布尔。现在它驱动的是一次解析交出的 `ParseOutcome.failure`。
    """
    factory = _factory(tmp_path)
    file_path = Path(tmp_path) / "report.png"
    file_path.write_bytes(b"report")
    with factory() as db:
        report = MedicalReport(patient_id="p1", status="processing")
        db.add(report)
        db.flush()
        db.add(
            ReportFile(
                report_id=report.id,
                file_index=1,
                original_filename="report.png",
                media_type="image/png",
                stored_path=str(file_path),
                page_count=1,
            )
        )
        db.add(ReportExtractionJob(report_id=report.id, status="queued"))
        db.commit()

    attempts = 0

    def parse(report_id, accepted_files, session_factory):
        nonlocal attempts
        attempts += 1
        with session_factory() as db:
            report = db.get(MedicalReport, report_id)
            report.parsed_content = {"failure": "provider_unavailable"}
            db.commit()
        if attempts == 1:
            return ParseOutcome(succeeded=False, failure="provider_unavailable")
        return ParseOutcome(succeeded=True)

    with patch("app.service.report_worker._parse_report", side_effect=parse):
        assert run_next_job(factory) is not None
        with factory() as db:
            job = db.query(ReportExtractionJob).one()
            assert job.status == "queued"
            assert job.attempt_count == 1
        assert run_next_job(factory) is not None

    with factory() as db:
        job = db.query(ReportExtractionJob).one()
        assert job.status == "completed"
        assert job.attempt_count == 2


def test_an_unretryable_cause_fails_the_job_on_the_first_attempt(tmp_path):
    """不可重试的成因（凭据被拒）一次就落终态 —— 今天它会重跑三次，因为「401」不在
    子串表里……而 `insufficient_quota` 一次都不重试。两个方向都要被钉住。"""
    factory = _factory(tmp_path)
    file_path = Path(tmp_path) / "report.png"
    file_path.write_bytes(b"report")
    with factory() as db:
        report = MedicalReport(patient_id="p1", status="processing")
        db.add(report)
        db.flush()
        db.add(
            ReportFile(
                report_id=report.id,
                file_index=1,
                original_filename="report.png",
                media_type="image/png",
                stored_path=str(file_path),
                page_count=1,
            )
        )
        db.add(ReportExtractionJob(report_id=report.id, status="queued"))
        db.commit()
        report_id = report.id

    with patch(
        "app.service.report_worker._parse_report",
        return_value=ParseOutcome(succeeded=False, failure="provider_rejected"),
    ):
        assert run_next_job(factory) is not None

    with factory() as db:
        job = db.query(ReportExtractionJob).one()
        assert job.status == "failed"
        assert job.attempt_count == 1
        assert db.get(MedicalReport, report_id).status == "failed"


def test_a_failed_report_a_patient_already_confirmed_is_never_retried(tmp_path):
    """一次重试会把已解析的指标行**全部删掉重插为 pending** —— 患者核对过的行会连同
    他的表态一起消失。

    这条路径上报告在**开始解析之前**就已经离开了 `processing`，所以 `_parse_report`
    根本不会被调用（同一形状在 `recover_stale_jobs` 里也是这么处置的）。收敛前这条
    路径会调 `transition(report, "processing")`，而 `confirmed → processing` 不在允许
    表里 → `IllegalTransition` 从 `run_next_job` 里抛出去，**worker 直接死掉**。
    """
    factory = _factory(tmp_path)
    file_path = Path(tmp_path) / "report.png"
    file_path.write_bytes(b"report")
    with factory() as db:
        report = MedicalReport(patient_id="p1", status="confirmed")
        db.add(report)
        db.flush()
        db.add(
            MetricRecord(
                report_id=report.id,
                metric_name="空腹血糖",
                metric_value="6.8",
                confirmation_status="confirmed",
            )
        )
        db.add(
            ReportFile(
                report_id=report.id,
                file_index=1,
                original_filename="report.png",
                media_type="image/png",
                stored_path=str(file_path),
                page_count=1,
            )
        )
        db.add(ReportExtractionJob(report_id=report.id, status="queued"))
        db.commit()
        report_id = report.id

    with patch("app.service.report_worker._parse_report") as parse:
        assert run_next_job(factory) is not None
    parse.assert_not_called()

    with factory() as db:
        job = db.query(ReportExtractionJob).one()
        # 这一轮什么都没解析，所以没有失败可报，也不该往患者已经看到的东西里写。
        assert job.status == "completed"
        report = db.get(MedicalReport, report_id)
        assert report.status == "confirmed"
        assert db.query(MetricRecord).filter(MetricRecord.report_id == report_id).count() == 1


def test_worker_marks_missing_saved_file_as_failed(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        report = MedicalReport(patient_id="p1", status="processing")
        db.add(report)
        db.flush()
        db.add(
            ReportFile(
                report_id=report.id,
                file_index=1,
                original_filename="missing.png",
                media_type="image/png",
                stored_path=str(Path(tmp_path) / "missing.png"),
                page_count=1,
            )
        )
        db.add(ReportExtractionJob(report_id=report.id, status="queued"))
        db.commit()
        report_id = report.id

    assert run_next_job(factory) is not None
    with factory() as db:
        assert db.query(ReportExtractionJob).one().status == "failed"
        report = db.get(MedicalReport, report_id)
        assert report.status == "failed"
        assert "原文读取失败" in report.parsed_content["error"]
        # 读不出来的那份材料**不可自动重试** —— 字节不在就是不在。但它是患者侧
        # 「你给的那份我们没读到」的成因，所以患者收到的会是「重新上传」。
        assert report.parsed_content["failure"] == "material_unreadable"
