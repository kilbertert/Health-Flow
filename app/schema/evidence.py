"""Validated published-evidence response contract."""

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricCatalogItem(StrictModel):
    code: str = Field(min_length=1)
    label: str = Field(min_length=1)


class SourceObservation(StrictModel):
    observation_id: str
    metric_code: str | None = None
    metric_label: str | None = None
    value: float
    unit: str
    reference_low: float | None = None
    reference_high: float | None = None
    evidence_text: str
    source_file_index: int = Field(ge=1)
    source_page: int = Field(ge=1)
    source_id: str | None = None
    source_url: str | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    bbox_normalized: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_bbox(self) -> "SourceObservation":
        if self.bbox is not None:
            if any(not math.isfinite(coordinate) or coordinate < 0 for coordinate in self.bbox):
                raise ValueError("bbox coordinates are invalid")
            if self.bbox[0] > self.bbox[2] or self.bbox[1] > self.bbox[3]:
                raise ValueError("bbox must be ordered as x1,y1,x2,y2")
        if self.bbox_normalized is not None:
            if any(not math.isfinite(coordinate) or not 0 <= coordinate <= 1000 for coordinate in self.bbox_normalized):
                raise ValueError("bbox_normalized coordinates are invalid")
            if self.bbox_normalized[0] > self.bbox_normalized[2] or self.bbox_normalized[1] > self.bbox_normalized[3]:
                raise ValueError("bbox_normalized must be ordered as x1,y1,x2,y2")
        return self


class ClaimSource(StrictModel):
    claim_id: str
    paper_id: str
    paper_title: str
    doi: str | None = None
    evidence: str
    locator: str


class PublishedCard(StrictModel):
    id: str
    condition_code: str
    scope_key: str
    version: str
    status: Literal["published"]
    grade: Literal["high", "moderate", "low", "very_low"]
    published_at: datetime
    evidence_profile_id: str
    patient_visible_body: str
    sources: list[ClaimSource] = Field(min_length=1)
    # Defaults keep older test fixtures and cached responses readable.
    content_layer: Literal["context_only"] = "context_only"
    action_status: Literal["not_available"] = "not_available"
    action_message: str = ""


class EvidenceItem(StrictModel):
    """One metric-level published card and its independent report trace."""

    metric_code: str
    metric_label: str
    card: PublishedCard
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    source_observation_ids: list[str] = Field(min_length=1)
    source_observations: list[SourceObservation] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope(self) -> "EvidenceItem":
        if self.card.scope_key != f"metric:{self.metric_code}":
            raise ValueError("evidence item card scope does not match metric_code")
        return self


class Sorting(StrictModel):
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low", "mixed"]
    needs_recheck: bool
    department: str
    epidemiology_background: str


class Finding(StrictModel):
    condition_code: str
    condition_name: str
    # Optional for already persisted v2 reports; v3 uses evidence_items.
    card: PublishedCard | None = None
    source_observation_ids: list[str]
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low", "mixed"]
    needs_recheck: bool
    department: str
    recheck_direction: str
    epidemiology_background: str
    source_observations: list[SourceObservation]
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    sorting: Sorting
    content_layer: Literal["context_only"] = "context_only"
    action_status: Literal["not_available"] = "not_available"
    action_message: str = ""

    @model_validator(mode="after")
    def require_metric_evidence_for_v3(self) -> "Finding":
        if not self.evidence_items and self.card is None:
            raise ValueError("findings require metric-level evidence_items")
        return self


class Unmatched(StrictModel):
    observation_id: str
    metric_code: str | None = None
    metric_label: str
    condition_codes: list[str]
    condition_names: list[str] = Field(default_factory=list)
    reason: Literal["no_published_knowledge_card", "unknown_metric_code"]
    source_observation: SourceObservation | None = None

    @model_validator(mode="after")
    def align_condition_names(self) -> "Unmatched":
        if self.condition_names and len(self.condition_names) != len(self.condition_codes):
            raise ValueError("condition_names must align with condition_codes")
        return self


class Skipped(StrictModel):
    observation_id: str
    reason: Literal[
        "missing_reference_range",
        "within_reference_range",
        "missing_source_evidence",
        "missing_source_page",
        "missing_unit",
        "invalid_value",
        "unknown_metric_code",
    ]


