"""Report upload, parsing and metric endpoints."""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, session_owner_dependency
from app.config import get_settings
from app.data.models import MedicalReport as ReportModel
from app.data.models import MetricRecord as MetricModel
from app.data.models import ReportAuditEvent, ReportExtractionJob
from app.data.models import ReportFile as ReportFileModel
from app.schema.evidence import (
    EvidenceMatchResponse,
    Skipped,
    SourceObservation,
    Unmatched,
)
from app.schema.report import (
    CartLinkResponse,
    MedicalReportResponse,
    MetricRecord,
    RecommendationResponse,
    ReportConfirmationRequest,
)
from app.service.deep_link import DeepLinkError, build_deep_link
from app.service.evidence_bridge import (
    EvidenceBridgeError,
    build_observations_with_unmatched,
    fetch_metric_catalog,
    infer_abnormal_flag_for_metric,
    match_published_evidence,
    resolve_metric_code,
)
from app.service.mall_goods import fetch_goods, label_pairs_for, serialized
from app.service.metric_effective_value import effective_value
from app.service.report_ownership import UNOWNED_SENTINEL, resolve_owner
from app.service.report_status import transition
from app.service.vision_encoder import ParsedReport, get_vision_encoder_service

#: 只有这些状态的报告才谈得上加购：未完成确认/评估的报告没有可据以取货的风险。
COMPLETED_FOR_LINK = frozenset({"confirmed", "assessed"})

router = APIRouter()
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp"}
logger = logging.getLogger(__name__)
_TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "temporarily",
    "unavailable",
    "connection",
    "暂时",
    "不可用",
    "连接",
    "429",
    "502",
    "503",
    "504",
    "json",
)


def _authorized_report(db: Session, report_id: int, *, owner_id: str) -> ReportModel:
    """只按**主体的存储标识**判定归属。报告令牌旁路已随 #172 一并移除。

    注意 `report.access_token_hash` 不再参与判定，但**不要求它为空**：历史行里有它，
    而「有令牌」不构成访问依据——本票之后没有任何凭据能替代主体。
    """
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None or not report.owner_id:
        raise HTTPException(status_code=404, detail="报告不存在")
    # **无主历史行对任何人封闭。** 移除令牌旁路之后有一个更弱的路径会冒出来：
    # `resolve_owner` 在无会话时返回哨兵值，若它与旧行的 `owner_id` 相等，
    # 一个未认证请求就能读到无主报告——那比被移除的令牌旁路还宽。所以哨兵值
    # 显式拒绝，双方都不算匹配。
    if report.owner_id == UNOWNED_SENTINEL:
        raise HTTPException(status_code=404, detail="报告不存在")
    if not hmac.compare_digest(report.owner_id or "", owner_id):
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


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

    effective = effective_value(metric)
    return MetricRecord(
        id=metric.id,
        report_id=metric.report_id,
        metric_name=metric.metric_name or "未命名指标",
        metric_value=metric.metric_value or "",
        unit=metric.unit,
        reference_range=metric.reference_range,
        trend=metric.trend,
        abnormal_flag=metric.abnormal_flag,
        inferred_abnormal_flag=infer_abnormal_flag_for_metric(metric),
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
        confirmed_evidence_text=metric.confirmed_evidence_text,
        effective_value=effective.value,
        effective_unit=effective.unit,
        effective_reference_range=effective.reference_range,
        effective_evidence_text=effective.evidence_text,
    )


def _audit(
    db: Session,
    report: ReportModel,
    action: str,
    detail: dict[str, object] | None = None,
    *,
    actor: str = "system",
) -> None:
    db.add(
        ReportAuditEvent(
            report_id=report.id,
            action=action,
            actor=actor,
            correlation_id=report.evidence_correlation_id,
            detail=detail or {},
        )
    )


def _ordered_metrics(db: Session, report_id: int):
    return (
        db.query(MetricModel)
        .filter(MetricModel.report_id == report_id)
        .order_by(
            MetricModel.source_file_index,
            MetricModel.page_number,
            MetricModel.id,
        )
    )


def _processing_warnings(report: ReportModel) -> list[str]:
    """这次解析有哪些文件没能解析完（患者可见的解析细节）。

    **它不是闸门的来源**：确认/评估能不能做由状态决定 —— 仍有文件未解析完的
    报告，其状态是 ``processing`` 而不是 ``pending_confirmation``（见
    ``app/service/report_status.py`` 的迁移表）。这个函数只用来把原因写进
    给患者的提示里。
    """
    return list((report.parsed_content or {}).get("warnings") or [])


