"""健康风险提示（GLOSSARY.md 的「健康风险提示」）。

这条链路此前用两套平行形状回答「患者看到了什么」：`Finding`（内部事实层）与
`PatientFinding`（患者层）共享约 15 个同名同义字段，而**两份都原样出域**，
客户端再用 `condition_code` 当场 join。本文件钉住收敛后的三条外部行为：

1. **只出一个形状**：出域的是 `PatientNotices`，内部层字段（`sorting` /
   `epidemiology_background` / `card.published_at`）不出域；
2. **身份唯一且有校验**：重复或不一致的 `condition_code` 走「格式无效」；
3. **摘要有单一来源**：`summary` 由投影函数无条件产出，不再只在 unmatched 非空时改写。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schema.evidence import build_patient_notices

CORRELATION = "00000000-0000-0000-0000-000000000001"


def _observation(observation_id: str = "obs-1") -> dict:
    return {
        "observation_id": observation_id,
        "metric_code": "fasting_glucose",
        "metric_label": "空腹血糖",
        "value": 6.5,
        "unit": "mmol/L",
        "evidence_text": "空腹血糖 6.5 mmol/L",
        "source_file_index": 1,
        "source_page": 1,
    }


def _card(condition_code: str = "COND_PREDIABETES") -> dict:
    return {
        "id": "card-1",
        "condition_code": condition_code,
        "scope_key": "metric:fasting_glucose",
        "version": "1.0.0",
        "status": "published",
        "grade": "moderate",
        "published_at": "2026-01-01T00:00:00Z",
        "evidence_profile_id": "profile-1",
        "patient_visible_body": "正文",
        "sources": [
            {
                "claim_id": "c1",
                "paper_id": "p1",
                "paper_title": "某研究",
                "doi": "10.1/x",
                "evidence": "证据",
                "locator": "sec-1",
            }
        ],
    }


def _unmatched(observation_id: str) -> dict:
    return {
        "observation_id": observation_id,
        "metric_label": "某指标",
        "condition_codes": [],
        "reason": "unknown_metric_code",
    }


def _finding(condition_code: str = "COND_PREDIABETES") -> dict:
    return {
        "condition_code": condition_code,
        "condition_name": "糖尿病前期 / 糖代谢异常",
        "urgency": "routine",
        "abnormality_severity": 1,
        "evidence_strength": "moderate",
        "needs_recheck": True,
        "department": "内分泌科",
        "recheck_direction": "复查空腹血糖",
        "epidemiology_background": "流行背景",
        "source_observation_ids": ["obs-1"],
        "source_observations": [_observation()],
        "sorting": {
            "urgency": "routine",
            "abnormality_severity": 1,
            "evidence_strength": "moderate",
            "needs_recheck": True,
            "department": "内分泌科",
            "epidemiology_background": "流行背景",
        },
        "evidence_items": [
            {
                "metric_code": "fasting_glucose",
                "metric_label": "空腹血糖",
                "card": _card(condition_code),
                "evidence_strength": "moderate",
                "source_observation_ids": ["obs-1"],
                "source_observations": [_observation()],
            }
        ],
    }


def _response(*, findings=None, unmatched=None, skipped=None, patient_findings=None) -> dict:
    findings = findings if findings is not None else [_finding()]
    return {
        "schema_version": "3",
        "sorting_version": "published-card-reference-range-v1",
        "correlation_id": CORRELATION,
        "findings": findings,
        "unmatched": unmatched or [],
        "skipped": skipped or [],
        "message": "证据服务写下的摘要",
        "patient_reply": {
            "title": "体检报告解读与健康风险提示",
            "summary": "证据服务写下的摘要",
            "findings": (
                patient_findings
                if patient_findings is not None
                else [
                    {
                        "condition_code": finding["condition_code"],
                        "condition_name": finding["condition_name"],
                        "urgency": finding["urgency"],
                        "abnormality_severity": finding["abnormality_severity"],
                        "evidence_strength": finding["evidence_strength"],
                        "needs_recheck": finding["needs_recheck"],
                        "department": finding["department"],
                        "recheck_direction": finding["recheck_direction"],
                        "source_observation_ids": finding["source_observation_ids"],
                        "source_observations": finding["source_observations"],
                        "evidence_items": finding["evidence_items"],
                    }
                    for finding in findings
                ]
            ),
            "unmatched_count": len(unmatched or []),
            "disclaimer": "仅供健康信息参考。",
        },
    }


def _notices(payload: dict):
    from app.schema.evidence import EvidenceMatchResponse

    return build_patient_notices(EvidenceMatchResponse.model_validate(payload))


# ---------------------------------------------------------------------------
# 只出一个形状
# ---------------------------------------------------------------------------

def test_internal_fields_do_not_reach_the_patient_projection():
    notices = _notices(_response())
    dumped = notices.model_dump(mode="json")

    assert set(dumped) == {
        "correlation_id",
        "title",
        "summary",
        "findings",
        "unmatched",
        "skipped",
        "unmatched_count",
        "disclaimer",
    }
    assert "sorting" not in dumped["findings"][0]
    assert "epidemiology_background" not in dumped["findings"][0]

    # 已知边界：证据项的 `card` 是证据服务契约层（两个仓库共用），它自带
    # `published_at` / `evidence_profile_id`。要收掉这两个字段得改跨仓契约，
    # 不在本票范围 —— 本票收掉的是**内部事实层**（`sorting` / 流行病学背景 /
    # 内部层的裸 `card`）不再作为顶层数组出域。
    card = dumped["findings"][0]["evidence_items"][0]["card"]
    assert card["id"] and card["version"]


# ---------------------------------------------------------------------------
# 身份唯一且有校验
# ---------------------------------------------------------------------------

def test_duplicate_condition_codes_are_rejected():
    """重复的 `condition_code` 走「格式无效」，而不是让客户端 Map 静默折叠。

    此前客户端用一个 `Map` 按 `condition_code` join，重复时保留最后一条 ——
    两张卡片共用它的证据与推荐，且没有任何东西会报错。
    """
    from app.schema.evidence import EvidenceMatchResponse

    payload = _response(findings=[_finding(), _finding()])
    with pytest.raises(ValidationError):
        EvidenceMatchResponse.model_validate(payload)


def test_projection_must_correspond_to_the_internal_findings():
    from app.schema.evidence import EvidenceMatchResponse

    payload = _response(
        findings=[_finding()],
        patient_findings=[
            {
                **_response()["patient_reply"]["findings"][0],
                "condition_code": "COND_UNKNOWN",
            }
        ],
    )
    with pytest.raises(ValidationError):
        EvidenceMatchResponse.model_validate(payload)


def test_card_condition_must_match_its_finding():
    from app.schema.evidence import EvidenceMatchResponse

    finding = _finding()
    finding["evidence_items"][0]["card"] = _card("COND_OTHER")
    with pytest.raises(ValidationError):
        EvidenceMatchResponse.model_validate(_response(findings=[finding]))


# ---------------------------------------------------------------------------
# 摘要有单一来源
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("findings", "unmatched", "expected_fragment"),
    [
        ([_finding()], [], "1 个可能相关健康问题"),
        ([], [_unmatched("obs-1")], "暂无已审核内容"),
        ([], [], "未见需要关注的问题"),
    ],
)
def test_summary_is_always_produced_by_the_projection(findings, unmatched, expected_fragment):
    """摘要**无条件**由投影产出 —— 不再只在 unmatched 非空时改写。"""
    notices = _notices(_response(findings=findings, unmatched=unmatched))
    assert expected_fragment in notices.summary


def test_summary_overrides_the_evidence_service_wording_even_without_unmatched():
    """**没有 unmatched** 时摘要也由投影产出，不是证据服务写下的那句。

    这是旧行为的分界线：此前端点在 `unmatched` 非空时改写 `message` 与
    `patient_reply.summary`，为空时两者各保留证据服务独立写下的值 —— 同一个
    摘要两个来源。
    """
    notices = _notices(_response(findings=[_finding()], unmatched=[]))
    assert notices.summary != "证据服务写下的摘要"
    assert "1 个可能相关健康问题" in notices.summary


def test_only_normal_skips_still_conclude_normal():
    """`skipped` 全是 `within_reference_range` 时，说法仍是「正常」。

    那一条 skipped 的意思是「**判定过**，在参考区间内」—— 把它说成「未能完成
    解读」是误报（评审在 #161 指出）。
    """
    skipped = [{"observation_id": "obs-1", "reason": "within_reference_range"}]
    notices = _notices(_response(findings=[], unmatched=[], skipped=skipped))
    assert "均在参考区间内" in notices.summary
    assert "未能完成解读" not in notices.summary


def test_summary_counts_only_the_patient_visible_findings():
    """摘要里的问题数按患者可见集合算，不是内部层全集。"""
    internal_only = _finding("COND_INTERNAL_ONLY")
    payload = _response(findings=[_finding(), internal_only])
    payload["patient_reply"]["findings"] = [payload["patient_reply"]["findings"][0]]
    from app.schema.evidence import EvidenceMatchResponse

    notices = build_patient_notices(EvidenceMatchResponse.model_validate(payload))
    assert "1 个可能相关健康问题" in notices.summary
    assert "2 个" not in notices.summary


def test_hidden_internal_findings_do_not_produce_a_normal_conclusion():
    """内部层有健康问题、患者投影里没有时，**不能**宣告「均在参考区间内」。

    投影可以是内部层的真子集（`validate_condition_identity` 只要求投影 ⊆ 内部层）。
    此时若只看投影与 skipped，会落到「一切正常」那一支 —— 而证据服务明明找出了
    问题（评审在 #161 指出）。
    """
    payload = _response(findings=[_finding()])
    payload["patient_reply"]["findings"] = []
    payload["skipped"] = [{"observation_id": "obs-9", "reason": "within_reference_range"}]
    from app.schema.evidence import EvidenceMatchResponse

    notices = build_patient_notices(EvidenceMatchResponse.model_validate(payload))
    assert "均在参考区间内" not in notices.summary


def test_skipped_metrics_do_not_produce_a_normal_conclusion():
    """`skipped` 非空时**不能**宣告「指标均在参考区间内」。

    `skipped` 里的原因（证据不足、值不可解析、缺参考范围）只说明「这次没能
    判定」，不代表正常 —— 宣告正常是替患者下一个服务端给不出的结论
    （评审在 #161 指出）。
    """
    skipped = [{"observation_id": "obs-1", "reason": "missing_source_evidence"}]
    notices = _notices(_response(findings=[], unmatched=[], skipped=skipped))
    assert "均在参考区间内" not in notices.summary
    assert "1 项指标未能完成解读" in notices.summary


def test_only_the_patient_selected_findings_are_projected():
    """患者可见集合以 `patient_reply.findings` 为准，不是内部层全集。

    `validate_condition_identity` 只要求投影 ⊆ 内部层 —— 内部层可以比投影多。
    拿内部层构造会把**未被选入患者投影**的问题也展示给患者。
    """
    internal_only = _finding("COND_INTERNAL_ONLY")
    payload = _response(findings=[_finding(), internal_only])
    payload["patient_reply"]["findings"] = [payload["patient_reply"]["findings"][0]]
    from app.schema.evidence import EvidenceMatchResponse

    notices = build_patient_notices(EvidenceMatchResponse.model_validate(payload))
    assert [finding.condition_code for finding in notices.findings] == ["COND_PREDIABETES"]


def test_summary_reflects_both_findings_and_unmatched():
    unmatched = [_unmatched("obs-9")]
    notices = _notices(_response(findings=[_finding()], unmatched=unmatched))
    assert "1 个可能相关健康问题" in notices.summary
    assert "1 条指标" in notices.summary


# ---------------------------------------------------------------------------
# 历史行（#161 复审）
# ---------------------------------------------------------------------------

def test_historical_rows_are_readable():
    """库里存的旧形状（`EvidenceMatchResponse` 的 dump）必须能读出来。

    旧行含 `schema_version` / `patient_reply` / 内部层 `findings`；收敛后出域的
    形状变了，但**已经写进数据库的数据不会自己改变** —— 读路径必须能读，
    否则患者的每一份历史报告都打不开。
    """
    from app.api.report import _patient_notices_from_stored

    stored = _response()  # 旧形状：EvidenceMatchResponse 的 dump
    notices = _patient_notices_from_stored(stored)

    assert "schema_version" not in notices
    assert notices["title"] == "体检报告解读与健康风险提示"
    assert notices["summary"] == "证据服务写下的摘要"  # 旧行按当时投影出的值呈现
    assert notices["findings"][0]["condition_code"] == "COND_PREDIABETES"


def test_new_shape_passes_through_unchanged():
    from app.api.report import _patient_notices_from_stored

    notices = _notices(_response()).model_dump(mode="json")
    assert _patient_notices_from_stored(notices) is notices
