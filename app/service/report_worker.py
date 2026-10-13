"""Durable single-worker report extraction queue."""

from __future__ import annotations

import fcntl
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from app.api.report import ParseOutcome, _audit, _carried_forward, _parse_report
from app.config import get_settings
from app.data.models import (
    MedicalReport,
    ReportExtractionJob,
    ReportFile,
)
from app.data.mysql_client import get_mysql_client
from app.service.interpretation_failure import InterpretationFailure, classify, is_retryable
from app.service.report_status import transition


def recover_stale_jobs(factory) -> int:
    """回收卡在 running 的抽取任务。

    「卡住」有两种成因，处置完全不同：

    1. **报告还在 processing** —— worker 真的没跑完。把任务放回队列是对的，
       报告保持解析中等待下一轮。
    2. **报告已经离开 processing**（pending_confirmation / confirmed / assessed）
       —— 解析**成功提交了**，只是 worker 在 ack 任务之前重启。这种任务**不能**
       放回队列：``run_next_job`` 会再解析一遍，把患者核对过的指标行删掉重来；
       已确认/已评估的报告还会因为非法迁移变成 failed。

    以前这里是一次批量 ``update()``，既不看报告、也不写审计 —— 事后无法回答
    「这份报告为什么又被解析了一遍」。现在逐条判断并留痕。
    """
    cutoff = _now() - timedelta(seconds=get_settings().REPORT_JOB_STALE_SECONDS)
    with factory() as db:
        stale = (
            db.query(ReportExtractionJob)
            .filter(
                ReportExtractionJob.status == "running",
                ReportExtractionJob.updated_at < cutoff,
            )
            .all()
        )
        now = _now()
        recovered = 0
        for job in stale:
            report = db.get(MedicalReport, job.report_id)
            if report is not None and report.status != "processing":
                # 解析已提交，只是任务没 ack —— 标记为完成，不入队。
                job.status = "completed"
                job.error_class = None
                job.completed_at = now
                job.updated_at = now
                _audit(
                    db,
                    report,
                    "status_recovered",
                    {
                        "job_id": job.id,
                        "reason": "stale_running_job_after_success",
                        "report_status": report.status,
                        "job_outcome": "completed",
                    },
                    actor="system:report-worker",
                )
                continue
            job.status = "queued"
            job.started_at = None
            job.updated_at = now
            if report is not None:
                transition(
                    report,
                    "processing",
                    db=db,
                    action="status_recovered",
                    actor="system:report-worker",
                    detail={"job_id": job.id, "reason": "stale_running_job"},
                )
            recovered += 1
        db.commit()
        return recovered


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
    read_error: InterpretationFailure | None = None
    # 报告是否还在 `processing`。取不到报告时按「不在」处理 —— 一个指向不存在报告的
    # 任务没有可解析的材料。
    still_processing = False
    try:
        with factory() as db:
            job = db.get(ReportExtractionJob, job_id)
            report = db.get(MedicalReport, job.report_id) if job else None
            files = (
                db.query(ReportFile).filter(ReportFile.report_id == job.report_id).order_by(ReportFile.file_index).all()
                if job
                else []
            )
            report_id = int(job.report_id) if job else 0
            # **报告是否还在 processing，必须在解析**之前**看。** `_parse_report` 的
            # 第一件事就是把这份报告的全部指标行删掉重插为 `pending` —— 等它在后面
            # 的重试守卫里才被拦下，患者核对过的行已经和它的表态一起没了。一个排队中
            # 的任务指向一份已确认/评估的报告，是 `recover_stale_jobs` 之外的路径也能
            # 造出来的状态，所以这里挡而不是在重试分支里挡。
            still_processing = report is not None and report.status == "processing"
            accepted_files = (
                [
                    (
                        int(item.file_index),
                        str(item.original_filename),
                        str(item.media_type),
                        Path(item.stored_path).read_bytes(),
                    )
                    for item in files
                ]
                if still_processing
                else []
            )
    except OSError as exc:
        # 读盘失败是**材料**那一环坏了：（我们存的那份）字节读不出来。它不可自动重试 ——
        # 字节不在就是不在，再跑一遍还是不在 —— 但它对患者是「你给的那份我们没读到」，
        # 也就是唯一该请患者重传的那种成因。
        accepted_files = []
        read_error = classify("material", exc)
    if read_error is not None:
        outcome = ParseOutcome(succeeded=False, failure=read_error)
    elif not still_processing:
        # 报告已经离开 `processing`（患者已确认或评估过），或者这个任务指向一份不存在的
        # 报告。两种情形都**什么都没解析**，所以没有失败可报 —— 那两件事在下面各自处置。
        outcome = ParseOutcome(succeeded=False, failure=None)
    elif accepted_files:
        outcome = _parse_report(report_id, accepted_files, factory)
    else:
        # 报告还在 processing，文件却一条都取不到：没有任何材料可解析。同「读不出来」一类。
        read_error = "material_unreadable"
        outcome = ParseOutcome(succeeded=False, failure=read_error)
    with factory() as db:
        job = db.get(ReportExtractionJob, job_id)
        report = db.get(MedicalReport, job.report_id) if job else None
        if job is None:
            return job_id
        now = _now()
        if report is not None and not still_processing and not read_error:
            # 报告在我们开始解析之前就已经离开 `processing`（患者已确认或评估过）。
            # 这一轮**什么都没解析**（上面据此没读文件、没调 `_parse_report`），所以
            # 没有失败可报，更不该往患者已经看到的东西里写一个字。任务直接 ack 掉：
            # 同一形状在 `recover_stale_jobs` 里也是这么处置的。
            job.status = "completed"
            job.error_class = None
            job.completed_at = now
            job.updated_at = now
            _audit(
                db,
                report,
                "status_recovered",
                {
                    "job_id": job.id,
                    "reason": "skipped_job_for_a_report_that_left_processing",
                    "report_status": report.status,
                    "job_outcome": "completed",
                },
                actor="system:report-worker",
            )
            db.commit()
            return job_id
        if read_error is not None and report is not None:
            # 保留的只有抽取轨迹，逐轮结果丢掉 —— 见 `_carried_forward`：上一轮的
            # `file_results` 会在这一轮的失败块里被说成「这次失败的文件」。
            report.parsed_content = {
                **_carried_forward(report.parsed_content),
                "error": "报告原文读取失败，请重新上传或稍后重试。",
                "failure": read_error,
            }
        # 重试判定的**唯一输入**是这次解析交出来的成因。此前它读的是
        # `parsed_content["retryable"]` —— 一个由「文件名 + 错误文本」的子串嗅探得出的
        # 布尔，而文件名在被嗅探的文本里：一份「没抽出指标」的体检单改名成
        # `timeout.jpg`，重试决策就翻转；真正的额度耗尽、限流反而永不重试。
        failure = outcome.failure
        max_attempts = max(1, int(getattr(get_settings(), "REPORT_JOB_MAX_ATTEMPTS", 3)))
        # **重试只在确认之前有意义。** 一次重试会把已解析的指标行全部删掉重插为 `pending`
        # —— 患者核对过的行会连同他的表态一起消失。报告一旦离开 `processing`（患者已经
        # 确认或评估过），就再也不给重试：那一轮解析的失败只作为事实记下，报告留在患者的
        # 那个状态上。
        report_is_ours_to_judge = report is not None and report.status == "processing"
        retryable_now = (
            report_is_ours_to_judge and is_retryable(failure) and job.attempt_count < max_attempts
        )
        if not outcome.succeeded and retryable_now:
            job.status = "queued"
            job.error_class = "ReportExtractionRetryable"
            job.started_at = None
            job.completed_at = None
            job.updated_at = now
            if report is not None:
                transition(
                    report,
                    "processing",
                    db=db,
                    action="extraction_retry_queued",
                    actor="system:report-worker",
                    detail={
                        "attempt_count": job.attempt_count,
                        "max_attempts": max_attempts,
                        "failure": failure,
                    },
                )
        else:
            job.status = "completed" if outcome.succeeded else "failed"
            job.error_class = None if outcome.succeeded else "ReportExtractionFailed"
            job.completed_at = now
            job.updated_at = now
            if not outcome.succeeded and report is not None:
                # **同一份报告不给两次终态。** `pending_confirmation → failed` 在迁移表里
                # 是合法的：一次已经解析成功、已在等患者核对的报告，在后拿到一个失败结论
                # 时确实「解读失败」了。但患者已经**确认或评估过**的报告不是 —— 那说明
                # 患者在解析期间完成了核对（报告开始时还在 `processing`，所以早返回那道
                # 守卫没拦到）。状态已经由患者推动了，那就只记事实，不再改第二次。
                already_settled = report.status in {"confirmed", "assessed", "failed"}
                if not already_settled:
                    transition(
                        report,
                        "failed",
                        db=db,
                        action="extraction_failed",
                        actor="system:report-worker",
                        detail={"attempt_count": job.attempt_count, "failure": failure},
                    )
                carried = _carried_forward(report.parsed_content)
                report.parsed_content = {
                    **carried,
                    # 本轮早些时候（读盘失败）写下的那句话优先 —— 它比通用话术说得准。
                    "error": carried.get("error") or "报告智能解读失败，请重新上传或稍后重试。",
                    "failure": failure,
                }
        db.commit()
    return job_id


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def main() -> None:
    client = get_mysql_client()
    client.create_tables()
    factory = sessionmaker(bind=client.engine)
    lock_path = Path(get_settings().REPORT_FILES_DIR).expanduser().resolve().parent / "healthflow-report-worker.lock"
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