def _retryable_error(text: str | None) -> bool:
    value = (text or "").casefold()
    return any(marker.casefold() in value for marker in _TRANSIENT_ERROR_MARKERS)


@router.post("/report/upload", response_model=MedicalReportResponse, status_code=202)
async def upload_report(
    request: Request,
    patient_id: str | None = Form(None, description="兼容旧客户端的患者 ID"),
    report_type: str | None = Form(None),
    department: str | None = Form(None),
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    patient_id = (patient_id or "").strip()
    if not patient_id and owner_id:
        patient_id = owner_id
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
        subject_consistency="same" if len(accepted_files) == 1 else "uncertain",
        owner_id=resolve_owner(request).storage_id,
        exam_date=datetime.now(),
    )
    # 状态由唯一迁移入口设置。此刻对象还没 flush，列默认值尚未落到属性上，
    # 所以这是「从无到有」的第一次迁移；审计事件要等 id 存在后再写。
    transition(report, "processing")
    db.add(report)
    db.flush()
    if report.id is None:
        raise HTTPException(status_code=500, detail="报告写入数据库失败")
    _persist_report_files(report.id, accepted_files, db)
    _audit(
        db,
        report,
        "uploaded",
        {"file_count": len(accepted_files)},
        actor=resolve_owner(request).subject,
    )
    db.add(ReportExtractionJob(report_id=report.id, status="queued"))

    db.commit()
    db.refresh(report)
    return _report_response(
        report,
        [],
        owned_by_account=True,
    )


