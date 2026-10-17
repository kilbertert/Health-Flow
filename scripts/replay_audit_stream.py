"""审计流回放（#247 取证）：一次真实的上传 → 解析 → 确认 → 评估，打印落下的流。

用法：uv run --extra dev python scripts/replay_audit_stream.py
输出每个动作的 (action, actor, detail) 与「每个 action 恰好一条」的判定。
不改任何生产数据：一次性 SQLite + 打桩的抽取器。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.data.models import Base, MedicalReport, ReportAuditEvent, ReportFile  # noqa: E402
from app.schema.report import MetricRecord  # noqa: E402
from app.service.report_status import record_event, transition  # noqa: E402
from app.service.report_worker import run_next_job  # noqa: E402


class _Vision:
    """一次成功的解析：一行血糖。"""

    def parse(self, content, filename):
        from app.service.vision_encoder import ParsedReport

        return ParsedReport(
            report_type="image",
            raw_text="空腹血糖 6.5",
            page_count=1,
            success=True,
            metrics=[
                MetricRecord(
                    metric_name="空腹血糖",
                    metric_value="6.5",
                    unit="mmol/L",
                    reference_range="3.9-6.1",
                    page_number=1,
                    evidence_text="空腹血糖 6.5 mmol/L",
                )
            ],
        )


def main() -> int:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    page = Path("/tmp/replay-report-page.png")
    page.write_bytes(b"fake-png")

    # ── 上传：一次不带 db 的 transition（状态显式设置）+ 一笔审计专用事件 ──
    with factory() as db:
        report = MedicalReport(patient_id="P", owner_id="account:t:u")
        transition(report, "processing")  # 落库前，还没有 id
        db.add(report)
        db.flush()
        record_event(db, report, "uploaded", actor="account:t:u", detail={"file_count": 1})
        from app.data.models import ReportExtractionJob

        db.add(
            ReportFile(
                report_id=report.id,
                file_index=1,
                original_filename="a.png",
                media_type="image/png",
                stored_path=str(page),
                page_count=1,
            )
        )
        db.add(ReportExtractionJob(report_id=report.id, status="queued"))
        db.commit()
        report_id = report.id

    # ── 解析 ──
    with patch("app.api.report.get_vision_encoder_service", return_value=_Vision()), patch(
        "app.service.report_worker.get_settings"
    ) as settings:
        settings.return_value.REPORT_JOB_MAX_ATTEMPTS = 3
        run_next_job(factory)

    # ── 确认 → 评估：两次 transition，各自 detail 一次给全 ──
    with factory() as db:
        report = db.get(MedicalReport, report_id)
        transition(
            report,
            "confirmed",
            db=db,
            action="confirmed",
            actor="account:t:u",
            detail={"confirmed_metric_ids": [1], "corrected_metric_ids": [], "excluded_metric_ids": []},
        )
        transition(
            report,
            "assessed",
            db=db,
            action="assessed",
            actor="system:report-assessor",
            detail={"finding_count": 1, "unmatched_count": 0, "skipped_count": 0},
        )
        db.commit()

    with factory() as db:
        events = (
            db.query(ReportAuditEvent)
            .filter(ReportAuditEvent.report_id == report_id)
            .order_by(ReportAuditEvent.id)
            .all()
        )

    print(f"报告 #{report_id} 的审计流（{len(events)} 条）：")
    for event in events:
        print(f"  {event.action:34} actor={event.actor:26} detail={event.detail}")

    counts: dict[str, int] = {}
    for event in events:
        counts[event.action] = counts.get(event.action, 0) + 1
    duplicated = {action: n for action, n in counts.items() if n > 1}
    print()
    print("每个 action 的出现次数：", counts)
    if duplicated:
        print(f"FAIL：这些 action 落了不止一条 —— {duplicated}")
        return 1
    print("OK：每个 action 恰好一条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
