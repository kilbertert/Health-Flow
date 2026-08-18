"""Schemas for report parsing and coordinate-aware metric extraction."""

import math
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.schema.evidence import EvidenceMatchResponse


class MetricRecord(BaseModel):
    id: Optional[int] = None
    report_id: Optional[int] = None
    metric_name: str = Field(..., min_length=1)
    metric_value: str
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    trend: Optional[str] = None
    abnormal_flag: Optional[str] = None
    bbox: Optional[List[float]] = Field(None, min_length=4, max_length=4)
    bbox_normalized: Optional[List[float]] = Field(None, min_length=4, max_length=4)
    source_file_index: int = Field(default=1, ge=1)
    page_number: Optional[int] = Field(None, ge=1)
    evidence_text: Optional[str] = None
    source_id: Optional[str] = None
    metric_code: Optional[str] = None
    confirmation_status: Literal["pending", "confirmed", "corrected", "excluded"] = (
        "pending"
    )
    confirmed_value: Optional[str] = None
    confirmed_unit: Optional[str] = None
    confirmed_reference_range: Optional[str] = None

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
    report_type: Optional[str] = None
    file_url: Optional[str] = None
    parsed_content: Optional[dict] = None
    exam_date: Optional[datetime] = None
    department: Optional[str] = None
    metrics: List[MetricRecord] = Field(default_factory=list)


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


class MedicalReportResponse(BaseModel):
    id: int
    patient_id: str
    report_type: Optional[str] = None
    exam_date: Optional[datetime] = None
    department: Optional[str] = None
    metrics: List[MetricRecord]
    files: List[ReportFileRecord] = Field(default_factory=list)
    created_at: datetime
    status: str = "pending_confirmation"
    subject_consistency: Optional[str] = None
    evidence_result: Optional[EvidenceMatchResponse] = None
    processing_error: Optional[str] = None
    access_token: Optional[str] = None

    model_config = {"from_attributes": True}


class MetricConfirmation(BaseModel):
    metric_id: int = Field(..., ge=1)
    decision: Literal["confirmed", "corrected", "excluded"]
    metric_code: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None


class ReportConfirmationRequest(BaseModel):
    observations: List[MetricConfirmation] = Field(default_factory=list)
    subject_consistency: Literal["same", "different", "uncertain"] | None = None
