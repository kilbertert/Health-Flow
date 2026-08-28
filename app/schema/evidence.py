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


class ProductRecommendation(StrictModel):
    recommendation_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    nutrient: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    safety_message: str = Field(min_length=1)
    disclaimer: str = Field(min_length=1)
    image_url: str | None = Field(default=None, pattern=r"^/products/[A-Za-z0-9_-]+\.png$")
    evidence_links: list[str] = Field(min_length=1)
    evidence_strength: Literal["high", "moderate", "low", "very_low", "mixed"]
    priority: int = Field(ge=0)


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
    # Evidence API v2 exposes capability metadata for the next product layer.
    # Defaults keep older test fixtures and cached responses readable.
    content_layer: Literal["context_only"] = "context_only"
    action_status: Literal["not_available"] = "not_available"
    action_message: str = ""
    product_status: Literal["not_implemented", "available"] = "not_implemented"


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
    product_status: Literal["not_implemented", "available"] = "not_implemented"
    recommendations: list[ProductRecommendation] = Field(default_factory=list)
    recommendation_message: str = "暂无推荐"

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
    product_status: Literal["not_implemented", "available"] = "not_implemented"
    recommendations: list[ProductRecommendation] = Field(default_factory=list)
    recommendation_message: str = "暂无推荐"
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
