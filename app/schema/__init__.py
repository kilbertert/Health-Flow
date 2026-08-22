"""Schema definitions for API request/response."""

from app.schema.report import (
    MetricRecord,
    MedicalReportCreate,
    MedicalReport,
    MedicalReportResponse,
    MetricConfirmation,
    ReportConfirmationRequest,
)
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
    IntentDistribution,
    ReferenceItem,
    SafetyCheckResult,
    FeedbackInfo,
    ChatStreamRequest,
    RoutingRequest,
    RoutingResponse,
)
from app.schema.train import (
    DataAugmentRequest,
    DataAugmentResponse,
    FinetuneRequest,
    FinetuneResponse,
    DPORequest,
    DPOResponse,
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
