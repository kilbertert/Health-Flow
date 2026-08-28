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
    confirmation_status: Literal["pending", "confirmed", "corrected", "excluded"] = (
        "pending"
    )
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
                not math.isfinite(coordinate)
                or coordinate < 0
                or (upper is not None and coordinate > upper)
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
    access_token: str | None = None
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
