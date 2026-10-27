"""Report upload, parsing and metric endpoints."""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import math
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
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

from app.api.deps import ACCESS_REASON_HEADER, db_dependency, session_owner_dependency
from app.config import get_settings
from app.data.models import MedicalReport as ReportModel
from app.data.models import MetricRecord as MetricModel
from app.data.models import ReportExtractionJob
from app.data.models import ReportFile as ReportFileModel
from app.schema.evidence import (
    EvidenceMatchResponse,
    Skipped,
    SourceObservation,
    Unmatched,
    build_patient_notices,
)
from app.schema.report import (
    CartLinkResponse,
    MedicalReportResponse,
    MetricRecord,
    RecommendationGroup,
    RecommendationResponse,
    ReportConfirmationRequest,
    ReportMetricCounts,
)
from app.service.admission_projection import (
    admission_shapes,
    has_conclusion,
)
from app.service.confirmation_decision import is_excluded, request_decision, require_full_coverage
from app.service.consent import has_valid
from app.service.consent_vocabulary import PRODUCT_RECOMMENDATION, REPORT_INTERPRETATION
from app.service.deep_link import DeepLinkError, build_deep_link
from app.service.evidence_bridge import (
    EvidenceBridgeError,
    build_observations_with_unmatched,
    fetch_metric_catalog,
    infer_abnormal_flag_for_metric,
    match_published_evidence,
    resolve_metric_code,
)
from app.service.interpretation_failure import (
    FAILURES_ORDERED,
    InterpretationFailure,
    classify,
    gate_detail,
    is_retryable,
    patient_action,
    report_failure,
)
from app.service.mall_goods import MallGoodsItem, fetch_goods, label_pairs_for, serialized
from app.service.metric_effective_value import effective_value
from app.service.metric_rows import deduplicate
from app.service.origin_location import page_url, source_id_for
from app.service.recommendation_empty_reason import REPORT_ACCESS_BY_STATUS
from app.service.report_material import (
    ACCEPTED_EXTENSIONS,
    KIND_MEDIA_TYPE,
    extension_of,
    mismatch_reason,
    page_count,
)
from app.service.report_material import (
    resolve as resolve_material,
)
from app.service.report_metric_counts import metric_counts
from app.service.report_ownership import UNOWNED_SENTINEL, resolve_owner
from app.service.report_status import record_event, transition
from app.service.report_subject import (
    SubjectGateError,
    apply_declaration,
    can_enter_reading,
    gate,
    initial_consistency,
)
from app.service.upload_policy import upload_policy
from app.service.vision_encoder import ParsedReport, get_vision_encoder_service

#: 只有这些状态的报告才谈得上加购：未完成确认/评估的报告没有可据以取货的风险。
COMPLETED_FOR_LINK = frozenset({"confirmed", "assessed"})

#: 词表的取值集合，供组装失败块时判「库里的成因是不是一个已知的成因」。历史行里可能
#: 是缺失，也可能是别的形状 —— 两种都读成「没有成因」，不猜。
FAILURE_SET = frozenset(FAILURES_ORDERED)

#: 上传被同意闸门拒绝时给患者看的那句话。**成因**仍走 `X-Report-Access-Reason`
#: （`REPORT_ACCESS_BY_STATUS[403]`），这句话只是给人读的；患者侧的动作是回到
#: 「隐私与授权」完成授权，所以句子要指向那里，而不是让他在上传页反复重试。
CONSENT_REQUIRED_DETAIL = "尚未同意「报告解读」授权，请先在商城模块完成同意后再上传报告"

router = APIRouter()
logger = logging.getLogger(__name__)


def _gate_detail(report: ReportModel) -> str | None:
    """闸门被拒时说的话：**从成因派生**。

    `gate_detail` 的家是词表模块（成因 → 那句话的唯一一处）。这一层只负责把库里的
    成因取出来 —— 闸门本身由报告状态决定，这句话不影响判定。
    """
    return gate_detail((report.parsed_content or {}).get("failure"))


def _authorized_report(db: Session, report_id: int, *, owner_id: str) -> ReportModel:
    """只按**主体的存储标识**判定归属。报告令牌旁路已随 #172 一并移除。

    注意 `report.access_token_hash` 不再参与判定，但**不要求它为空**：历史行里有它，
    而「有令牌」不构成访问依据——本票之后没有任何凭据能替代主体。

    三个分支都给同一个状态与同一个**具名成因**：报告不存在、无主、不属于你 —— 对患者
    是同一件事，不分别暴露（分别暴露等于告诉调用者「这个 id 存在但不是你的」）。
    """
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None or not report.owner_id:
        raise _report_not_found()
    # **无主历史行对任何人封闭。** 移除令牌旁路之后有一个更弱的路径会冒出来：
    # `resolve_owner` 在无会话时返回哨兵值，若它与旧行的 `owner_id` 相等，
    # 一个未认证请求就能读到无主报告——那比被移除的令牌旁路还宽。所以哨兵值
    # 显式拒绝，双方都不算匹配。
    if report.owner_id == UNOWNED_SENTINEL:
        raise _report_not_found()
    if not hmac.compare_digest(report.owner_id or "", owner_id):
        raise _report_not_found()
    return report


