"""Tests for schema validation."""

from app.schema.chat import ChatRequest, ChatResponse
from app.schema.evidence import EvidenceMatchResponse
from app.schema.report import MetricRecord
from app.schema.train import DataAugmentRequest, FinetuneRequest


def test_metric_record_validation():
    """Test MetricRecord validation."""
    metric = MetricRecord(
        metric_name="空腹血糖",
        metric_value="6.5",
        unit="mmol/L",
        reference_range="3.9-6.1",
    )
    assert metric.metric_name == "空腹血糖"
    assert metric.metric_value == "6.5"


def test_metric_record_with_bbox():
    """Test MetricRecord with bbox."""
    metric = MetricRecord(
        metric_name="空腹血糖", metric_value="6.5", bbox=[120.0, 340.0, 280.0, 360.0]
    )
    assert metric.bbox == [120.0, 340.0, 280.0, 360.0]


def test_evidence_v2_capability_fields_are_parsed_in_nested_response():
    payload = {
        "schema_version": "2",
        "sorting_version": "published-card-reference-range-v1",
        "correlation_id": "00000000-0000-0000-0000-000000000001",
        "findings": [
            {
                "condition_code": "COND_DYSLIPIDEMIA",
                "condition_name": "血脂异常",
                "card": {
                    "id": "card-1",
                    "condition_code": "COND_DYSLIPIDEMIA",
                    "scope_key": "metric:ldl_c",
                    "version": "1.0.0",
                    "status": "published",
                    "grade": "moderate",
                    "published_at": "2026-08-19T00:00:00Z",
                    "evidence_profile_id": "profile-1",
                    "patient_visible_body": "正式知识卡内容",
                    "sources": [
                        {
                            "claim_id": "claim-1",
                            "paper_id": "paper-1",
                            "paper_title": "Test paper",
                            "doi": "10.1000/test",
                            "evidence": "Test evidence",
                            "locator": "p. 1",
                        }
                    ],
                    "content_layer": "context_only",
                    "action_status": "not_available",
                    "action_message": "当前证据确定性尚未达到具体行动建议门槛。",
                    "product_status": "not_implemented",
                },
                "source_observation_ids": ["observation-1"],
                "urgency": "routine",
                "abnormality_severity": 1,
                "evidence_strength": "moderate",
                "needs_recheck": True,
                "department": "心血管内科",
                "recheck_direction": "复查血脂",
                "epidemiology_background": "",
                "source_observations": [],
                "sorting": {
                    "urgency": "routine",
                    "abnormality_severity": 1,
                    "evidence_strength": "moderate",
                    "needs_recheck": True,
                    "department": "心血管内科",
                    "epidemiology_background": "",
                },
                "content_layer": "context_only",
                "action_status": "not_available",
                "action_message": "当前证据确定性尚未达到具体行动建议门槛。",
                "product_status": "not_implemented",
            }
        ],
        "unmatched": [],
        "skipped": [],
        "message": "",
        "patient_reply": {
            "title": "体检报告解读与健康风险提示",
            "summary": "",
            "findings": [
                {
                    "condition_code": "COND_DYSLIPIDEMIA",
                    "condition_name": "血脂异常",
                    "urgency": "routine",
                    "abnormality_severity": 1,
                    "evidence_strength": "moderate",
                    "needs_recheck": True,
                    "department": "心血管内科",
                    "recheck_direction": "复查血脂",
                    "card_id": "card-1",
                    "card_version": "1.0.0",
                    "evidence_profile_id": "profile-1",
                    "patient_visible_body": "正式知识卡内容",
                    "sources": [
                        {
                            "claim_id": "claim-1",
                            "paper_id": "paper-1",
                            "paper_title": "Test paper",
                            "doi": "10.1000/test",
                            "evidence": "Test evidence",
                            "locator": "p. 1",
                        }
                    ],
                    "source_observation_ids": ["observation-1"],
                    "source_observations": [],
                    "content_layer": "context_only",
                    "action_status": "not_available",
                    "action_message": "当前证据确定性尚未达到具体行动建议门槛。",
                    "product_status": "not_implemented",
                }
            ],
            "unmatched_count": 0,
            "disclaimer": "仅供健康信息参考。",
        },
    }

    result = EvidenceMatchResponse.model_validate(payload)
    finding = result.findings[0]
    assert finding.card.content_layer == "context_only"
    assert finding.action_status == "not_available"
    assert finding.card.action_message.startswith("当前证据")
    assert result.patient_reply.findings[0].product_status == "not_implemented"


def test_chat_request_validation():
    """Test ChatRequest validation."""
    request = ChatRequest(
        session_id="sess_123",
        message="我空腹血糖有点高",
        patient_id="P001",
        include_history=True,
    )
    assert request.session_id == "sess_123"
    assert request.message == "我空腹血糖有点高"


def test_chat_request_without_optional():
    """Test ChatRequest without optional fields."""
    request = ChatRequest(message="我空腹血糖有点高")
    assert request.session_id is None
    assert request.include_history is True  # default


def test_chat_response_structure():
    """Test ChatResponse has required fields."""
    from app.schema.chat import FeedbackInfo, SafetyCheckResult

    response = ChatResponse(
        reply="这是一条回复",
        department="内分泌科",
        agent_used="EndocrinolAgent",
        safety_check=SafetyCheckResult(passed=True),
        feedback_info=FeedbackInfo(),
    )
    assert response.reply == "这是一条回复"
    assert response.department == "内分泌科"


def test_finetune_request_defaults():
    """Test FinetuneRequest default values."""
    request = FinetuneRequest(
        model_name="qwen-vl-plus",
        dataset_path="/data/train.json",
        output_dir="/models/output",
    )
    assert request.method == "qlora"
    assert request.lora_r == 64
    assert request.lora_alpha == 16
    assert request.learning_rate == 2e-4
    assert request.num_epochs == 3
    assert request.batch_size == 4


def test_data_augment_request():
    """Test DataAugmentRequest validation."""
    request = DataAugmentRequest(
        source="pmc", target_size=8000, categories=["体检报告解读", "指标异常问询"]
    )
    assert request.source == "pmc"
    assert request.target_size == 8000
    assert len(request.categories) == 2
