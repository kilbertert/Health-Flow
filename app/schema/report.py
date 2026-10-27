"""Schemas for report parsing and coordinate-aware metric extraction."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schema.evidence import PatientNotices
from app.service.admission_vocabulary import AdmissionReasonLiteral
from app.service.audit_vocabulary import AuditActionLiteral
from app.service.confirmation_vocabulary import (
    DECISIONS_ORDERED,
    REQUEST_DECISIONS_ORDERED,
)
from app.service.interpretation_failure import (
    FAILURE_LITERAL,
    PATIENT_ACTION_LITERAL,
)
from app.service.recommendation_empty_reason import (
    CartLinkReasonLiteral,
    RecommendationReasonLiteral,
)
from app.service.report_status import PATIENT_STATUSES
from app.service.report_subject import SUBJECT_CONSISTENCIES_ORDERED


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
    #
    # `A` 是**分类结果的阳性**（`Detected` / `Positive`，#236）：它不是偏高也不是偏低
    # ——分类结果没有方向，说「H 偏高」会在一个阳性/阴性检验上说假话。它与 `H`/`L` 一样
    # 是「异常」，患者侧显示「异常」并由摘要计数。
    inferred_abnormal_flag: Literal["H", "L", "N", "A"] | None = None
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
    # 词表**派生自**「指标确认决策」的那一份（GLOSSARY.md 的词条）。此前这个 Literal
    # 自己声明了四个名字，而请求那份声明了三个（没有 `pending`）—— 两份声明各写各的。
    confirmation_status: Literal[*DECISIONS_ORDERED] = "pending"
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


# 取货为空的原因。取值与 `app/service/recommendation_empty_reason.py` 的声明**派生自
# 同一处**；`app.service.mall_goods.Reason` 是本模块能产生的那个子集，两侧一致由
# `tests/test_mall_goods.py` 断言。
RecommendationReason = RecommendationReasonLiteral


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
    """加购深链。为空时 `reason` 说明是哪一种空，不合并成一句。

    两个取值各有各的患者动作：「这份报告还没准备好」与「商城暂时跳不过去」是两件事，
    此前前端把两者都压成一句「暂时无法跳转到商城」。
    """

    url: str | None = None
    reason: CartLinkReasonLiteral | None = None


class RecommendationResponse(BaseModel):
    """检测页推荐结果。为空时 `reason` 必须说明是哪一种空。"""

    items: list[RecommendationItem] = Field(default_factory=list)
    reason: RecommendationReason | None = None


class ReportExtractionJobResponse(BaseModel):
    model_config = {"extra": "forbid"}

    status: Literal["queued", "running", "completed", "failed"]
    attempt_count: int = Field(..., ge=0)
    created_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class FailedFile(BaseModel):
    """一个没能解析完的原始文件。**只带文件名，不带错误原文。**

    `errored_text` 曾经是 `["filename: <模型/框架的错误原文>"]` 这样的拼接串，直接
    呈现给患者 —— provider 的自由文本本来就是给排障看的，不是给患者的。文件名是患者
    自己给的，认得出来；错误原文不是。
    """

    file_index: int = Field(..., ge=1)
    filename: str


class InterpretationFailureReport(BaseModel):
    """报告级的解读失败块（GLOSSARY.md 的「解读失败」）。成因的**唯一**出域形状。

    它取代了三处半成品：`processing_error`（自由文本）、`processing_warnings`
    （`["文件名: 错误"]` 自由文本数组）、`extraction_job.error_class`（Python 类名 /
    策略标签，出域无人读）。那三处互相矛盾的方式是「同一件事有三个名字」，而**成因
    与可重试性根本不在契约里**，所以患者侧无从复述。

    `retryable` 与 `action` 都是 `cause` 的**派生读法**，不是各自独立的事实 —— 服务端
    算好一起送出去，省得前端再按成因表推一遍（推的规则只有一处，这一条与
    `AdmissionLedger` 把四类之和一起送出去同一个理由）。

    **`failed_files` 里只有文件。** 「这次失败是谁的问题」由 `cause` 回答；把每个文件的
    错误原文再出一遍是第二个入口，而第二个入口就是今天三个名字的来源。
    """

    cause: FAILURE_LITERAL
    #: 这条命还有没有救。可重试的两个成因是「服务端当时不行」与「模型当时不可用」。
    retryable: bool
    #: 患者现在该做什么。只有两个取值，且只有两个成因会请患者动手。
    action: PATIENT_ACTION_LITERAL
    #: 没能解析完的文件（文件名，不带错误原文）。可以为空 —— 一次整份材料都读不出来
    #: 的失败没有「某个文件」可指；这时患者侧的话由 `cause` 独自支撑。
    failed_files: list[FailedFile] = Field(default_factory=list)


class AdmissionLedger(BaseModel):
    """报告级的准入台账：四类相加 == 已解析行数。

    它存在的理由是**逐行那个 `None` 兼了两件事**：既有「成功进入解读」，也有「这份
    报告还没跑过评估」。前端要区分这两者才能说对的话 —— 而它不该靠「有没有评估时间」
    之类的旁证去推断，那是同一个概念的第二处判定。

    所以台账是**唯一**报告级答案：`assessed_at` 为空时它没有意义，前端据此显示「还没
    有准入结论」，而不是硬说「全部进入了」。
    """

    included: int = 0
    # 「判定过，在参考区间内」—— **正常**，不是「没能进入解读」。单列是为了让患者侧的
    # 「有 N 项未进入解读」不把每一条正常指标算进去（那正是本次改动要消灭的矛盾）。
    normal: int = 0
    skipped: int = 0
    unmatched: int = 0
    not_evaluated: int = 0
    # 四类之和。服务端算好一起送出去，省得前端再求和一次（求和规则也只有一处）。
    total: int = 0


class ReportAuditEventRecord(BaseModel):
    """一条审计事件（GLOSSARY.md 的「审计事件」）。

    它此前是 `list[dict[str, Any]]`：`action` 没有类型、没有约束，而**全部 action 名只
    出现在 `report_status.transition` 的 docstring 里** —— 拼错一个名字不会有任何东西
    发现。现在 `action` 派生自 `app/service/audit_vocabulary.py` 的那一份声明。

    **`detail` 刻意保持自由形状。** 每条事件要带的明细本来就不同（抽取轨迹、确认过的
    指标 id、任务 id），给它一个统一模型只会逼出一堆全可选的字段 —— 那是把「每条自己
    的形状」换成「一个谁都不完整的形状」。类型化的收益在 action 上，因为 action 是
    被**判读**的那个字段，而 detail 是被**查证**的。
    """

    action: AuditActionLiteral
    actor: str
    correlation_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ReportMetricCounts(BaseModel):
    """一份报告的四个患者可见计数（GLOSSARY.md 的「报告指标计数」）。

    它此前由**三处各自计算**：历史摘要数 `{H,L,A}`、报告单自己再数一遍（还先剔掉
    `excluded` 行）、确认页数的是另一个把「待核对」也算进去的谓词。于是同一份报告在报告单
    上说「未见异常指标」并给出「当前报告的指标均在参考区间内」，在确认页上却在数
    「其中 N 项需要确认」。

    `undecided` 是**结论句的前提**：有它的时候不许宣告「均在参考区间内」——那些行是
    **没判出来**，不是「在区间内」。`needs_review` 与它刻意分开：一行 `two_values`
    同时落进两桶，而一行「模型没标异常、也没有参考范围」只落进 `undecided`。
    """

    #: 已解析行数 —— 另外三个数的分母。
    parsed: int = 0
    #: 判定为异常的行数（`H`/`L`/`A`）。
    abnormal: int = 0
    #: 需要患者处理的行数（异常，或判定不出来但模型宣称异常）。
    needs_review: int = 0
    #: 还没有判定的行数（判不出来、且患者没有排除）。结论句以它为前提。
    undecided: int = 0
    #: 判定在参考范围内的行数（`N`，且患者没有排除）。患者摘要上「需关注 / 待核对 /
    #: 范围内」的第三个数。与 `abnormal`、`undecided` 是同一份判定的三个互斥出口。
    in_range: int = 0


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
    status: Literal[*PATIENT_STATUSES] = "pending_confirmation"
    subject_consistency: Literal[*SUBJECT_CONSISTENCIES_ORDERED] | None = None
    # 报告级的准入台账（GLOSSARY.md 的「解读准入」）：每一行的结论恰好归一类，
    # 四类相加 == 已解析行数。`included` 是**成功进入解读**的行数 —— 这是逐行的
    # `admission_reason` 那个 `None` 独自表达不了的那一半（它还要兼表达「本报告
    # 还没跑过评估」）。
    #
    # **为空表示这份报告还没有准入结论**（状态不是 `assessed`）。不用一份全 0 的
    # 台账表达那件事：全 0 与「没有一行被拦下」在数值上一样，靠旁证去区分等于给
    # 同一个问题留第二个答案 —— 报告状态才是那个答案的唯一来源。
    admission: "AdmissionLedger | None" = None
    # 报告指标计数（GLOSSARY.md 的「报告指标计数」）。报告单与确认页的**数字**读它，
    # 不再自己数一遍 —— 它们此前各用一个谓词，所以同一份报告能同时说「未见异常指标」
    # 与「其中 N 项需要确认」。
    counts: ReportMetricCounts = Field(default_factory=ReportMetricCounts)
    # 患者可见的健康风险提示（唯一出域的形状）。内部事实层不出域。
    #
    # 历史行存的是旧形状（含 `schema_version` / `patient_reply` / 内部层
    # `findings`）—— 那是**已写入库的数据**，读路径必须能读。所以这个字段按
    # 「新形状，或旧形状的原始 dump」两者都收，由 `app/api/report.py` 负责
    # 把旧行归一成新形状后再出域（见 `_patient_notices_from_stored`）。
    evidence_result: PatientNotices | dict | None = None
    # 报告级的解读失败块（GLOSSARY.md 的「解读失败」）。**为空表示这份报告没有失败**
    # —— 它不是「有失败但说不出成因」：词表是封闭的，服务端每次失败都点名一个成因。
    #
    # 它取代了 `processing_error` / `processing_warnings` / `extraction_job.error_class`
    # 三处半成品。历史行里没有它，读路径按「空即为没有失败」处理（见
    # `app/api/report.py` 的组装）。
    interpretation_failure: InterpretationFailureReport | None = None
    extraction_job: ReportExtractionJobResponse | None = None
    extraction_trace: dict[str, Any] | None = None
    # 这份报告的审计流，按时间序**原样**透出（`app/api` 不得筛、不得挑 —— 见
    # `tests/test_report_status_vocabulary.py` 的守卫）。逐条目是带类型的记录，
    # action 取自唯一的词表。
    audit_events: list[ReportAuditEventRecord] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MetricConfirmation(BaseModel):
    metric_id: int = Field(..., ge=1)
    # 派生自同一份词表。**包含** `pending` —— 它的含义是「这条我还没动」，是一个诚实的
    # 表态（客户端必须提交全部已解析指标），由服务端解成该行已落定的状态。见
    # `app/service/confirmation_decision.py` 的 `request_decision`。
    decision: Literal[*REQUEST_DECISIONS_ORDERED]
    metric_code: str | None = None
    value: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    evidence_text: str | None = None


class ReportConfirmationRequest(BaseModel):
    observations: list[MetricConfirmation] = Field(default_factory=list)
    subject_consistency: Literal[*SUBJECT_CONSISTENCIES_ORDERED] | None = None
