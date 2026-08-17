"""Schemas for report parsing and coordinate-aware metric extraction."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


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
    confirmation_status: Literal["pending", "confirmed", "corrected", "excluded"] = "pending"
    confirmed_value: Optional[str] = None
    confirmed_unit: Optional[str] = None
    confirmed_reference_range: Optional[str] = None


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

class MedicalReportResponse(BaseModel):
    id: int
    patient_id: str
    report_type: Optional[str] = None
    exam_date: Optional[datetime] = None
    department: Optional[str] = None
    metrics: List[MetricRecord]
    created_at: datetime
    status: str = "pending_confirmation"
    subject_consistency: Optional[str] = None
    evidence_result: Optional[dict] = None
    processing_error: Optional[str] = None

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
