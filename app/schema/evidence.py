"""Validated Evidence API v1 response contract."""

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
    metric_code: str
    value: float
    unit: str
    reference_low: float | None = None
    reference_high: float | None = None
    evidence_text: str
    source_file_index: int = Field(ge=1)
    source_page: int = Field(ge=1)
    source_id: str | None = None
    bbox_normalized: list[float] | None = Field(
        default=None, min_length=4, max_length=4
    )

    @model_validator(mode="after")
    def validate_bbox(self) -> "SourceObservation":
        if self.bbox_normalized is not None:
            if any(
                not math.isfinite(coordinate) or not 0 <= coordinate <= 1000
                for coordinate in self.bbox_normalized
            ):
                raise ValueError("bbox_normalized coordinates are invalid")
            if (
                self.bbox_normalized[0] > self.bbox_normalized[2]
                or self.bbox_normalized[1] > self.bbox_normalized[3]
            ):
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


class Sorting(StrictModel):
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    epidemiology_background: str


class Finding(StrictModel):
    condition_code: str
    condition_name: str
    card: PublishedCard
    source_observation_ids: list[str]
    urgency: Literal["routine", "soon", "urgent", "emergency"]
    abnormality_severity: int = Field(ge=0, le=3)
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    recheck_direction: str
    epidemiology_background: str
    source_observations: list[SourceObservation]
    sorting: Sorting


class Unmatched(StrictModel):
    observation_id: str
    metric_code: str
    metric_label: str
    condition_codes: list[str]
    reason: Literal["no_published_knowledge_card"]


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
    evidence_strength: Literal["high", "moderate", "low", "very_low"]
    needs_recheck: bool
    department: str
    recheck_direction: str
    card_id: str
    card_version: str
    evidence_profile_id: str
    patient_visible_body: str
    sources: list[ClaimSource] = Field(min_length=1)
    source_observation_ids: list[str]
    source_observations: list[SourceObservation]


class PatientReply(StrictModel):
    title: Literal["体检报告解读与健康风险提示"]
    summary: str
    findings: list[PatientFinding]
    unmatched_count: int = Field(ge=0)
    disclaimer: str


class EvidenceMatchResponse(StrictModel):
    schema_version: Literal["2"]
    sorting_version: Literal["published-card-reference-range-v1"]
    correlation_id: str
    findings: list[Finding]
    unmatched: list[Unmatched]
    skipped: list[Skipped]
    message: str
    patient_reply: PatientReply