def _report_not_found() -> HTTPException:
    """报告读不到时的唯一形状：状态 + **具名成因**。

    成因走响应头而不是从 `detail` 里猜 —— 前端此前用 `/\\b(401|404)\\b/` 匹配那句人话，
    而 `detail` 一旦被写得更具体（例如带上报告号），正则就再也匹配不到，归因**静默翻转**。
    """
    return HTTPException(
        status_code=404,
        detail="报告不存在",
        headers={ACCESS_REASON_HEADER: REPORT_ACCESS_BY_STATUS[404]},
    )


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
                # 页数的唯一计算入口在原始材料模块：读不出页数时是 None（未知），
                # 不再用 1 冒充已知值。
                page_count=page_count(filename, content),
            )
        )


def _stored_bbox(value: object, *, upper: float | None = None) -> list[float] | None:
    """读一条已存下来的坐标：**读取边界**，容忍历史形状但产出可用的指针。

    存下来的零面积框指不到任何东西 —— 读出来是 ``None``（定位为空），不是把它
    原样透出给契约（那会让整份报告打不开），也不是猜一个坐标。
    """
    from app.service.origin_location import clean_bbox

    return clean_bbox(value, upper=upper, strict=True)


def _metric_response(metric: MetricModel, admission: str | None = None) -> MetricRecord:
    """一个数据库行 → 逐行契约。

    ``admission`` 由调用方**传进来**，不在这里现算：准入结论的来源是
    `app/service/admission_projection.py`（唯一一处），本函数只负责把它写进契约。
    默认 `None` 是刻意的 —— 它同时表示「进入了解读」与「这份报告还没评估」，两者的
    区分由报告级台账承担（见 `MedicalReportResponse.admission`）。
    """

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
        admission_reason=admission,
        # 历史行里可能存着退化框（零面积）。它不是可用的指针 —— 按词条
        # 「任一组件缺失时定位为空，不猜测」，读出来就是 None，而不是让
        # 整份报告打不开。
        bbox=_stored_bbox(metric.bbox),
        bbox_normalized=_stored_bbox(metric.bbox_normalized, upper=1000),
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


def _interpretation_failure_block(report: ReportModel) -> dict | None:
    """报告级的解读失败块 —— 成因与由它派生的两项，从**落库的事实**组装。

    库里存的是 `parsed_content["failure"]`（成因）与 `parsed_content["file_results"]`
    （每个文件的状态）。这一层只做组装：`retryable` 与 `action` 由成因**派生**
    （`is_retryable` / `patient_action` 是唯一的口径），失败的文件只挑文件名 —— 每个
    文件条目里的 `error` 自由文本**不出域**，它是排障用的，患者看它没有用。

    **历史行**：本次改动之前的 `parsed_content` 只有 `error`（一句通用话术）与
    `warnings`，没有 `failure`。那种行读出来是空失败块 —— 我们**不知道**它的成因，
    所以不猜一个。这是诚实的空：患者侧据此退回状态级的那句话，而不是收到一个编出来的
    成因。
    """
    parsed = report.parsed_content or {}
    cause = parsed.get("failure")
    # `isinstance` 先判：`parsed_content` 是 JSON 列，历史行里这个键可能是任何形状。
    # 一个列表或字典直接拿去查集合会抛 `TypeError: unhashable type` —— 那会让整份报告
    # 的读路径 500，患者连打开都做不到，而这只是一个我们读不懂的陈旧字段。
    if not isinstance(cause, str) or cause not in FAILURE_SET:
        return None
    return {
        "cause": cause,
        "retryable": is_retryable(cause),
        "action": patient_action(cause),
        "failed_files": [
            {"file_index": item.get("file_index"), "filename": item.get("filename")}
            for item in parsed.get("file_results") or []
            if isinstance(item, dict) and item.get("status") == "failed" and item.get("filename")
        ],
    }


def _partial_files_in_flight(report: ReportModel) -> bool:
    """还有文件没解析完吗。**只**决定闸门被拒时说什么，不决定闸门本身。

    #139 已把「能不能确认/评估」收到报告状态上，这里不再把闸门搬回 JSON —— 它靠报告
    状态（`processing`）拒人，这个函数只为那句话提供依据。
    """
    parsed = report.parsed_content or {}
    return any(
        isinstance(item, dict) and item.get("status") == "failed"
        for item in parsed.get("file_results") or []
    )