def _parse_report(
    report_id: int,
    accepted_files: list[tuple[int, str, str, bytes]],
    factory,
) -> bool:
    try:
        vision = get_vision_encoder_service()

        def parse_file(source):
            file_index, filename, _, content = source
            try:
                parsed = vision.parse(content, filename)
            except Exception as exc:
                parsed = ParsedReport(
                    report_type="unknown",
                    raw_text="",
                    metrics=[],
                    page_count=0,
                    success=False,
                    error=str(exc),
                )
            return (
                file_index,
                filename,
                parsed,
                parsed.error or (None if parsed.success else "未提取到可确认的指标"),
            )

        workers = max(1, min(get_settings().REPORT_PARSE_WORKERS, len(accepted_files)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            parsed_reports = list(executor.map(parse_file, accepted_files))
        successful_reports = [item for item in parsed_reports if item[2].success]
        parsed_metrics = [
            item.model_copy(
                update={
                    "source_file_index": file_index,
                    # 解析时写入：目录此刻还不可用（尚未到确认），只做名称归一化并标记待裁决。
                    "metric_code": resolve_metric_code(item.metric_name, None),
                    "source_id": (
                        f"file-{file_index}/"
                        f"{item.source_id or ('p' + str(item.page_number or 1) + '-m' + str(metric_index))}"
                    ),
                }
            )
            for file_index, _, parsed, error in successful_reports
            for metric_index, item in enumerate(parsed.metrics, start=1)
        ]
        unique_metrics: dict[tuple[object, ...], MetricRecord] = {}
        for item in parsed_metrics:
            identity = " ".join((item.evidence_text or item.metric_name).split()).casefold()
            key = (
                item.source_file_index,
                item.page_number,
                identity,
                item.metric_value,
                item.unit,
                item.reference_range,
            )
            unique_metrics.setdefault(key, item)
        parsed_metrics = sorted(
            unique_metrics.values(),
            key=lambda item: (
                item.source_file_index,
                item.page_number or 0,
                item.bbox_normalized[1] if item.bbox_normalized else 1001,
                item.bbox_normalized[0] if item.bbox_normalized else 1001,
                item.metric_name.casefold(),
                item.metric_value,
            ),
        )
        if not parsed_metrics:
            errors = "; ".join(f"{filename}: {error}" for _, filename, _, error in parsed_reports if error)
            raise ValueError(errors or "未提取到可确认的指标")

        with factory() as db:
            report = db.get(ReportModel, report_id)
            if report is None:
                return False
            db.query(MetricModel).filter(MetricModel.report_id == report_id).delete(synchronize_session=False)
            report.parsed_content = {
                "report_type": successful_reports[0][2].report_type,
                "raw_text": "\n".join(parsed.raw_text for _, _, parsed, _ in parsed_reports if parsed.raw_text),
                "page_count": sum(parsed.page_count for _, _, parsed, _ in parsed_reports),
                "metric_count": len(parsed_metrics),
                "file_results": [
                    {
                        "file_index": file_index,
                        "filename": filename,
                        "status": "parsed" if error is None else "failed",
                        **({"error": error} if error else {}),
                    }
                    for file_index, filename, _, error in parsed_reports
                ],
            }
            warnings = [f"{filename}: {error}" for _, filename, _, error in parsed_reports if error]
            if warnings:
                report.parsed_content["warnings"] = warnings
                report.parsed_content["retryable"] = any(_retryable_error(warning) for warning in warnings)
            first_trace = next(
                (parsed for _, _, parsed, _ in parsed_reports if parsed.run_id),
                parsed_reports[0][2],
            )
            report.extraction_provider = first_trace.provider
            report.extraction_model = first_trace.model
            report.extraction_prompt_version = first_trace.prompt_version
            report.extraction_prompt_hash = first_trace.prompt_hash
            report.extraction_run_id = first_trace.run_id
            provider_runs = [
                run_id
                for _, _, parsed, _ in parsed_reports
                for run_id in (parsed.provider_run_ids or ((parsed.provider_run_id,) if parsed.provider_run_id else ()))
                if run_id
            ]
            report.provider_run_id = provider_runs[0] if provider_runs else None
            report.provider_run_ids = json.dumps(provider_runs, ensure_ascii=False)
            transition(
                report,
                "processing" if warnings else "pending_confirmation",
                db=db,
                action="extraction_partial" if warnings else "extraction_completed",
                actor="ai:report-extractor",
                detail={
                    "metric_count": len(parsed_metrics),
                    "provider": report.extraction_provider,
                    "model": report.extraction_model,
                    "prompt_version": report.extraction_prompt_version,
                    "prompt_hash": report.extraction_prompt_hash,
                    "run_id": report.extraction_run_id,
                    "provider_run_ids": provider_runs,
                    "warnings": warnings,
                },
            )
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
        return not warnings
    except Exception as exc:
        logger.exception("report parsing failed", extra={"report_id": report_id})
        with factory() as db:
            report = db.get(ReportModel, report_id)
            if report is None:
                return False
            transition(
                report,
                "failed",
                db=db,
                action="extraction_failed",
                actor="ai:report-extractor",
                detail={"error_type": type(exc).__name__},
            )
            report.parsed_content = {
                **(report.parsed_content or {}),
                "error": "报告智能解读失败，请重新上传或稍后重试。",
                "error_class": type(exc).__name__,
                "retryable": _retryable_error(str(exc)),
            }
            db.commit()
        return False


def _confirmed_condition_codes(report: ReportModel) -> list[str]:
    """已确认健康风险的 condition_code，按出现顺序去重。

    商品按这些风险的方向取货；没有风险就没有取货依据，不是"默认给点推荐"。
    """
    evidence = report.evidence_result or {}
    codes: list[str] = []
    for finding in evidence.get("findings") or []:
        code = finding.get("condition_code") if isinstance(finding, dict) else None
        if code and code not in codes:
            codes.append(code)
    return codes


def _report_response(
    report: ReportModel,
    metrics: list[MetricModel],
    *,
    owned_by_account: bool = False,
) -> MedicalReportResponse:
    extraction_job = getattr(report, "extraction_job", None)
    return MedicalReportResponse(
        id=report.id,
        patient_id=report.patient_id,
        owned_by_account=owned_by_account,
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
                "source_url": (f"/api/health/report/{report.id}/files/{item.file_index}/pages/1"),
            }
            for item in sorted(report.files, key=lambda value: value.file_index)
        ],
        created_at=report.created_at,
        status=report.status or "pending_confirmation",
        subject_consistency=report.subject_consistency,
        evidence_result=report.evidence_result,
        processing_error=(report.parsed_content or {}).get("error"),
        processing_warnings=list((report.parsed_content or {}).get("warnings") or []),
        extraction_job=(
            {
                "status": extraction_job.status,
                "attempt_count": extraction_job.attempt_count,
                "error_class": extraction_job.error_class,
                "created_at": extraction_job.created_at,
                "started_at": extraction_job.started_at,
                "updated_at": extraction_job.updated_at,
                "completed_at": extraction_job.completed_at,
            }
            if extraction_job is not None
            else None
        ),
        extraction_trace={
            key: getattr(report, key)
            for key in (
                "extraction_provider",
                "extraction_model",
                "extraction_prompt_version",
                "extraction_prompt_hash",
                "extraction_run_id",
                "provider_run_id",
                "provider_run_ids",
                "evidence_correlation_id",
            )
            if getattr(report, key, None)
        }
        or None,
        audit_events=[
            {
                "action": event.action,
                "actor": event.actor,
                "correlation_id": event.correlation_id,
                "detail": event.detail or {},
                "created_at": event.created_at,
            }
            for event in sorted(report.audit_events, key=lambda item: item.id)
        ],
    )


