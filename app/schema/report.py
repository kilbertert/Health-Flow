"""Schemas for report parsing and coordinate-aware metric extraction."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schema.evidence import PatientNotices
from app.service.admission_vocabulary import AdmissionReasonLiteral


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
    # 服务端给出的「解读准入」结论（GLOSSARY.md 的「解读准入」）：`None` 表示这一行
    # **进入了解读**，否则是它没进去的**唯一**原因。值集与词表同源（词表在
    # `app/service/admission_vocabulary.py`），所以患者侧不需要、也不允许自己推导 ——
    # 此前「待核对 / 已排除」是前端从原始 `confirmation_status` 猜的，「值用不了」是
    # 前端拿 `inferred_abnormal_flag === null` 当代理猜的，两种猜法都把不同的事实
    # 压成同一句话。
    #
    # `None` 是**两件事共用的一个值**：还没跑过评估的 `pending_confirmation` 报告
    # （没有任何结论可言）与评估过后**成功进入解读**的行。所以它只在这份逐行契约里
    # 表示「服务端没有拦下这一行」；报告级台账由 `MedicalReportResponse.admission`
    # 给出，两者不是同一个概念，不要互相推导。
    admission_reason: AdmissionReasonLiteral | None = None
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
        """坐标规则在 `app/service/origin_location.py`（唯一一处）。

        这里是**创建边界**：使用 `strict=True`，退化框（零面积）被拒绝 ——
        它指不到任何东西。此前这一层只查 `>`，于是退化的 `[100,100,100,100]`
        在抽取层被拒绝、在这里被**接受**（#101 的四种实现、三种裁决）。
        """
        # 延迟导入：`app.service.__init__` 会拉起 vision_encoder，而它反过来
        # import 本模块 —— 在模块级导入会成环。校验器里导入没有这个问题。
        from app.service.origin_location import bbox_issue

        for name, box in (
            ("bbox", self.bbox),
            ("bbox_normalized", self.bbox_normalized),
        ):
            if box is None:
                continue  # 定位为空是合法状态（任一组件缺失即为空，不猜测）
            issue = bbox_issue(box, upper=1000 if name == "bbox_normalized" else None, strict=True)
            if issue is not None:
                raise ValueError(f"{name} coordinates are invalid ({issue})")
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
    # **`None` 表示「读不出页数」（未知），不是「共 0 页」。** 它与「共 1 页」是
    # 两件事：损坏的 PDF 此前被说成 1（见 app/service/report_material.py 的
    # `page_count`）。前端据此隐藏翻页器，而不是显示一个假的页数。
    page_count: int | None = None
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


class AdmissionLedger(BaseModel):
    """报告级的准入台账：四类相加 == 已解析行数。

    它存在的理由是**逐行那个 `None` 兼了两件事**：既有「成功进入解读」，也有「这份
    报告还没跑过评估」。前端要区分这两者才能说对的话 —— 而它不该靠「有没有评估时间」
    之类的旁证去推断，那是同一个概念的第二处判定。

    所以台账是**唯一**报告级答案：`assessed_at` 为空时它没有意义，前端据此显示「还没
    有准入结论」，而不是硬说「全部进入了」。
    """

    included: int = 0
    skipped: int = 0
    unmatched: int = 0
    not_evaluated: int = 0
    # 四类之和。服务端算好一起送出去，省得前端再求和一次（求和规则也只有一处）。
    total: int = 0


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
    # 报告级的准入台账（GLOSSARY.md 的「解读准入」）：每一行的结论恰好归一类，
    # 四类相加 == 已解析行数。`included` 是**成功进入解读**的行数 —— 这是逐行的
    # `admission_reason` 那个 `None` 独自表达不了的那一半（它还要兼表达「本报告
    # 还没跑过评估」）。
    #
    # **为空表示这份报告还没有准入结论**（状态不是 `assessed`）。不用一份全 0 的
    # 台账表达那件事：全 0 与「没有一行被拦下」在数值上一样，靠旁证去区分等于给
    # 同一个问题留第二个答案 —— 报告状态才是那个答案的唯一来源。
    admission: "AdmissionLedger | None" = None
    # 患者可见的健康风险提示（唯一出域的形状）。内部事实层不出域。
    #
    # 历史行存的是旧形状（含 `schema_version` / `patient_reply` / 内部层
    # `findings`）—— 那是**已写入库的数据**，读路径必须能读。所以这个字段按
    # 「新形状，或旧形状的原始 dump」两者都收，由 `app/api/report.py` 负责
    # 把旧行归一成新形状后再出域（见 `_patient_notices_from_stored`）。
    evidence_result: PatientNotices | dict | None = None
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