class PatientFinding(StrictModel):
    condition_code: str
    condition_name: str
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low", "mixed"]
    needs_recheck: bool
    department: str
    recheck_direction: str
    # Deprecated v2 single-card fields. New replies use evidence_items.
    card_id: str | None = None
    card_version: str | None = None
    evidence_profile_id: str | None = None
    patient_visible_body: str = ""
    sources: list[ClaimSource] = Field(default_factory=list)
    source_observation_ids: list[str]
    source_observations: list[SourceObservation]
    content_layer: Literal["context_only"] = "context_only"
    action_status: Literal["not_available"] = "not_available"
    action_message: str = ""
    evidence_items: list[EvidenceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_metric_evidence_for_v3(self) -> "PatientFinding":
        if not self.evidence_items and self.card_id is None:
            raise ValueError("patient findings require metric-level evidence_items")
        return self


class PatientReply(StrictModel):
    title: Literal["体检报告解读与健康风险提示"]
    summary: str
    findings: list[PatientFinding]
    unmatched_count: int = Field(ge=0)
    disclaimer: str


class PatientNotices(StrictModel):
    """患者可见的健康风险提示（GLOSSARY.md 的「健康风险提示」）。

    这是**唯一**出域给浏览器的形状。内部事实层（`Finding` 的 `sorting` /
    `epidemiology_background` / `card.published_at` 等）留在服务端 —— 它此前
    连同患者投影一起经 `evidence_result` 原样透出，客户端再用 `condition_code`
    当场 join 两个数组。

    `summary` 与 `PatientReply.summary` 恒等：投影由一个函数产出，摘要也就只有
    一个来源（此前端点只在 `unmatched` 非空时改写，两处可以各说各话）。
    """

    correlation_id: str
    title: Literal["体检报告解读与健康风险提示"]
    summary: str
    findings: list[PatientFinding]
    unmatched: list[Unmatched]
    skipped: list[Skipped]
    unmatched_count: int = Field(ge=0)
    disclaimer: str


def build_patient_notices(
    result: "EvidenceMatchResponse",
    *,
    unmatched: list[Unmatched] | None = None,
    skipped: list[Skipped] | None = None,
) -> PatientNotices:
    """从证据服务响应构造**唯一的患者可见投影**（GLOSSARY.md 的「健康风险提示」）。

    这是本仓库里唯一允许决定「患者看到什么」的函数：

    - **形状**：只输出 `PatientNotices` —— 内部事实层的 `sorting` /
      `epidemiology_background` / `card.published_at` 等不出域；
    - **摘要**：无条件的单一来源。此前端点在 `unmatched` 非空时才改写
      `message` 与 `patient_reply.summary`，两者可以各说各话；
    - **v2→v3 归一**：v2 单卡（`PatientFinding.card_id`）在这里就转成
      `evidence_items`，不在浏览器渲染函数里做（`Upload.jsx` 曾用已废弃的 v2
      字段现场合成一份 `EvidenceItem`）；
    - **身份**：由 `EvidenceMatchResponse.validate_condition_identity` 保证唯一
      且与内部事实层对应。
    """
    final_unmatched = unmatched if unmatched is not None else result.unmatched
    final_skipped = skipped if skipped is not None else result.skipped
    finding_count = len(result.findings)
    unmatched_count = len(final_unmatched)
    if finding_count and unmatched_count:
        summary = (
            f"发现 {finding_count} 个可能相关健康问题；"
            f"另有 {unmatched_count} 条指标与健康问题关联暂无已审核知识卡。"
        )
    elif finding_count:
        summary = f"发现 {finding_count} 个可能相关健康问题。"
    elif unmatched_count:
        summary = f"发现 {unmatched_count} 个异常指标，但暂无已审核内容。"
    elif final_skipped:
        # `skipped` 里的原因（证据不足、值不可解析、缺参考范围）**不代表指标
        # 在参考区间内** —— 那只是「这次没能判定」。宣告「均在参考区间内」是
        # 替患者下一个服务端给不出的结论（评审在 #161 指出）。
        summary = f"本次报告有 {len(final_skipped)} 项指标未能完成解读，其余指标未见需要关注的问题。"
    else:
        summary = "本次报告的指标均在参考区间内，未见需要关注的问题。"

    # 以 `patient_reply.findings` 为准：那才是证据服务选定的**患者可见集合**。
    # 内部事实层可以比它多（`validate_condition_identity` 只要求投影 ⊆ 内部层），
    # 拿内部层构造会把未被选入投影的问题也展示给患者（评审在 #161 指出）。
    internal_by_code = {finding.condition_code: finding for finding in result.findings}
    findings = [
        _patient_finding(internal_by_code.get(projected.condition_code), projected)
        for projected in result.patient_reply.findings
    ]
    return PatientNotices(
        correlation_id=result.correlation_id,
        title=result.patient_reply.title,
        summary=summary,
        findings=findings,
        unmatched=final_unmatched,
        skipped=final_skipped,
        unmatched_count=unmatched_count,
        disclaimer=result.patient_reply.disclaimer,
    )


def _patient_finding(internal: "Finding | None", projected: "PatientFinding") -> PatientFinding:
    """内部事实层 → 患者可见层。**v2→v3 的归一只在这里做一次。**

    此前这一步在浏览器的渲染函数里（`Upload.jsx` 的 `evidenceItemsFor`）：两个
    形状都没有 `evidence_items` 时，用已废弃的 v2 单卡字段现场合成一个
    `EvidenceItem`。契约版本迁移写在 React 里，就是「契约没有家」。

    v3 直接搬运 `evidence_items`；v2（只有 `card` 与 deprecated 字段）在服务端
    转成 v3 形状。v2 且没有来源观测时保留 v2 形态（`card_id` 等），
    让 `PatientFinding` 自己的 v2 分支接住 —— 不伪造一条不存在的观测。
    """
    # 投影已有的字段优先（证据服务选定的说法），缺的内部层补。
    common = {
        "condition_code": projected.condition_code,
        "condition_name": projected.condition_name,
        "urgency": projected.urgency,
        "abnormality_severity": projected.abnormality_severity,
        "evidence_strength": projected.evidence_strength,
        "needs_recheck": projected.needs_recheck,
        "department": projected.department,
        "recheck_direction": projected.recheck_direction,
        "source_observation_ids": projected.source_observation_ids,
        "source_observations": projected.source_observations,
    }
    if projected.evidence_items:
        return PatientFinding(**common, evidence_items=projected.evidence_items)
    if internal is None or internal.card is None:
        return PatientFinding(**common, evidence_items=[])
    finding = internal
    if not finding.source_observations:
        # v2 且没有来源观测：保留 v2 形态，不伪造观测。
        return PatientFinding(
            **common,
            card_id=finding.card.id,
            card_version=finding.card.version,
            evidence_profile_id=finding.card.evidence_profile_id,
            patient_visible_body=finding.card.patient_visible_body,
            sources=finding.card.sources,
        )
    metric_code = (finding.card.scope_key or "metric:").split(":", 1)[1]
    return PatientFinding(
        **common,
        evidence_items=[
            EvidenceItem(
                metric_code=metric_code,
                metric_label=finding.condition_name,
                card=finding.card,
                evidence_strength=(
                    finding.evidence_strength
                    if finding.evidence_strength in {"high", "moderate", "low", "very_low"}
                    else "moderate"
                ),
                source_observation_ids=finding.source_observation_ids,
                source_observations=finding.source_observations,
            )
        ],
    )


class EvidenceMatchResponse(StrictModel):
    schema_version: Literal["2", "3"]
    sorting_version: Literal["published-card-reference-range-v1"]
    correlation_id: str
    findings: list[Finding]
    unmatched: list[Unmatched]
    skipped: list[Skipped]
    message: str
    patient_reply: PatientReply

    @model_validator(mode="after")
    def validate_version_shape(self) -> "EvidenceMatchResponse":
        if self.schema_version == "3":
            if any(not finding.evidence_items for finding in self.findings):
                raise ValueError("schema v3 findings require metric-level evidence_items")
            if any(not finding.evidence_items for finding in self.patient_reply.findings):
                raise ValueError("schema v3 patient findings require evidence_items")
        return self

    @model_validator(mode="after")
    def validate_condition_identity(self) -> "EvidenceMatchResponse":
        """健康问题的身份在投影内唯一，且与内部事实层一一对应。

        此前没有任何校验：客户端拿 `condition_code` 当 join key 用一个 `Map`
        合并两个数组，重复时**静默折叠最后一条**（两张卡片共用它的证据与推荐）。
        本仓库对「指标↔卡」的一致性早就有先例（`EvidenceItem.validate_scope`），
        条件码的一致性一直没有。

        校验失败走调用方既有的「证据服务返回格式无效」路径，而不是让客户端
        猜。内部事实层的每个健康问题**必须**在投影里出现且只出现一次 ——
        投影是 `Finding` 的患者可见形态，不是它的子集。
        """
        internal = [finding.condition_code for finding in self.findings]
        if len(set(internal)) != len(internal):
            raise ValueError("findings contain duplicate condition_code")
        projected = [finding.condition_code for finding in self.patient_reply.findings]
        if len(set(projected)) != len(projected):
            raise ValueError("patient_reply.findings contain duplicate condition_code")
        # 投影里的每个健康问题都必须在内部事实层里有对应项 —— 否则客户端拿着
        # 一个 join 不到的 key。**不要求反向**：内部层可以比投影多（投影是
        # 患者可见的部分），且这是跨仓契约（证据服务在另一个仓库），
        # 不该由消费方单方面要求两侧集合相等。
        if not set(projected) <= set(internal):
            raise ValueError("patient_reply.findings must correspond to findings")
        for finding in self.findings:
            for item in finding.evidence_items:
                if item.card.condition_code != finding.condition_code:
                    raise ValueError("evidence item card condition does not match its finding")
        return self