@dataclass(frozen=True)
class ParseOutcome:
    """一次报告解析的结果：**成没成，没成的话坏在哪一环**（GLOSSARY.md 的「解读失败」）。

    它是解析路径与队列之间的唯一交接物。此前这里返回的只是一个 ``bool``，而重试与否
    由 worker 从 ``parsed_content["retryable"]`` 读一个布尔 —— 那个布尔的来源是对
    ``f"{filename}: {error}"`` 的子串嗅探。现在两者都在这里：``succeeded`` 说的是
    「这一轮的每个文件都干净地解析完了吗」，``failure`` 说的是「没有的话坏在哪一环」。

    ``succeeded`` 为假**不等于**这份报告就是失败：一部分文件解析成功、另一部分失败时，
    成功的那些指标已经落库，报告停在 ``processing`` 等队列决定要不要再跑一轮。所以
    两个字段是**各自独立**的 —— ``succeeded`` 是「这一轮的全部文件」，``failure`` 是
    「没成的那些坏在哪」。
    """

    succeeded: bool
    failure: InterpretationFailure | None = None

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
    # **闸门在读取任何文件之前**（#269）。否则「没有同意」这件事会表现为「格式不对」
    # ——服务端已经收下了字节、再挑毛病，而那正是「门槛只在界面上」的形状。
    #
    # 判据只有一处（`consent.has_valid`），与 `/api/auth/consent` 读取状态是**同一次**
    # 判定：不可能出现「状态说已同意、上传却被拒」。
    #
    # 被问的是**这份报告将来的 owner**（`resolve_owner`，与下面落库用的是同一个值），
    # 不是会话主体：一个还没有主体的请求（无主哨兵）因此落 403 —— 没有主体就无从同意，
    # fail closed，而不是先收下再说。
    if not has_valid(db, resolve_owner(request).storage_id, REPORT_INTERPRETATION):
        raise HTTPException(
            status_code=403,
            detail=CONSENT_REQUIRED_DETAIL,
            headers={ACCESS_REASON_HEADER: REPORT_ACCESS_BY_STATUS[403]},
        )
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
        if extension_of(filename) not in ACCEPTED_EXTENSIONS:
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
        # 类型判定从**内容**来，不再只看后缀：改名成 .pdf 的 PNG 在这里被拒绝，
        # 而不是放行之后在查看原文时才失败。后缀在受理集合内、内容也认不出来时
        # 以后缀为准（截断的 PDF 仍然是 PDF），不因此拒绝。
        material = resolve_material(filename, content)
        if material.mismatch:
            raise HTTPException(status_code=415, detail=mismatch_reason(material))
        accepted_files.append((file_index, filename, material.media_type, content))

    report = ReportModel(
        patient_id=patient_id,
        report_type=report_type or "体检",
        department=department,
        parsed_content={"file_count": len(accepted_files)},
        subject_consistency=initial_consistency(len(accepted_files)),
        owner_id=resolve_owner(request).storage_id,
        exam_date=datetime.now(),
    )
    # 状态由唯一迁移入口设置。此刻对象还没 flush，列默认值尚未落到属性上，
    # 所以这是「从无到有」的第一次迁移（不带 `db`：还没有 id 可写事件）。
    transition(report, "processing")
    db.add(report)
    db.flush()
    if report.id is None:
        raise HTTPException(status_code=500, detail="报告写入数据库失败")
    _persist_report_files(report.id, accepted_files, db)
    # 「这份报告被接收」是一笔**审计专用**事件：状态在上一行就已经设好，这里没有
    # 迁移。它此前写成一条与迁移事件同形的事件，于是读者无法分辨「状态动过」与
    # 「状态没动、只是记了一笔」—— `record_event` 是这两类分开之后的那个入口。
    record_event(
        db,
        report,
        "uploaded",
        actor=resolve_owner(request).subject,
        detail={"file_count": len(accepted_files)},
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
) -> ParseOutcome:
    """解析这份报告的全部文件，并把**结论**（成没成、没成的话坏在哪一环）交出去。

    这里**不落终态**。队列才是「还有没有救、还要不要再跑一轮」的唯一判定者（它知道
    还剩几次尝试），而报告状态是患者可见的 —— 一次还会被重试的失败不该先给患者看一眼
    「解读失败」。所以本函数只写事实（成因、细节、抽取轨迹），由 ``report_worker``
    决定这一轮之后报告落到哪里。
    """
    try:
        vision = get_vision_encoder_service()

        def parse_file(source):
            file_index, filename, _, content = source
            try:
                parsed = vision.parse(content, filename)
            except Exception as exc:
                # 抽取器自己都没接住的异常：它没能告诉我们坏在哪一环。那是**我们这边**
                # 的逻辑缺陷，不是这份材料的问题，更不是患者的输入的问题 —— 判成
                # service 而不是 content，否则一个我方崩溃会变成一句「请重新上传」。
                parsed = ParsedReport(
                    report_type="unknown",
                    raw_text="",
                    metrics=[],
                    page_count=0,
                    success=False,
                    error=str(exc),
                    failure=classify("service", exc),
                )
            if not parsed.success and parsed.failure is None:
                # 抽取器说它失败了，却没点名成因。这不是一个可以让判定自己长回来的洞：
                # 出口处补齐它，用**错误文本**（不是文件名 —— 文件名今天在被嗅探的
                # 文本里，那正是要消灭的东西）。
                parsed = replace(parsed, failure=classify("provider", text=parsed.error or ""))
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
                    # source_id 的最终形态只有一处定义（origin_location）。
                    # 抽取器给了自定义标识就**保留它**（只补文件前缀）——
                    # 那是它的定位依据，用生成的序号替换会丢掉 provider 的标识。
                    "source_id": _final_source_id(item.source_id, file_index, item.page_number, metric_index),
                }
            )
            for file_index, _, parsed, error in successful_reports
            for metric_index, item in enumerate(parsed.metrics, start=1)
        ]
        # 唯一的同一观测执行点（app/service/metric_rows.py）。解析器里那份页内
        # 去重已删除 —— 它没有文件编号、不看原文证据，对同一份内容给出不同的行数。
        parsed_metrics = sorted(
            deduplicate(parsed_metrics),
            key=lambda item: (
                item.source_file_index,
                item.page_number or 0,
                item.bbox_normalized[1] if item.bbox_normalized else 1001,
                item.bbox_normalized[0] if item.bbox_normalized else 1001,
                item.metric_name.casefold(),
                item.metric_value,
            ),
        )
        # 报告级成因：所有失败文件里**最上游**的那一环。一个指标都没抽到、而每个文件
        # 又都说自己没错（抽取器答上了、只是页面里没有可确认的指标）时，那是内容事实。
        failure = report_failure(
            parsed.failure for _, _, parsed, _ in parsed_reports if parsed.failure
        ) or (None if parsed_metrics else "content_unrecognized")

        with factory() as db:
            report = db.get(ReportModel, report_id)
            if report is None:
                return ParseOutcome(succeeded=False, failure=failure)
            db.query(MetricModel).filter(MetricModel.report_id == report_id).delete(synchronize_session=False)
            report.parsed_content = {
                "report_type": successful_reports[0][2].report_type if successful_reports else "unknown",
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
            if failure is not None:
                report.parsed_content["failure"] = failure
            warnings = [f"{filename}: {error}" for _, filename, _, error in parsed_reports if error]
            if warnings:
                report.parsed_content["warnings"] = warnings
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
            if parsed_metrics:
                # 抽到了东西就不是失败 —— 哪怕还有文件没解析完。剩下那部分由队列决定
                # 要不要再跑一轮，报告本身停在 processing 等它。
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
                        "failure": failure,
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
        return ParseOutcome(succeeded=not warnings, failure=failure)
    except Exception as exc:
        logger.exception("report parsing failed", extra={"report_id": report_id})
        # 走到这里说明失败发生在**我们这边**（数据库、目录、度量去重……）：抽取器
        # 的异常在 parse_file 里就被接住了。一个瞬时的基础设施故障不该对患者表现为
        # 「这份报告读不出来，请重新上传」—— 那正是 ADR-0006 那次事故的形状。
        failure = classify("service", exc)
        # 记事实这一步**自己也可能失败**：数据库都没了，`db.get` 会原样再抛一次。
        # 让那个异常逃出去会从 `run_next_job` 里炸掉整个 worker 循环，后面每一份
        # 报告都不再被处理 —— 一次数据库抖动变成一条永久停摆的队列。所以这里只记
        # 日志：结论已经拿到了，落不落得下不影响队列该做什么。
        try:
            with factory() as db:
                report = db.get(ReportModel, report_id)
                if report is None:
                    return ParseOutcome(succeeded=False, failure=failure)
                report.parsed_content = {
                    **_carried_forward(report.parsed_content),
                    "error": "报告智能解读失败，请重新上传或稍后重试。",
                    "failure": failure,
                }
                db.commit()
        except Exception:  # noqa: BLE001 —— 见上：这一层的失败不许逃出解析路径
            logger.exception("could not record the parsing failure", extra={"report_id": report_id})
        return ParseOutcome(succeeded=False, failure=failure)



def _confirmed_conditions(report: ReportModel) -> list[tuple[str, str]]:
    """已确认健康风险的 `(condition_code, condition_name)`，按出现顺序去重。

    商品按这些风险的方向取货；没有风险就没有取货依据，不是"默认给点推荐"。
    名字取证据服务给的那个（`condition_name`），缺失时退回编码 —— 分组标题要有一个
    可读的东西，而本服务不**编**一个名字出来。
    """
    evidence = report.evidence_result or {}
    conditions: list[tuple[str, str]] = []
    seen: set[str] = set()
    for finding in evidence.get("findings") or []:
        code = finding.get("condition_code") if isinstance(finding, dict) else None
        if not code or code in seen:
            continue
        seen.add(code)
        name = finding.get("condition_name") or code
        conditions.append((code, name))
    return conditions


async def _recommendation_groups(
    conditions: list[tuple[str, str]],
) -> tuple[list[RecommendationGroup], tuple[MallGoodsItem, ...], str | None]:
    """按健康方向逐组取货，返回 (分组, 扁平并集, 整体原因)（#271）。

    **一次并集取货回答不了「这件商品属于哪个健康问题」。** 商品的 `labels` 字段在商城的
    列表/批量接口里不返回（ADR 0005 的 2026-09-30 修订），而按标签取货的接口本身就不带
    「这条记录命中哪个标签」——它只回一个命中集合。用一次并集结果反推归属只能靠猜，而
    猜错的方向不对称（把血脂的商品说成「针对血糖偏高」，是把一条不存在的关系写给患者）。

    所以改为**每个有映射的健康方向问一次**商城（`label_pairs_for` + 按标签取货），那一次
    的命中集合就是该组的商品，归属由商城的查询本身给出，不需要第二份商品-方向对应表。
    代价是 N 次调用（N 是有映射的方向数，一份报告通常一两个），换来的是分组不是装饰。

    没有映射的方向**不去问商城**，它的组如实说 `no_label_data`：患者看到的是「该方向
    暂无可推荐商品」，而「没建映射」是内部事实。

    扁平 `items` / `reason` 保持旧前端读到的口径，且与改动前**一次并集调用**的结论相同：
    并集按首次出现去重；有商品时原因为空；没有任何商品时，只要有一次调用到达了商城就是
    `no_label_data`，全都不可达才是 `mall_unavailable`。一组没货不影响另一组有货，所以
    整体原因是并集上的结论，不是任何单组的。
    """
    groups: list[RecommendationGroup] = []
    flat: dict[str, MallGoodsItem] = {}
    claims: list[tuple[str, str, tuple[MallGoodsItem, ...], str | None]] = []
    calls = 0
    any_reachable = False
    for code, name in conditions:
        pairs = label_pairs_for(code)
        if not pairs:
            # 没有映射就**不发出这次取货**：命中集合不是「商城说没有」，是我们没问。
            claims.append((code, name, (), "no_label_data"))
            continue
        calls += 1
        result = await fetch_goods(labels=pairs)
        if result.reason != "mall_unavailable":
            any_reachable = True
        for item in result.items:
            flat.setdefault(item.id, item)
        claims.append((code, name, result.items, result.reason))

    # **一件商品只归一组。** 一件同时挂两个方向标签的商品会出现在两次取货里；在两个组里
    # 各展示一遍是重复推荐，而从后一组里悄悄抹掉又会让后一组看起来「没货」。这里按组序
    # 先到先得：某组的商品只保留**还没有被更靠前的一组认领**的那些。组序就是报告里健康
    # 问题的出现顺序，与 `items` 的顺序一致。
    claimed: set[str] = set()
    for code, name, items, reason in claims:
        mine = tuple(item for item in items if item.id not in claimed)
        claimed.update(item.id for item in mine)
        groups.append(
            RecommendationGroup(
                condition_code=code,
                condition_name=name,
                items=serialized(mine),
                # 商城不可达是整次取货的事实，每一组都带它 —— 否则患者会把「打不开商城」
                # 读成「这个方向没货」。
                reason="mall_unavailable" if reason == "mall_unavailable" else (None if mine else "no_label_data"),
            )
        )

    items = tuple(flat.values())
    if items:
        reason: str | None = None
    elif calls and not any_reachable:
        reason = "mall_unavailable"
    else:
        # 有调用到达商城但没货，或一个方向都没建映射（一次都没发出）——两者对患者都是
        # 「这些健康方向暂无可推荐商品」，与改动前把空标签集交给一次 `fetch_goods`
        # 得到的结论一致，旧前端看到同一句话。
        reason = "no_label_data"
    return groups, items, reason


def _final_source_id(existing: str | None, file_index: int, page_number: int | None, fallback: int) -> str:
    """最终 `source_id`：抽取器的标识优先，只补文件前缀。"""
    if existing:
        return existing if existing.startswith(f"file-{file_index}/") else f"file-{file_index}/{existing}"
    return source_id_for(file_index, page_number, fallback)


def _source_position(existing: str | None, fallback: int) -> int:
    """从既有 `source_id` 里取页内序号（`...-m{position}`），取不到用 fallback。

    抽取器已经给出过 `p{page}-m{index}`；重算时不能改变它，否则同一行的
    `source_id` 会在两次行走之间漂移。
    """
    if existing and "-m" in existing:
        tail = existing.rsplit("-m", 1)[1]
        if tail.isdigit():
            return int(tail)
    return fallback


def _patient_notices_from_stored(stored: object) -> dict | None:
    """把库里存的 `evidence_result` 归一成当前的患者投影形状。

    库里可能存着两种：**新形状**（`PatientNotices` 的 dump）与**旧形状**
    （`EvidenceMatchResponse` 的 dump，含 `schema_version` / `patient_reply` /
    内部层 `findings`）。旧行是已经写进数据库的数据 —— 读路径必须能读。

    旧行按**已投影过的患者字段**（`patient_reply`）重建，而不是把内部层
    `findings` 重新投影一次：那份 dump 当时就是按当时的规则投影的，重投影会
    引入一个从未发生过的形状变化。
    """
    if not isinstance(stored, dict):
        return stored
    if "schema_version" not in stored:
        return stored  # 已经是新形状
    patient_reply = stored.get("patient_reply") or {}
    unmatched = list(stored.get("unmatched") or [])
    return {
        "correlation_id": stored.get("correlation_id") or "",
        "title": patient_reply.get("title") or "体检报告解读与健康风险提示",
        "summary": patient_reply.get("summary") or stored.get("message") or "",
        "findings": list(patient_reply.get("findings") or []),
        "unmatched": unmatched,
        "skipped": list(stored.get("skipped") or []),
        "unmatched_count": patient_reply.get("unmatched_count", len(unmatched)),
        "disclaimer": patient_reply.get("disclaimer") or "",
    }


def _carried_forward(parsed_content: dict | None) -> dict:
    """一次**新一轮**失败要保留的旧字段。

    失败路径过去写的是 `{**parsed_content, "failure": ..., "error": ...}` —— 把上一轮
    解析的**全部**结果一起留下。其中 `file_results` 会说谎：一次部分解析留下
    `[{"filename": "坏.png", "status": "failed"}]`，下一轮整份材料因为**服务不可用**而失败
    时，那份旧的 `file_results` 还在，于是在新的失败块里，「坏.png」被列成这一次失败的
    文件 —— 患者看到的是一次服务故障去归咎于某个文件。

    保留的只有抽取轨迹（它是**历史事实**，且下一轮可能没能产生任何轨迹），逐轮结果一律
    丢掉：这一轮的失败块只描述这一轮。
    """
    parsed = parsed_content or {}
    return {
        key: parsed[key]
        for key in (
            "report_type",
            "raw_text",
            "page_count",
            "metric_count",
            "extraction_provider",
            "extraction_model",
            "extraction_prompt_version",
            "extraction_prompt_hash",
            "extraction_run_id",
            "provider_run_id",
            "provider_run_ids",
            # `error` 保留：它可能是**本轮**早些时候（读盘失败）刚写下的那句话，
            # 而终态分支要用 `error or <通用话术>` 决定说不说它。丢掉它会把「原文读取
            # 失败」降级成一句通用话术。
            "error",
        )
        if key in parsed
    }


def _report_response(
    report: ReportModel,
    metrics: list[MetricModel],
    *,
    owned_by_account: bool = False,
    catalog: Iterable[str] | None = None,
) -> MedicalReportResponse:
    extraction_job = getattr(report, "extraction_job", None)
    assessed = has_conclusion(report.status)
    pairs, ledger = admission_shapes(metrics, assessed=assessed, catalog=catalog)
    counts = metric_counts(metrics)
    return MedicalReportResponse(
        id=report.id,
        patient_id=report.patient_id,
        owned_by_account=owned_by_account,
        report_type=report.report_type,
        exam_date=report.exam_date,
        department=report.department,
        metrics=[_metric_response(metric, reason) for metric, reason in pairs],
        admission=ledger,
        counts=ReportMetricCounts(
            parsed=counts.parsed,
            abnormal=counts.abnormal,
            needs_review=counts.needs_review,
            undecided=counts.undecided,
            in_range=counts.in_range,
        ),
        files=[
            {
                "file_index": item.file_index,
                "original_filename": item.original_filename,
                "media_type": item.media_type,
                "page_count": item.page_count,
                # files 列表语义化为「该文件首页」。
                "source_url": page_url(report.id, item.file_index, 1),
            }
            for item in sorted(report.files, key=lambda value: value.file_index)
        ],
        created_at=report.created_at,
        status=report.status or "pending_confirmation",
        subject_consistency=report.subject_consistency,
        evidence_result=_patient_notices_from_stored(report.evidence_result),
        interpretation_failure=_interpretation_failure_block(report),
        extraction_job=(
            {
                "status": extraction_job.status,
                "attempt_count": extraction_job.attempt_count,
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
    # 提醒里带上成因(患者可见),但判定不依赖它。
    if report.status not in {"pending_confirmation", "confirmed"}:
        raise HTTPException(
            status_code=409,
            detail=_gate_detail(report) or "报告当前状态不允许确认",
        )
    try:
        # 闸门语义由 report_subject 单点拥有：非 same 的显式表态是「停止」，
        # 不置 confirmed、不进入评估（旧实现会把它当 same 放行）。
        settled = gate(report.subject_consistency, confirmation.subject_consistency)
    except SubjectGateError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    metrics = _ordered_metrics(db, report_id).all()
    by_id = {metric.id: metric for metric in metrics}
    supplied = {item.metric_id: item for item in confirmation.observations}
    if len(supplied) != len(confirmation.observations):
        raise HTTPException(status_code=422, detail="确认列表包含重复指标")
    # 请求必须**覆盖全部**已解析指标：「没提供」此前被静默当成「排除」—— 任何一条漏提交的
    # 已解析指标会被置成 excluded，与患者明确排除不可区分（`confirmed_at` 照写、审计把两者
    # 合在一个数组里）。一次漏提交的行从此是一次 422，而不是一次患者看不到的排除。
    coverage = require_full_coverage(supplied, list(by_id))
    if not coverage.ok:
        raise HTTPException(status_code=422, detail=coverage.detail())
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
        item = supplied[metric.id]  # 覆盖面已由 require_full_coverage 保证
        # 「这条我还没动」由服务端解：落定过的沿用上次的决定，没落定过的仍是 pending。
        # 客户端因此不需要、也不允许替患者猜一个默认值（#195）。
        decision = request_decision(item.decision, status=metric.confirmation_status)
        if decision == "pending":
            # 患者没动过这一行，而它此前也没落定过 —— **什么都不做**。
            # 静默置成 excluded 正是本票要消灭的那件事（患者没看过的行会被记成他排除的）。
            continue
        if is_excluded(decision):
            metric.confirmation_status = "excluded"
            metric.confirmed_value = None
            metric.confirmed_unit = None
            metric.confirmed_reference_range = None
            metric.confirmed_evidence_text = None
            metric.confirmed_at = now
            excluded_ids.append(metric.id)
            continue
        # 先试患者选定的编码，再试指标名 —— 两者都必须**回目录验证**。
        # （只试编码会让一个目录里下架的旧编码把本来能解析的名称也一起挡掉。）
        requested_code = (item.metric_code or metric.metric_code or "").strip()
        code = resolve_metric_code(requested_code, canonical_codes) or resolve_metric_code(
            metric.metric_name or "", canonical_codes
        )
        if decision == "corrected":
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
            metric.confirmation_status = decision
            metric.confirmed_value = item.value if decision == "corrected" else metric.metric_value
            metric.confirmed_unit = item.unit if decision == "corrected" else metric.unit
            metric.confirmed_reference_range = (
                item.reference_range
                if decision == "corrected" and item.reference_range is not None
                else metric.reference_range
            )
            metric.confirmed_evidence_text = item.evidence_text or metric.evidence_text
            metric.confirmed_at = now
            (corrected_ids if decision == "corrected" else confirmed_ids).append(metric.id)
            continue
        metric.metric_code = code
        metric.confirmation_status = decision
        metric.confirmed_value = item.value if decision == "corrected" else metric.metric_value
        metric.confirmed_unit = item.unit if decision == "corrected" else metric.unit
        metric.confirmed_reference_range = (
            item.reference_range
            if decision == "corrected" and item.reference_range is not None
            else metric.reference_range
        )
        metric.confirmed_evidence_text = item.evidence_text or metric.evidence_text
        metric.confirmed_at = now
        (corrected_ids if decision == "corrected" else confirmed_ids).append(metric.id)
    # 一次确认**恰好一条**审计事件，detail 在这里一次给全。
    # 此前这里是 `transition(action="confirmed")` 写一条空壳、紧跟 `_audit` 再写一条
    # 带实体的同名事件 —— 同一次确认落下两条，读的人无法判断该信哪一条。
    transition(
        report,
        "confirmed",
        db=db,
        action="confirmed",
        actor=resolve_owner(request).subject,
        detail={
            "confirmed_metric_ids": confirmed_ids,
            "corrected_metric_ids": corrected_ids,
            "excluded_metric_ids": excluded_ids,
            "subject_consistency": report.subject_consistency,
        },
    )
    apply_declaration(report, settled)
    report.evidence_result = None
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
    if not can_enter_reading(report.subject_consistency):
        # 历史行的兼容闸门：本次收敛前，列已是 `same` 的报告可被二次确认改写成
        # `different` 并照常评估 —— 那些报告不该继续生成合并解读。
        raise HTTPException(status_code=409, detail="这批文件不属于同一主体，不能生成合并解读；请分开上传")
    if _partial_files_in_flight(report):
        raise HTTPException(
            status_code=409,
            detail=_gate_detail(report) or "报告仍有文件未完成解析，不能生成健康提示",
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
    if not can_enter_reading(report.subject_consistency):
        raise EvidenceBridgeError("这批文件不属于同一主体，不能生成合并解读")
    metrics = _ordered_metrics(db, report.id).all()
    # 权威目录:落定编码为空的指标在这里被重新裁决一次(确认那刻目录不可用的情况)。
    # 目录读不到时降级为 None —— 那些指标保持 unmatched,不猜。
    try:
        catalog = [item["code"] for item in await fetch_metric_catalog()]
    except EvidenceBridgeError:
        catalog = None
    observations, local_skipped, local_unmatched = build_observations_with_unmatched(metrics, catalog)
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
    # 患者投影：从证据服务响应**确定性**构造，唯一的出域形状。
    # 摘要**无条件**由这里产出 —— 此前只在 `unmatched` 非空时才改写 message 与
    # patient_reply.summary，两者可以各说各话。
    notices = build_patient_notices(typed_result, unmatched=typed_result.unmatched, skipped=typed_result.skipped)
    report.evidence_result = notices.model_dump(mode="json")
    report.evidence_correlation_id = typed_result.correlation_id
    # 一次评估**恰好一条**审计事件。此前两条：`_audit(action="assessed",
    # actor="system:evidence-service")` 带三个计数、紧跟
    # `transition(action="assessed", actor="system:report-assessor")` 写 `detail={}` ——
    # 同一条 `assessed` 因此有两个 actor，而两个都不是完整答案。现在 actor 是**一个**
    # 取值（它是「评估由谁产生」这个问题的唯一答案），明细由这一处一次给全。
    transition(
        report,
        "assessed",
        db=db,
        action="assessed",
        actor="system:report-assessor",
        detail={
            "finding_count": len(typed_result.findings),
            "unmatched_count": len(typed_result.unmatched),
            "skipped_count": len(typed_result.skipped),
            "correlation_id": typed_result.correlation_id,
        },
    )
    db.commit()
    db.refresh(report)
    metrics = _ordered_metrics(db, report.id).all()
    # 把**这次评估用的**目录传下去：准入台账必须与门禁做过的裁决同一口径，否则
    # 「确认那刻目录不可用、评估时目录恢复」的行会一边被送进匹配、一边显示「没有编码」。
    return _report_response(report, metrics, owned_by_account=owned_by_account, catalog=catalog)


@router.get("/report/{report_id}/recommendations", response_model=RecommendationResponse)
async def get_report_recommendations(
    report_id: int,
    request: Request,
    db: Session = Depends(db_dependency),
    owner_id: str | None = Depends(session_owner_dependency),
):
    """推荐商品（同源）。浏览器只与本服务通信，商城由服务端取。

    为空时必须说明**为什么**为空，这些情况不得合并成一句「暂无推荐」：

    - `product_recommendation_consent_required`：主体没有对当前版本的「商品推荐」同意
      （#271）。闸门在**调用商城之前** —— 没同意就根本不去问商城，这是「门槛在服务端」
      与「界面上一个勾」的区别。与 `no_published_card` 一样不调商城，但患者侧的话不同：
      这一条要指向「开启推荐」。
    - `no_published_card`：报告没有可据以取货的健康风险（本报告自身的事实，不调商城）；
    - `no_label_data`：商城调用成功，但没有商品命中这些健康方向；
    - `mall_unavailable`：商城不可达/超时/返回业务错误/凭据未配置。

    响应同时带 `groups`（按健康问题分组，副状态见 `_recommendation_groups`）与旧前端仍在
    读的扁平 `items` / `reason`。

    商城不可达**不报错**：返回空列表与原因，页面照常渲染「暂无推荐」。
    """
    report = _authorized_report(
        db,
        report_id,
        owner_id=resolve_owner(request).storage_id,
    )

    # 闸门在**读取报告数据与调用商城之前**：没有有效的推荐同意，服务端连商城都不问
    # （PRD #266 的验收项正是「没有推荐同意时，不发出任何商城请求」）。判据与状态接口
    # 是同一次判定（`consent.has_valid`，ADR 0027 的第 3 条）。
    #
    # 被问的是**会话主体**，与会话拿报告的那个主体是同一个（`_authorized_report` 刚用
    # 它判过归属）；没有会话时 `_authorized_report` 已经先拒绝，所以这里拿到的永远是
    # 一个真实的存储标识。
    if not has_valid(db, resolve_owner(request).storage_id, PRODUCT_RECOMMENDATION):
        return RecommendationResponse(
            items=[],
            reason="product_recommendation_consent_required",
            groups=[],
        )

    conditions = _confirmed_conditions(report)
    if not conditions:
        return RecommendationResponse(items=[], reason="no_published_card", groups=[])

    groups, items, reason = await _recommendation_groups(conditions)
    return RecommendationResponse(
        items=serialized(items),
        reason=reason,
        groups=groups,
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


@router.get("/upload-policy")
async def report_upload_policy():
    """受理规则（扩展名与三个上限）—— 只读，不下发任何服务端路径或解析配置。

    前端据此生成 `accept` 属性、剩余份数与超出提示；调大 `MAX_UPLOAD_FILES` 之后
    不需要再发一次前端版本。前端拿不到时回落到自己的内建默认值，不因为一次请求
    失败挡住上传（`frontend/src/uploadPolicy.js`）。
    """
    return upload_policy()


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
    if source is None or page_number < 1:
        raise HTTPException(status_code=404, detail="报告原文页不存在")
    # **页数未知时只承认第 1 页。** 存下来的 `page_count` 为 NULL 是两种情况之一：
    # 真正读不出页数（本票新增语义），或落在列默认值之前的历史行（旧模型在这里
    # 也会写出 NULL，读取时同样是未知）。两者都只能确定「首页在」，所以既不把
    # 任意页当合法，也不因为未知就整份报告打不开。
    #
    # 注意这个 1 是**校验边界**，不是对外发布的页数：响应里的 `page_count` 仍然是
    # `None`（未知），前端据此隐藏翻页器。把两者混起来，就是本票要修的那个 bug。
    boundary = source.page_count if source.page_count is not None else 1
    if page_number > boundary:
        raise HTTPException(status_code=404, detail="报告原文页不存在")
    path = Path(source.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="报告原文文件不存在")
    if source.media_type != KIND_MEDIA_TYPE["pdf"]:
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