@router.get("/report/{report_id}", response_model=MedicalReportResponse)
async def get_report(
    report_id: int,
    request: Request,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    report = _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )
    metrics = _ordered_metrics(db, report_id).all()
    return _report_response(report, metrics, owned_by_account=owner_id is not None)


@router.post("/report/{report_id}/confirm", response_model=MedicalReportResponse)
async def confirm_report(
    report_id: int,
    request: Request,
    confirmation: ReportConfirmationRequest,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    report = _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )
    # 闸门只看状态:仍有文件未解析完的报告状态是 processing,走不进这一步。
    # 提醒里带上具体原因(患者可见),但判定不依赖它。
    if report.status not in {"pending_confirmation", "confirmed"}:
        raise HTTPException(
            status_code=409,
            detail="报告仍有文件未完成解析，请重新上传或先修复失败文件"
            if _processing_warnings(report)
            else "报告当前状态不允许确认",
        )
    if report.subject_consistency != "same" and confirmation.subject_consistency != "same":
        raise HTTPException(status_code=422, detail="请先确认所有文件属于同一主体")
    metrics = _ordered_metrics(db, report_id).all()
    by_id = {metric.id: metric for metric in metrics}
    supplied = {item.metric_id: item for item in confirmation.observations}
    if len(supplied) != len(confirmation.observations) or not set(supplied) <= set(by_id):
        raise HTTPException(status_code=422, detail="确认列表包含重复或未知指标")
    # 目录读不到时**不挡死写路径**：患者核对好的二十项决策必须落库。
    # 降级为「维持模型值/清空」保存，等目录恢复再由评估时的权威目录重新裁决。
    # 这是 ARCHITECTURE.md 既定降级哲学（服务不可用时返回空证据并让上层继续运行，
    # 不伪造检索结果）在指标目录上的延伸。
    try:
        metric_catalog = await fetch_metric_catalog()
    except EvidenceBridgeError:
        metric_catalog = None
    canonical_codes = {item["code"] for item in metric_catalog} if metric_catalog is not None else None
    now = datetime.now()
    corrected_ids: list[int] = []
    confirmed_ids: list[int] = []
    excluded_ids: list[int] = []
    for metric in metrics:
        item = supplied.get(metric.id)
        if item is None or item.decision == "excluded":
            metric.confirmation_status = "excluded"
            metric.confirmed_value = None
            metric.confirmed_unit = None
            metric.confirmed_reference_range = None
            metric.confirmed_evidence_text = None
            metric.confirmed_at = now
            excluded_ids.append(metric.id)
            continue
        requested_code = (item.metric_code or metric.metric_code or "").strip()
        code = resolve_metric_code(requested_code or metric.metric_name or "", canonical_codes)
        if item.decision == "corrected":
            if not item.value or not item.unit:
                raise HTTPException(status_code=422, detail=f"指标 {metric.id} 的修正值不完整")
            try:
                corrected_value = float(item.value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"指标 {metric.id} 的修正值必须是单个数字") from exc
            if not math.isfinite(corrected_value):
                raise HTTPException(status_code=422, detail=f"指标 {metric.id} 的修正值无效")
        # 目录不可用（降级）：无法仲裁编码，维持别名归一化的候选并清空 ——
        # 与「目录里没有这个编码」同路：不跨证据边界，评估时表现为 unmatched，
        # 等目录恢复后由评估路径按权威目录重新裁决。
        code_is_canonical = canonical_codes is not None and code in canonical_codes
        if not code_is_canonical:
            # Confirmed unknown anomalies stay auditable but never cross the
            # evidence boundary; assessment reports them as unmatched.
            metric.metric_code = None
            metric.confirmation_status = item.decision
            metric.confirmed_value = item.value if item.decision == "corrected" else metric.metric_value
            metric.confirmed_unit = item.unit if item.decision == "corrected" else metric.unit
            metric.confirmed_reference_range = (
                item.reference_range
                if item.decision == "corrected" and item.reference_range is not None
                else metric.reference_range
            )
            metric.confirmed_evidence_text = item.evidence_text or metric.evidence_text
            metric.confirmed_at = now
            (corrected_ids if item.decision == "corrected" else confirmed_ids).append(metric.id)
            continue
        metric.metric_code = code
        metric.confirmation_status = item.decision
        metric.confirmed_value = item.value if item.decision == "corrected" else metric.metric_value
        metric.confirmed_unit = item.unit if item.decision == "corrected" else metric.unit
        metric.confirmed_reference_range = (
            item.reference_range
            if item.decision == "corrected" and item.reference_range is not None
            else metric.reference_range
        )
        metric.confirmed_evidence_text = item.evidence_text or metric.evidence_text
        metric.confirmed_at = now
        (corrected_ids if item.decision == "corrected" else confirmed_ids).append(metric.id)
    transition(report, "confirmed", db=db, action="confirmed", actor=resolve_owner(request).subject)
    report.subject_consistency = confirmation.subject_consistency or report.subject_consistency or "same"
    report.evidence_result = None
    _audit(
        db,
        report,
        "confirmed",
        {
            "confirmed_metric_ids": confirmed_ids,
            "corrected_metric_ids": corrected_ids,
            "excluded_metric_ids": excluded_ids,
            "subject_consistency": report.subject_consistency,
        },
        actor=resolve_owner(request).subject,
    )
    db.commit()
    try:
        return await _assess_report(report, db, owned_by_account=owner_id is not None)
    except EvidenceBridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/report/{report_id}/assess", response_model=MedicalReportResponse)
