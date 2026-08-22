from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data.models import Base, MedicalReport, ReportExtractionJob, ReportFile
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

    with patch("app.service.report_worker._parse_report", return_value=True) as parse:
        assert run_next_job(factory) is not None
    parse.assert_called_once()
    with factory() as db:
        assert db.query(ReportExtractionJob).one().status == "completed"


def test_retryable_report_job_is_requeued_then_completed(tmp_path):
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
            report.parsed_content = {"retryable": attempts == 1}
            report.status = "processing" if attempts == 1 else "pending_confirmation"
            db.commit()
        return attempts > 1

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
