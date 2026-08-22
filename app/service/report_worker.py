"""Durable single-worker report extraction queue."""

from __future__ import annotations

import fcntl
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app.api.report import _parse_report
from app.config import get_settings
from app.data.models import (
    MedicalReport,
    ReportAuditEvent,
    ReportExtractionJob,
    ReportFile,
)
from app.data.mysql_client import get_mysql_client


def recover_stale_jobs(factory) -> int:
    cutoff = _now() - timedelta(seconds=get_settings().REPORT_JOB_STALE_SECONDS)
    with factory() as db:
        result = db.execute(
            update(ReportExtractionJob)
            .where(
                ReportExtractionJob.status == "running",
                ReportExtractionJob.updated_at < cutoff,
            )
            .values(status="queued", started_at=None, updated_at=_now())
        )
        db.commit()
        return int(result.rowcount or 0)


def claim_next_job(factory) -> int | None:
    with factory() as db:
        candidate = (
            db.query(ReportExtractionJob.id)
            .filter(ReportExtractionJob.status == "queued")
            .order_by(ReportExtractionJob.created_at, ReportExtractionJob.id)
            .first()
        )
        if candidate is None:
            return None
        now = _now()
        result = db.execute(
            update(ReportExtractionJob)
            .where(
                ReportExtractionJob.id == candidate.id,
                ReportExtractionJob.status == "queued",
            )
            .values(
                status="running",
                attempt_count=ReportExtractionJob.attempt_count + 1,
                started_at=now,
                updated_at=now,
                error_class=None,
            )
        )
        db.commit()
        return int(candidate.id) if result.rowcount == 1 else None


def run_next_job(factory) -> int | None:
    job_id = claim_next_job(factory)
    if job_id is None:
        return None
    report_id = 0
    try:
        with factory() as db:
            job = db.get(ReportExtractionJob, job_id)
            report = db.get(MedicalReport, job.report_id) if job else None
            files = (
                db.query(ReportFile)
                .filter(ReportFile.report_id == job.report_id)
                .order_by(ReportFile.file_index)
                .all()
                if job
                else []
            )
            report_id = int(job.report_id) if job else 0
            accepted_files = [
                (
                    int(item.file_index),
                    str(item.original_filename),
                    str(item.media_type),
                    Path(item.stored_path).read_bytes(),
                )
                for item in files
            ]
        succeeded = bool(
            report
            and accepted_files
            and _parse_report(report_id, accepted_files, factory)
        )
    except OSError:
        succeeded = False
        with factory() as db:
            job = db.get(ReportExtractionJob, job_id)
            report = db.get(MedicalReport, job.report_id) if job else None
            if report is not None:
                report.status = "failed"
                report.parsed_content = {
                    **(report.parsed_content or {}),
                    "error": "报告原文读取失败，请重新上传或稍后重试。",
                }
                db.add(
                    ReportAuditEvent(
                        report_id=report.id,
                        action="extraction_failed",
                        actor="system:report-worker",
                        detail={"error_type": "ReportFileReadError"},
                    )
                )
            db.commit()
    with factory() as db:
        job = db.get(ReportExtractionJob, job_id)
        report = db.get(MedicalReport, job.report_id) if job else None
        if job is None:
            return job_id
        retryable = (
            bool((report.parsed_content or {}).get("retryable")) if report else False
        )
        max_attempts = max(
            1, int(getattr(get_settings(), "REPORT_JOB_MAX_ATTEMPTS", 3))
        )
        now = _now()
        if not succeeded and retryable and job.attempt_count < max_attempts:
            job.status = "queued"
            job.error_class = "ReportExtractionRetryable"
            job.started_at = None
            job.completed_at = None
            job.updated_at = now
            if report is not None:
                report.status = "processing"
                db.add(
                    ReportAuditEvent(
                        report_id=report.id,
                        action="extraction_retry_queued",
                        actor="system:report-worker",
                        detail={
                            "attempt_count": job.attempt_count,
                            "max_attempts": max_attempts,
                        },
                    )
                )
        else:
            job.status = "completed" if succeeded else "failed"
            job.error_class = None if succeeded else "ReportExtractionFailed"
            job.completed_at = now
            job.updated_at = now
            if not succeeded and report is not None:
                report.status = "failed"
                error = (report.parsed_content or {}).get("error")
                report.parsed_content = {
                    **(report.parsed_content or {}),
                    "error": error or "报告智能解读失败，请重新上传或稍后重试。",
                }
        db.commit()
    return job_id


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def main() -> None:
    client = get_mysql_client()
    client.create_tables()
    factory = sessionmaker(bind=client.engine)
    lock_path = (
        Path(get_settings().REPORT_FILES_DIR).expanduser().resolve().parent
        / "healthflow-report-worker.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        poll_seconds = max(0.5, float(os.getenv("REPORT_WORKER_POLL_SECONDS", "2")))
        while True:
            recover_stale_jobs(factory)
            if run_next_job(factory) is None:
                time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