async def assess_report(
    report_id: int,
    request: Request,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    report = _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )
    if report.status not in {"confirmed", "assessed"}:
        raise HTTPException(status_code=409, detail="请先确认报告指标")
    if _processing_warnings(report):
        raise HTTPException(
            status_code=409,
            detail="报告仍有文件未完成解析，不能生成健康提示",
        )
    try:
        return await _assess_report(report, db, owned_by_account=owner_id is not None)
    except EvidenceBridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _assess_report(
    report: ReportModel, db: Session, *, owned_by_account: bool = False
) -> MedicalReportResponse:
    # 准入由调用方按状态判定(仍有文件未解析完 → 状态是 processing)。
    # 这里不再从 parsed_content 重新推导闸门 —— 那是旧的双通道。
    if report.status not in {"confirmed", "assessed"}:
        raise EvidenceBridgeError("报告当前状态不允许生成健康提示")
    metrics = _ordered_metrics(db, report.id).all()
    observations, local_skipped, local_unmatched = build_observations_with_unmatched(metrics)
    typed_result = EvidenceMatchResponse.model_validate(await match_published_evidence(observations))
    source_by_id = {
        observation["observation_id"]: SourceObservation.model_validate(
            {key: value for key, value in observation.items() if key != "confirmation_status"}
        )
        for observation in observations
    }
    if typed_result.unmatched:
        typed_result = typed_result.model_copy(
            update={
                "unmatched": [
                    item.model_copy(
                        update={"source_observation": item.source_observation or source_by_id.get(item.observation_id)}
                    )
                    for item in typed_result.unmatched
                ]
            }
        )
    if local_unmatched:
        typed_result = typed_result.model_copy(
            update={
                "unmatched": [
                    *typed_result.unmatched,
                    *(Unmatched.model_validate(item) for item in local_unmatched),
                ]
            }
        )
    if local_skipped:
        typed_result = typed_result.model_copy(
            update={
                "skipped": [
                    *typed_result.skipped,
                    *(Skipped.model_validate(item) for item in local_skipped),
                ]
            }
        )
    if typed_result.unmatched:
        finding_count = len(typed_result.findings)
        unmatched_count = len(typed_result.unmatched)
        summary = (
            f"发现 {finding_count} 个可能相关健康问题；另有 {unmatched_count} 条指标与健康问题关联暂无已审核知识卡。"
            if finding_count
            else f"发现 {unmatched_count} 个异常指标，但暂无已审核内容。"
        )
        typed_result = typed_result.model_copy(
            update={
                "message": summary,
                "patient_reply": typed_result.patient_reply.model_copy(
                    update={
                        "summary": summary,
                        "unmatched_count": unmatched_count,
                    }
                ),
            }
        )
    report.evidence_result = typed_result.model_dump(mode="json")
    report.evidence_correlation_id = typed_result.correlation_id
    _audit(
        db,
        report,
        "assessed",
        {
            "finding_count": len(typed_result.findings),
            "unmatched_count": len(typed_result.unmatched),
            "skipped_count": len(typed_result.skipped),
            "correlation_id": typed_result.correlation_id,
        },
        actor="system:evidence-service",
    )
    transition(report, "assessed", db=db, action="assessed", actor="system:report-assessor")
    db.commit()
    db.refresh(report)
    metrics = _ordered_metrics(db, report.id).all()
    return _report_response(report, metrics, owned_by_account=owned_by_account)


