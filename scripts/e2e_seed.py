"""Seed throwaway E2E databases with a login account and reports.

由前端 E2E 脚手架(``frontend/e2e``)调用:向 Playwright 运行所用的测试数据库
写入登录账户与指定状态的报告。种子数据全部通过应用自身的模型、口令散列与
证据响应契约生成,与真实运行时行为保持一致,不引入并行契约。

用法::

    uv run python scripts/e2e_seed.py \\
        --database sqlite:////tmp/healthflow-e2e/healthflow-e2e.db \\
        --tenant-id e2e-tenant --external-subject e2e-user \\
        [--display-name 昵称] [--report assessed] [--report pending_confirmation]

标准输出为 JSON(账户凭据 + 报告 ID/状态/访问令牌),供脚手架消费。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.models import Base, MedicalReport, MetricRecord, ReportFile, TicketSubject, UserSession
from app.schema.evidence import EvidenceMatchResponse
from app.service.origin_location import source_id_for
from app.service.report_ownership import subject_storage_id
from app.service.sessions import issue_session

# 支持的种子报告状态:已完成(assessed)与待确认(pending_confirmation)。
SEED_REPORT_STATUSES = ("assessed", "pending_confirmation")

E2E_DISCLAIMER = "本解读由 HealthFlow 生成,仅提供信息整理与健康辅助建议,不能替代医生诊断。"

# 已完成(assessed)报告的指标:确认流程已结束,携带用户核对后的值。
_ASSESSED_METRICS: tuple[dict[str, Any], ...] = (
    {
        "metric_name": "空腹血糖",
        "metric_value": "6.5",
        "unit": "mmol/L",
        "reference_range": "3.9-6.1",
        "abnormal_flag": "H",
        "evidence_text": "空腹血糖 6.5 mmol/L ↑",
        "bbox": [120.0, 340.0, 280.0, 360.0],
        "bbox_normalized": [60.0, 170.0, 140.0, 180.0],
        "page_number": 1,
    },
    {
        "metric_name": "糖化血红蛋白",
        "metric_value": "6.2",
        "unit": "%",
        "reference_range": "4.0-6.0",
        "abnormal_flag": "H",
        "evidence_text": "糖化血红蛋白 6.2 % ↑",
        "bbox": [120.0, 390.0, 300.0, 410.0],
        "bbox_normalized": [60.0, 195.0, 150.0, 205.0],
        "page_number": 1,
    },
    {
        "metric_name": "血红蛋白",
        "metric_value": "138",
        "unit": "g/L",
        "reference_range": "115-150",
        "abnormal_flag": "N",
        "evidence_text": "血红蛋白 138 g/L",
        "bbox": [120.0, 440.0, 260.0, 460.0],
        "bbox_normalized": [60.0, 220.0, 130.0, 230.0],
        "page_number": 1,
    },
    # 分歧样本一：抽取模型标了 H，但数值在参考范围内 —— 判定为 N，
    # 报告页显示正常、历史摘要不计数。
    {
        "metric_name": "误标的餐后血糖",
        "metric_value": "5.0",
        "unit": "mmol/L",
        "reference_range": "3.9-6.1",
        "abnormal_flag": "H",
        "evidence_text": "误标的餐后血糖 5.0 mmol/L 3.9-6.1",
        "bbox": [120.0, 490.0, 300.0, 510.0],
        "bbox_normalized": [60.0, 245.0, 150.0, 255.0],
        "page_number": 1,
    },
    # 分歧样本二：抽取模型没有标记，但数值超出参考范围 —— 判定为 H，
    # 报告页显示异常、历史摘要计数。
    {
        "metric_name": "漏标的总胆固醇",
        "metric_value": "6.9",
        "unit": "mmol/L",
        "reference_range": "0.0-5.2",
        "abnormal_flag": None,
        "evidence_text": "漏标的总胆固醇 6.9 mmol/L 0.0-5.2",
        "bbox": [120.0, 540.0, 320.0, 560.0],
        "bbox_normalized": [60.0, 270.0, 160.0, 280.0],
        "page_number": 1,
    },
)

# 待确认(pending_confirmation)报告的指标:解析完成、等待用户核对。
_PENDING_METRICS: tuple[dict[str, Any], ...] = (
    {
        "metric_name": "甘油三酯",
        "metric_value": "2.3",
        "unit": "mmol/L",
        "reference_range": "0.45-1.7",
        "abnormal_flag": "H",
        "evidence_text": "甘油三酯 2.3 mmol/L ↑",
        "bbox": [110.0, 320.0, 270.0, 340.0],
        "bbox_normalized": [55.0, 160.0, 135.0, 170.0],
        "page_number": 1,
    },
    {
        "metric_name": "低密度脂蛋白胆固醇",
        "metric_value": "3.6",
        "unit": "mmol/L",
        "reference_range": "2.1-3.1",
        "abnormal_flag": "H",
        "evidence_text": "低密度脂蛋白胆固醇 3.6 mmol/L ↑",
        "bbox": [110.0, 370.0, 320.0, 390.0],
        "bbox_normalized": [55.0, 185.0, 160.0, 195.0],
        "page_number": 1,
    },
)


def _engine_for(database_url: str) -> Engine:
    """Create a short-lived engine for the E2E database."""
    if database_url.startswith("sqlite"):
        # 与 E2E 服务器共用同一个文件时留足忙等待时间。
        return create_engine(database_url, connect_args={"timeout": 30})
    return create_engine(database_url)


def _ensure_sqlite_database_directory(database_url: str) -> None:
    """Create the parent directory of a file-backed SQLite database, if any."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return
    database = url.database
    if not database or database == ":memory:":
        return
    parent = Path(database).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def _write_seed_report_page(report_files_dir: str, report_id: int) -> tuple[str, str, str]:
    """Create a white seed page image so the source viewer has a real file to render."""
    from PIL import Image

    directory = Path(report_files_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"seed-report-{report_id}-page-1.png"
    Image.new("RGB", (900, 1200), "white").save(target, "PNG")
    return str(target), "image/png", "e2e-seed-report.png"


def _assessed_evidence_result() -> dict[str, Any]:
    """Build a contract-valid evidence result with no published-card matches."""
    summary = "E2E 种子报告:暂无匹配的已发布知识卡。"
    response = EvidenceMatchResponse.model_validate(
        {
            "schema_version": "2",
            "sorting_version": "published-card-reference-range-v1",
            "correlation_id": f"e2e-seed-{uuid.uuid4().hex}",
            "findings": [],
            "unmatched": [],
            "skipped": [],
            "message": summary,
            "patient_reply": {
                "title": "体检报告解读与健康风险提示",
                "summary": summary,
                "findings": [],
                "unmatched_count": 0,
                "disclaimer": E2E_DISCLAIMER,
            },
        }
    )
    return response.model_dump(mode="json")


def _seed_metric(
    report_id: int,
    spec: dict[str, Any],
    index: int,
    *,
    confirmed: bool,
) -> MetricRecord:
    """Create one metric row, either confirmed (assessed) or pending."""
    record = MetricRecord(
        report_id=report_id,
        source_file_index=1,
        metric_name=spec["metric_name"],
        metric_value=spec["metric_value"],
        unit=spec["unit"],
        reference_range=spec["reference_range"],
        abnormal_flag=spec["abnormal_flag"],
        bbox=spec["bbox"],
        bbox_normalized=spec["bbox_normalized"],
        page_number=spec["page_number"],
        evidence_text=spec["evidence_text"],
        # source_id 的格式只有一处定义（origin_location）——seed 不再手抄。
        source_id=source_id_for(1, spec["page_number"], index),
    )
    if not confirmed:
        return record
    record.confirmation_status = "confirmed"
    record.confirmed_value = spec["metric_value"]
    record.confirmed_unit = spec["unit"]
    record.confirmed_reference_range = spec["reference_range"]
    record.confirmed_evidence_text = spec["evidence_text"]
    return record


def _seed_report(
    session: Session,
    owner_id: str,
    status: str,
    *,
    report_files_dir: str | None = None,
) -> MedicalReport:
    """Create one owned report in ``status``。

    不再签发报告令牌（#172 已移除该机制），也不再回显任何令牌。
    """
    confirmed = status == "assessed"
    specs = _ASSESSED_METRICS if confirmed else _PENDING_METRICS
    report = MedicalReport(
        patient_id=owner_id,
        owner_id=owner_id,
        report_type="体检报告",
        department="健康管理中心",
        status=status,
        subject_consistency="same",
        parsed_content={
            "report_type": "体检报告",
            "raw_text": "\n".join(spec["evidence_text"] for spec in specs),
            "page_count": 1,
            "metric_count": len(specs),
        },
        evidence_result=_assessed_evidence_result() if confirmed else None,
        extraction_provider="e2e-seed",
        extraction_model="e2e-seed",
        extraction_run_id=f"e2e-seed-{uuid.uuid4().hex[:12]}",
    )
    session.add(report)
    session.flush()
    if report_files_dir:
        stored_path, media_type, original_filename = _write_seed_report_page(report_files_dir, report.id)
        session.add(
            ReportFile(
                report_id=report.id,
                file_index=1,
                original_filename=original_filename,
                media_type=media_type,
                stored_path=stored_path,
                page_count=1,
            )
        )
    for index, spec in enumerate(specs, start=1):
        session.add(_seed_metric(report.id, spec, index, confirmed=confirmed))
    return report


def seed_database(
    database_url: str,
    *,
    tenant_id: str = "e2e-tenant",
    external_subject: str = "e2e-user",
    display_name: str | None = None,
    reports: Sequence[str] = SEED_REPORT_STATUSES,
    report_files_dir: str | None = None,
) -> dict[str, Any]:
    """Seed one ticket subject, its session, and the requested reports.

    #172 之后不再有账号与密码：主体由「票据兑换」产生。种子脚本直接构造等价的
    主体标识与一条会话行，等价于「用户刚从商城跳进来兑换过一张票」的状态。
    """
    for status in reports:
        if status not in SEED_REPORT_STATUSES:
            raise ValueError(f"不支持的种子报告状态: {status}")
    _ensure_sqlite_database_directory(database_url)
    engine = _engine_for(database_url)
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            owner_id = subject_storage_id(tenant_id, external_subject)
            existing = session.scalar(select(UserSession).where(UserSession.account_id == owner_id))
            if existing is not None:
                # 同一主体重复播种是调用方的错误：本次会再建一套报告，与已有的一套
                # 混在一起，测试数据不再是确定的。显式拒绝，而不是静默产生重复。
                raise IntegrityError("subject already seeded", None, Exception(owner_id))
            # 主体记录：票据兑换会建它，种子这里建等价的一条，否则 /me 拿不到展示名。
            session.add(
                TicketSubject(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    external_subject=external_subject,
                    display_name=display_name or "商城用户",
                )
            )
            token, session_row = issue_session(owner_id)
            session.add(session_row)
            seeded = [
                _seed_report(session, owner_id, status, report_files_dir=report_files_dir)
                for status in reports
            ]
            session.commit()
            payload = {
                "subject": {
                    "owner_id": owner_id,
                    "tenant_id": tenant_id,
                    "external_subject": external_subject,
                    "display_name": display_name or "商城用户",
                    "session_token": token,
                },
                "reports": [
                    {
                        "id": report.id,
                        "status": report.status,
                        "report_type": report.report_type,
                    }
                    for report in seeded
                ],
            }
    finally:
        engine.dispose()
    return payload


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="向 E2E 测试数据库写入票据主体、会话与指定状态的报告种子数据。")
    parser.add_argument("--database", required=True, help="测试数据库 SQLAlchemy URL")
    parser.add_argument("--display-name", default=None, help="主体显示昵称")
    parser.add_argument("--tenant-id", default="e2e-tenant", help="主体所属租户标识")
    parser.add_argument("--external-subject", default="e2e-user", help="主体在商城侧的用户标识")
    parser.add_argument(
        "--report",
        action="append",
        choices=SEED_REPORT_STATUSES,
        dest="reports",
        help="要创建的报告状态,可重复;缺省同时创建已完成与待确认报告",
    )
    parser.add_argument(
        "--report-files",
        default=None,
        help="报告原文页落盘目录;提供时为每个报告生成一页白色样本图片",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; prints the seed payload as JSON on success."""
    args = _parse_args(argv)
    try:
        payload = seed_database(
            args.database,
            tenant_id=args.tenant_id,
            external_subject=args.external_subject,
            display_name=args.display_name,
            reports=args.reports or SEED_REPORT_STATUSES,
            report_files_dir=args.report_files,
        )
    except IntegrityError as exc:
        print(f"e2e_seed: 主体已存在({args.external_subject}): {exc.orig}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
