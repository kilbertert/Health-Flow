"""Schema definitions for API request/response."""

from app.schema.auth import (
    AccountResponse,
    LoginRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    ReportHistoryItem,
)
from app.schema.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatStreamRequest,
    FeedbackInfo,
    IntentDistribution,
    ReferenceItem,
    RoutingRequest,
    RoutingResponse,
    SafetyCheckResult,
)
from app.schema.report import (
    MedicalReport,
    MedicalReportCreate,
    MedicalReportResponse,
    MetricConfirmation,
    MetricRecord,
    ReportConfirmationRequest,
)
from app.schema.train import (
    DataAugmentRequest,
    DataAugmentResponse,
    DPORequest,
    DPOResponse,
    FinetuneRequest,
    FinetuneResponse,
)

__all__ = [
    "MetricRecord",
    "MedicalReportCreate",
    "MedicalReport",
    "MedicalReportResponse",
    "MetricConfirmation",
    "ReportConfirmationRequest",
    "AccountResponse",
    "LoginRequest",
    "ProfileUpdateRequest",
    "RegisterRequest",
    "ReportHistoryItem",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "IntentDistribution",
    "ReferenceItem",
    "SafetyCheckResult",
    "FeedbackInfo",
    "ChatStreamRequest",
    "RoutingRequest",
    "RoutingResponse",
    "DataAugmentRequest",
    "DataAugmentResponse",
    "FinetuneRequest",
    "FinetuneResponse",
    "DPORequest",
    "DPOResponse",
]
