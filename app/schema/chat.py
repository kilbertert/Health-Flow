"""Schemas used by chat, routing and safety APIs."""

from datetime import datetime

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., description="user/assistant/system")
    content: str
    timestamp: datetime | None = Field(default_factory=datetime.now)
    referenced_metrics: list[str] = Field(default_factory=list)
    safety_check_result: str | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(..., min_length=1, max_length=4000)
    patient_id: str | None = None
    include_history: bool = True


class IntentDistribution(BaseModel):
    endocrinology: float = Field(0.0, ge=0, le=1)
    cardiology: float = Field(0.0, ge=0, le=1)
    gastroenterology: float = Field(0.0, ge=0, le=1)
    respiratory: float = Field(0.0, ge=0, le=1)
    general: float = Field(0.0, ge=0, le=1)

    def get_routed_department(self) -> str:
        return max(self.model_dump(), key=self.model_dump().get)


class ReferenceItem(BaseModel):
    type: str = Field(..., description="evidence type: vector, graph or report")
    content: str
    source_id: str | None = None
    score: float | None = None
    path: list[str] = Field(default_factory=list)


class SafetyCheckResult(BaseModel):
    passed: bool
    warnings: list[str] = Field(default_factory=list)
    # 保持纯英文字段名：FastAPI 默认按别名序列化，中文别名会导致
    # 前端拿到「红旗标记」而不是 red_flag，各接口返回不一致。
    red_flag: bool = False
    critical: bool = False


class FeedbackInfo(BaseModel):
    recursion_depth: int = Field(0, ge=0, le=5)
    consistency_check: str = "PASS"
    evidence_score: float | None = Field(None, ge=0, le=1)
    contradictions: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    department: str
    agent_used: str
    # 服务端分配的会话 ID（sess_<int>），客户端应保存并在后续轮次回传
    session_id: str | None = None
    intent_distribution: dict[str, float] | None = None
    referenced_metrics: list[str] = Field(default_factory=list)
    references: list[ReferenceItem] = Field(default_factory=list)
    safety_check: SafetyCheckResult
    feedback_info: FeedbackInfo


class ChatStreamRequest(ChatRequest):
    pass


class RoutingRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    patient_id: str | None = None


class RoutingResponse(BaseModel):
    routed_department: str
    intent_distribution: dict[str, float]
    confidence: float = Field(..., ge=0, le=1)
    reasoning: str
    low_confidence: bool = False
    human_review_required: bool = False
