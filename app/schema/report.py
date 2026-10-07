"""Schemas for report parsing and coordinate-aware metric extraction."""

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schema.evidence import PatientNotices


class MetricRecord(BaseModel):
    id: int | None = None
    report_id: int | None = None
    metric_name: str = Field(..., min_length=1)
    metric_value: str
    unit: str | None = None
    reference_range: str | None = None
    trend: str | None = None
    abnormal_flag: str | None = None
    # 服务端计算的「当前生效判定」（异常判定）：确认值/确认参考范围优先，否则模型值/
    # 模型参考范围；无法判定为 None，不猜测。它是患者侧异常标记与历史摘要的唯一口径，
    # 覆盖抽取模型写下的原始 abnormal_flag。定义见 GLOSSARY.md 的「异常判定」。
    inferred_abnormal_flag: Literal["H", "L", "N"] | None = None
    bbox: list[float] | None = Field(None, min_length=4, max_length=4)
    bbox_normalized: list[float] | None = Field(None, min_length=4, max_length=4)
    source_file_index: int = Field(default=1, ge=1)
    page_number: int | None = Field(None, ge=1)
    evidence_text: str | None = None
    source_id: str | None = None
    metric_code: str | None = None
    confirmation_status: Literal["pending", "confirmed", "corrected", "excluded"] = "pending"
    confirmed_value: str | None = None
    confirmed_unit: str | None = None
    confirmed_reference_range: str | None = None
    confirmed_evidence_text: str | None = None
    # 当前生效值四元组（GLOSSARY.md 的「指标生效值」）：修正值 > 确认值 > 模型值；
    # excluded 的指标没有生效值（四项皆 None）。定义见 app/service/metric_effective_value.py，
    # 与证据边界用的是同一个函数、同一份输入。
    effective_value: str | None = None
    effective_unit: str | None = None
    effective_reference_range: str | None = None
    effective_evidence_text: str | None = None

    @model_validator(mode="after")
    def validate_bboxes(self) -> "MetricRecord":
        for name, box in (
            ("bbox", self.bbox),
            ("bbox_normalized", self.bbox_normalized),
        ):
            if box is None:
                continue
            upper = 1000 if name == "bbox_normalized" else None
            if any(
                not math.isfinite(coordinate) or coordinate < 0 or (upper is not None and coordinate > upper)
                for coordinate in box
            ):
                raise ValueError(f"{name} coordinates are invalid")
            if box[0] > box[2] or box[1] > box[3]:
                raise ValueError(f"{name} must be ordered as x1,y1,x2,y2")
        return self


class MedicalReportCreate(BaseModel):
    patient_id: str
    report_type: str | None = None
    file_url: str | None = None
    parsed_content: dict | None = None
    exam_date: datetime | None = None
    department: str | None = None
    metrics: list[MetricRecord] = Field(default_factory=list)


class MedicalReport(MedicalReportCreate):
    id: int
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = {"from_attributes": True}


class ReportFileRecord(BaseModel):
    file_index: int
    original_filename: str
    media_type: str
    page_count: int
    source_url: str


# 推荐为空的原因。**只有三种**，且与 `app.service.mall_goods.Reason` 是同一条约定：
# `no_published_card`（本报告没有可据以取货的健康风险，不调商城）、
# `no_label_data`（商城调用成功但按标签交集为空）、
# `mall_unavailable`（不可达/超时/业务错误/凭据未配置）。命中时为空值。
# 这条约定有测试钉住两侧一致（`tests/test_mall_goods.py`），别再各写一份。
RecommendationReason = Literal["no_published_card", "no_label_data", "mall_unavailable"]


class RecommendationItem(BaseModel):
    """检测页商品卡片。字段是商城端点返回的展示子集，不含内部定价面。"""

    id: str
    name: str
    image: str | None = None
    # 价格保持十进制字符串：商城返回的是 BigDecimal，转成二进制浮点会抖动。
    price_down: str | None = None
    price_up: str | None = None
    # null 表示商城未标注库存，**不等同于 0**；前端据此显示"未标注"。
    stock: int | None = None
    shop_id: str | None = None


class CartLinkResponse(BaseModel):
    """加购深链。为空时 `reason` 说明是哪一种空，不合并成一句。"""

    url: str | None = None
    reason: Literal["report_not_ready", "link_unavailable"] | None = None


class RecommendationResponse(BaseModel):
    """检测页推荐结果。为空时 `reason` 必须说明是哪一种空。"""

    items: list[RecommendationItem] = Field(default_factory=list)
    reason: RecommendationReason | None = None


class ReportExtractionJobResponse(BaseModel):
    model_config = {"extra": "forbid"}

    status: Literal["queued", "running", "completed", "failed"]
    attempt_count: int = Field(..., ge=0)
    error_class: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class MedicalReportResponse(BaseModel):
    id: int
    patient_id: str
    # Server-computed: whether this report belongs to the authenticated account.
    # The client used to re-derive this by comparing `patient_id` against the
    # account id, which silently reported "not this account" whenever an old
    # client supplied its own label. Ownership is a server-side answer.
    owned_by_account: bool = False
    report_type: str | None = None
    exam_date: datetime | None = None
    department: str | None = None
    metrics: list[MetricRecord]
    files: list[ReportFileRecord] = Field(default_factory=list)
    created_at: datetime
    # 患者可见的报告状态（GLOSSARY.md 的「报告状态」）。唯一迁移入口见
    # app/service/report_status.py；数据库列仍是 String（兼容既有行），
    # 但这个契约只允许这五个值。
    status: Literal["processing", "pending_confirmation", "confirmed", "assessed", "failed"] = (
        "pending_confirmation"
    )
    subject_consistency: Literal["same", "different", "uncertain"] | None = None
    # 患者可见的健康风险提示（唯一出域的形状）。内部事实层不出域。
    evidence_result: PatientNotices | None = None
    processing_error: str | None = None
    processing_warnings: list[str] = Field(default_factory=list)
    extraction_job: ReportExtractionJobResponse | None = None
    extraction_trace: dict[str, Any] | None = None
    audit_events: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MetricConfirmation(BaseModel):
    metric_id: int = Field(..., ge=1)
    decision: Literal["confirmed", "corrected", "excluded"]
    metric_code: str | None = None
    value: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    evidence_text: str | None = None


class ReportConfirmationRequest(BaseModel):
    observations: list[MetricConfirmation] = Field(default_factory=list)
    subject_consistency: Literal["same", "different", "uncertain"] | None = None