@router.get("/report/{report_id}/recommendations", response_model=RecommendationResponse)
async def get_report_recommendations(
    report_id: int,
    request: Request,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    """推荐商品（同源）。浏览器只与本服务通信，商城由服务端取。

    为空时必须说明**为什么**为空，这些情况不得合并成一句「暂无推荐」：

    - `no_published_card`：报告没有可据以取货的健康风险（本报告自身的事实，不调商城）；
    - `no_label_data`：商城调用成功，但按标签过滤后没有商品——今天映射本身为空，
      所以这条同时覆盖"还没映射标签"与"映射了但没货"，两者的修复动作都在商城侧；
    - `mall_unavailable`：商城不可达/超时/返回业务错误/凭据未配置。

    商城不可达**不报错**：返回空列表与原因，页面照常渲染「暂无推荐」。
    """
    report = _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )

    conditions = _confirmed_condition_codes(report)
    if not conditions:
        return RecommendationResponse(items=[], reason="no_published_card")

    labels: list[tuple[str, str]] = []
    for condition_code in conditions:
        labels.extend(label_pairs_for(condition_code))
    # 无标签仍然**照常调用商城**（#174 的验收项就是"服务端经商城端点取货"）：
    # 早起返回会让这条 AC 只剩测试里的 mock，且链路上最易失效的一段没有真实调用证据。
    result = await fetch_goods(labels=tuple(labels))
    return RecommendationResponse(
        items=serialized(result.items),
        reason=result.reason,
    )


@router.get("/report/{report_id}/metrics", response_model=list[MetricRecord])
async def get_report_metrics(
    report_id: int,
    request: Request,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )
    return [_metric_response(metric) for metric in _ordered_metrics(db, report_id).all()]


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
    request: Request,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )
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


@router.delete("/report/{report_id}")
async def delete_report(
    report_id: int,
    request: Request,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    report = _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )
    # 先删数据库主记录；向量索引是尽力而为，放在 DB 成功之后，
    # 避免 DB 删除失败时向量索引已被清掉造成状态不一致。
    stored_paths = [Path(item.stored_path) for item in report.files]
    db.delete(report)
    db.commit()
    for path in stored_paths:
        path.unlink(missing_ok=True)
    if stored_paths:
        with contextlib.suppress(OSError):
            stored_paths[0].parent.rmdir()
    return {"message": "报告已删除", "report_id": report_id}


@router.post("/report/{report_id}/recommendations/{spu_id}/cart-link", response_model=CartLinkResponse)
async def create_cart_link(
    report_id: int,
    spu_id: str,
    request: Request,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    """构造「加入购物车」深链（同源，POST 因为它在推进一次状态转换）。

    这是 #175 的**签发端**：本仓只构造 URL 与签名，**不调用商城任何写接口、不持有
    购物车状态**。跳转由浏览器完成，加购由商城完成。

    与推荐端点同样的降级口径：构造不出来就**如实说明是哪一种**，不发一个注定被拒的链接。
    """
    report = _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )
    if report.status not in COMPLETED_FOR_LINK:
        return CartLinkResponse(url=None, reason="report_not_ready")

    try:
        link = build_deep_link(spu_id=spu_id, detection_id=f"report-{report.id}")
    except DeepLinkError:
        # 不把原因透给患者（可能是密钥未配置这类运维细节），但也不伪装成成功。
        logger.warning("加购深链构造失败 report=%s", report.id)
        return CartLinkResponse(url=None, reason="link_unavailable")

    return CartLinkResponse(url=link.url, reason=None)
