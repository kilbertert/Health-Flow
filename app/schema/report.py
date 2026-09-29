"""Schemas for report parsing and coordinate-aware metric extraction."""

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schema.evidence import EvidenceMatchResponse


class MetricRecord(BaseModel):
    id: int | None = None
    report_id: int | None = None
    metric_name: str = Field(..., min_length=1)
    metric_value: str
    unit: str | None = None
    reference_range: str | None = None
    trend: str | None = None
    abnormal_flag: str | None = None
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
    status: str = "pending_confirmation"
    subject_consistency: str | None = None
    evidence_result: EvidenceMatchResponse | None = None
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
