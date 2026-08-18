"""Report upload, parsing and metric endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import db_dependency
from app.config import get_settings
from app.data.milvus_client import get_milvus_client
from app.data.models import MedicalReport as ReportModel
from app.data.models import MetricRecord as MetricModel
from app.data.models import ReportFile as ReportFileModel
from app.schema.report import (
    MedicalReportResponse,
    MetricRecord,
    ReportConfirmationRequest,
)
from app.service.evidence_bridge import (
    EvidenceBridgeError,
    build_observations,
    fetch_metric_catalog,
    match_published_evidence,
    metric_code_for_name,
)
from app.service.vision_encoder import get_vision_encoder_service

router = APIRouter()
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}
logger = logging.getLogger(__name__)


def _media_type(filename: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(Path(filename).suffix.casefold(), "application/octet-stream")


def _page_count(filename: str, content: bytes) -> int:
    if Path(filename).suffix.casefold() != ".pdf":
        return 1
    try:
        import fitz

        with fitz.open(stream=content, filetype="pdf") as document:
            return max(1, document.page_count)
    except Exception:
        return 1


def _persist_report_files(
    report_id: int,
    files: list[tuple[int, str, str, bytes]],
    db: Session,
) -> None:
    report_dir = Path(get_settings().REPORT_FILES_DIR).expanduser().resolve() / str(report_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    for file_index, filename, media_type, content in files:
        suffix = Path(filename).suffix.casefold()
        target = report_dir / f"{file_index}{suffix}"
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        db.add(
            ReportFileModel(
                report_id=report_id,
                file_index=file_index,
                original_filename=filename,
                media_type=media_type,
                stored_path=str(target),
                page_count=_page_count(filename, content),
            )
        )


def _metric_response(metric: MetricModel) -> MetricRecord:
    def load_json(value):
        if value is None or isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None

    return MetricRecord(
        id=metric.id,
        report_id=metric.report_id,
        metric_name=metric.metric_name or "未命名指标",
        metric_value=metric.metric_value or "",
        unit=metric.unit,
        reference_range=metric.reference_range,
        trend=metric.trend,
        abnormal_flag=metric.abnormal_flag,
        bbox=load_json(metric.bbox),
        bbox_normalized=load_json(metric.bbox_normalized),
        source_file_index=metric.source_file_index or 1,
        page_number=metric.page_number,
        evidence_text=metric.evidence_text,
        source_id=metric.source_id,
        metric_code=metric.metric_code,
        confirmation_status=metric.confirmation_status or "pending",
        confirmed_value=metric.confirmed_value,
        confirmed_unit=metric.confirmed_unit,
        confirmed_reference_range=metric.confirmed_reference_range,
    )


@router.post("/report/upload", response_model=MedicalReportResponse, status_code=202)
async def upload_report(
    background_tasks: BackgroundTasks,
    patient_id: str = Form(..., min_length=1, description="患者 ID（必填，非空）"),
    report_type: Optional[str] = Form(None),
    department: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    files: Optional[list[UploadFile]] = File(None),
    db: Session = Depends(db_dependency),
):
    patient_id = patient_id.strip()
    if not patient_id:
        raise HTTPException(status_code=422, detail="patient_id 不能为空")
    upload_files = list(files or [])
    if file is not None:
        upload_files.append(file)
    if not upload_files:
        raise HTTPException(status_code=400, detail="请至少上传一个报告文件")
    if len(upload_files) > get_settings().MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail="报告文件数量超过限制")

    max_bytes = get_settings().MAX_UPLOAD_BYTES
    max_total_bytes = get_settings().MAX_UPLOAD_TOTAL_BYTES
    total_bytes = 0
    accepted_files = []
    for file_index, upload_file in enumerate(upload_files, start=1):
        filename = upload_file.filename or f"report-{file_index}"
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=415, detail="仅支持 PDF 或常见图片格式")
        chunks: list[bytes] = []
        file_total = 0
        while True:
            chunk = await upload_file.read(1024 * 1024)
            if not chunk:
                break
            file_total += len(chunk)
            total_bytes += len(chunk)
            if file_total > max_bytes:
                raise HTTPException(status_code=413, detail="单个报告文件超过大小限制")
            if total_bytes > max_total_bytes:
                raise HTTPException(status_code=413, detail="报告文件总大小超过限制")
            chunks.append(chunk)
        content = b"".join(chunks)
        if not content:
            raise HTTPException(status_code=400, detail=f"第 {file_index} 个报告文件内容为空")
        accepted_files.append((file_index, filename, _media_type(filename), content))

    report = ReportModel(
        patient_id=patient_id,
        report_type=report_type or "体检",
        department=department,
        parsed_content={"file_count": len(accepted_files)},
        status="processing",
        subject_consistency="same" if len(accepted_files) == 1 else "uncertain",
        exam_date=datetime.now(),
    )
    db.add(report)
    db.flush()
    if report.id is None:
        raise HTTPException(status_code=500, detail="报告写入数据库失败")
    _persist_report_files(report.id, accepted_files, db)

    db.commit()
    db.refresh(report)
    factory = sessionmaker(bind=db.get_bind())
    # ponytail: in-process jobs fit this single-instance demo; use a durable
    # queue before horizontal scaling or restart-safe processing is required.
    background_tasks.add_task(_parse_report, report.id, accepted_files, factory)
    return _report_response(report, [])


def _parse_report(
    report_id: int,
    accepted_files: list[tuple[int, str, str, bytes]],
    factory,
) -> None:
    try:
        vision = get_vision_encoder_service()

        def parse_file(source):
            file_index, filename, _, content = source
            parsed = vision.parse(content, filename)
            if not parsed.success:
                raise ValueError(parsed.error or "未提取到可确认的指标")
            return file_index, parsed

        workers = max(1, min(get_settings().REPORT_PARSE_WORKERS, len(accepted_files)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            parsed_reports = list(executor.map(parse_file, accepted_files))
        parsed_metrics = [
            item.model_copy(
                update={
                    "source_file_index": file_index,
                    "metric_code": metric_code_for_name(item.metric_name),
                }
            )
            for file_index, parsed in parsed_reports
            for item in parsed.metrics
        ]
        if not parsed_metrics:
            raise ValueError("未提取到可确认的指标")

        with factory() as db:
            report = db.get(ReportModel, report_id)
            if report is None:
                return
            report.parsed_content = {
                "report_type": parsed_reports[0][1].report_type,
                "raw_text": "\n".join(
                    parsed.raw_text for _, parsed in parsed_reports if parsed.raw_text
                ),
                "page_count": sum(parsed.page_count for _, parsed in parsed_reports),
                "metric_count": len(parsed_metrics),
            }
            report.status = "pending_confirmation"
            for item in parsed_metrics:
                db.add(
                    MetricModel(
                        report_id=report.id,
                        source_file_index=item.source_file_index,
                        metric_name=item.metric_name,
                        metric_value=item.metric_value,
                        unit=item.unit,
                        reference_range=item.reference_range,
                        trend=item.trend,
                        abnormal_flag=item.abnormal_flag,
                        bbox=item.bbox,
                        bbox_normalized=item.bbox_normalized,
                        page_number=item.page_number,
                        evidence_text=item.evidence_text,
                        source_id=item.source_id,
                        metric_code=item.metric_code,
                        confirmation_status="pending",
                    )
                )
            db.commit()
    except Exception:
        logger.exception("report parsing failed", extra={"report_id": report_id})
        with factory() as db:
            report = db.get(ReportModel, report_id)
            if report is None:
                return
            report.status = "failed"
            report.parsed_content = {
                **(report.parsed_content or {}),
                "error": "报告智能解读失败，请重新上传或稍后重试。",
            }
            db.commit()


def _report_response(report: ReportModel, metrics: list[MetricModel]) -> MedicalReportResponse:
    return MedicalReportResponse(
        id=report.id,
        patient_id=report.patient_id,
        report_type=report.report_type,
        exam_date=report.exam_date,
        department=report.department,
        metrics=[_metric_response(metric) for metric in metrics],
        files=[
            {
                "file_index": item.file_index,
                "original_filename": item.original_filename,
                "media_type": item.media_type,
                "page_count": item.page_count,
                "source_url": (
                    f"/api/health/report/{report.id}/files/{item.file_index}/pages/1"
                ),
            }
            for item in sorted(report.files, key=lambda value: value.file_index)
        ],
        created_at=report.created_at,
        status=report.status or "pending_confirmation",
        subject_consistency=report.subject_consistency,
        evidence_result=report.evidence_result,
        processing_error=(report.parsed_content or {}).get("error"),
    )


@router.get("/report/{report_id}", response_model=MedicalReportResponse)
async def get_report(report_id: int, db: Session = Depends(db_dependency)):
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    metrics = db.query(MetricModel).filter(MetricModel.report_id == report_id).all()
    return _report_response(report, metrics)


@router.post("/report/{report_id}/confirm", response_model=MedicalReportResponse)
async def confirm_report(
    report_id: int,
    request: ReportConfirmationRequest,
    db: Session = Depends(db_dependency),
):
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.status not in {"pending_confirmation", "confirmed"}:
        raise HTTPException(status_code=409, detail="报告当前状态不允许确认")
    if report.subject_consistency != "same" and request.subject_consistency != "same":
        raise HTTPException(status_code=422, detail="请先确认所有文件属于同一主体")
    metrics = db.query(MetricModel).filter(MetricModel.report_id == report_id).all()
    by_id = {metric.id: metric for metric in metrics}
    supplied = {item.metric_id: item for item in request.observations}
    if len(supplied) != len(request.observations) or not set(supplied) <= set(by_id):
        raise HTTPException(status_code=422, detail="确认列表包含重复或未知指标")
    try:
        metric_catalog = await fetch_metric_catalog()
    except EvidenceBridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    canonical_codes = {item["code"] for item in metric_catalog}
    now = datetime.now()
    for metric in metrics:
        item = supplied.get(metric.id)
        if item is None or item.decision == "excluded":
            metric.confirmation_status = "excluded"
            metric.confirmed_value = None
            metric.confirmed_unit = None
            metric.confirmed_reference_range = None
            metric.confirmed_at = now
            continue
        requested_code = (item.metric_code or metric.metric_code or "").strip()
        code = (
            requested_code
            if requested_code in canonical_codes
            else metric_code_for_name(metric.metric_name or "")
        )
        if code not in canonical_codes:
            # Unknown rows stay in the report for transparency but never cross
            # the evidence boundary.  The UI can still show them as excluded.
            metric.confirmation_status = "excluded"
            metric.confirmed_value = None
            metric.confirmed_unit = None
            metric.confirmed_reference_range = None
            metric.confirmed_at = now
            continue
        if item.decision == "corrected":
            if not item.value or not item.unit:
                raise HTTPException(status_code=422, detail=f"指标 {metric.id} 的修正值不完整")
            try:
                corrected_value = float(item.value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"指标 {metric.id} 的修正值必须是单个数字") from exc
            if not math.isfinite(corrected_value):
                raise HTTPException(status_code=422, detail=f"指标 {metric.id} 的修正值无效")
        metric.metric_code = code
        metric.confirmation_status = item.decision
        metric.confirmed_value = item.value if item.decision == "corrected" else metric.metric_value
        metric.confirmed_unit = item.unit if item.decision == "corrected" else metric.unit
        metric.confirmed_reference_range = (
            item.reference_range
            if item.decision == "corrected" and item.reference_range is not None
            else metric.reference_range
        )
        metric.confirmed_at = now
    report.status = "confirmed"
    report.subject_consistency = request.subject_consistency or report.subject_consistency or "same"
    report.evidence_result = None
    db.commit()
    try:
        return await _assess_report(report, db)
    except EvidenceBridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/report/{report_id}/assess", response_model=MedicalReportResponse)
async def assess_report(report_id: int, db: Session = Depends(db_dependency)):
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.status not in {"confirmed", "assessed"}:
        raise HTTPException(status_code=409, detail="请先确认报告指标")
    try:
        return await _assess_report(report, db)
    except EvidenceBridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _assess_report(report: ReportModel, db: Session) -> MedicalReportResponse:
    metrics = db.query(MetricModel).filter(MetricModel.report_id == report.id).all()
    result = await match_published_evidence(build_observations(metrics))
    report.evidence_result = result
    report.status = "assessed"
    db.commit()
    db.refresh(report)
    metrics = db.query(MetricModel).filter(MetricModel.report_id == report.id).all()
    return _report_response(report, metrics)


@router.get("/report/{report_id}/metrics", response_model=list[MetricRecord])
async def get_report_metrics(report_id: int, db: Session = Depends(db_dependency)):
    if not db.query(ReportModel).filter(ReportModel.id == report_id).first():
        raise HTTPException(status_code=404, detail="报告不存在")
    return [
        _metric_response(metric)
        for metric in db.query(MetricModel).filter(MetricModel.report_id == report_id).all()
    ]


@router.get("/metric-catalog", response_model=list[dict[str, str]])
async def report_metric_catalog():
    try:
        return await fetch_metric_catalog()
    except EvidenceBridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/report/{report_id}/files/{file_index}/pages/{page_number}")
async def get_report_page(
    report_id: int,
    file_index: int,
    page_number: int,
    db: Session = Depends(db_dependency),
):
    source = (
        db.query(ReportFileModel)
        .filter(
            ReportFileModel.report_id == report_id,
            ReportFileModel.file_index == file_index,
        )
        .first()
    )
    if source is None or page_number < 1 or page_number > source.page_count:
        raise HTTPException(status_code=404, detail="报告原文页不存在")
    path = Path(source.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="报告原文文件不存在")
    if source.media_type != "application/pdf":
        if page_number != 1:
            raise HTTPException(status_code=404, detail="报告原文页不存在")
        return FileResponse(path, media_type=source.media_type)
    try:
        import fitz

        with fitz.open(path) as document:
            image = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(2, 2))
            return Response(content=image.tobytes("png"), media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=422, detail="报告原文页无法渲染") from exc


@router.get("/reports", response_model=list[MedicalReportResponse])
async def list_reports(
    patient_id: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(db_dependency),
):
    limit = max(1, min(limit, 100))
    query = db.query(ReportModel)
    if patient_id:
        query = query.filter(ReportModel.patient_id == patient_id)
    if department:
        query = query.filter(ReportModel.department == department)
    reports = query.order_by(ReportModel.created_at.desc()).offset(max(0, offset)).limit(limit).all()
    return [
        _report_response(
            report,
            db.query(MetricModel).filter(MetricModel.report_id == report.id).all(),
        )
        for report in reports
    ]


@router.delete("/report/{report_id}")
async def delete_report(report_id: int, db: Session = Depends(db_dependency)):
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    # 先删数据库主记录；向量索引是尽力而为，放在 DB 成功之后，
    # 避免 DB 删除失败时向量索引已被清掉造成状态不一致。
    stored_paths = [Path(item.stored_path) for item in report.files]
    db.delete(report)
    db.commit()
    for path in stored_paths:
        path.unlink(missing_ok=True)
    if stored_paths:
        try:
            stored_paths[0].parent.rmdir()
        except OSError:
            pass
    try:
        await asyncio.to_thread(get_milvus_client().delete_by_report_id, report_id)
    except Exception:
        pass
    return {"message": "报告已删除", "report_id": report_id}
