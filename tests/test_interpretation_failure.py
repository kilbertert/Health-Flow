"""解读失败成因的判定（GLOSSARY.md 的「解读失败」，PRD #226）。

这一组断言的是**外部行为**：给分类入口一组真实形状的输入，它给出的成因是哪一个、
由成因派生出来的「要不要重试」与「患者做什么」是什么。不断言内部结构 —— 子串表怎么
排、有几个函数，都不该让这些测试变红。

四条**今天会判错**的样本是这组测试存在的理由（改名翻转重试、真瞬时故障不重试）。
"""

import httpx
import pytest

from app.service.interpretation_failure import (
    FAILURES_ORDERED,
    PATIENT_ACTION_BY_FAILURE,
    classify,
    is_retryable,
    patient_action,
    report_failure,
)


class _ProviderError(Exception):
    """一个 provider 侧的错误，带结构化状态码 —— 与 openai/httpx 的形状一致。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def _openai_error(name: str, message: str = "") -> Exception:
    """按类名造一个 provider SDK 的异常（跨库没有共同基类，类名就是结构化事实）。"""
    return type(name, (Exception,), {})(message)


# ── 四条今天会判错的样本 ──────────────────────────────────────────────────────


def test_a_timeout_shaped_sentence_is_not_taken_as_evidence_of_anything():
    """**这是本 PRD 的核心断言。** 今天 `_retryable_error` 会在这句话里找到 `timeout`，
    于是判定「可重试」。成因判定**不再**从这句话里读任何东西：

    - 「报告里没有可确认的指标」是一个**内容**事实，由抽取方看到空指标列表时当场判定，
      不靠文本（`classify` 对它一无所知）；
    - 判定剩下的唯一入口是 `classify`，而它看到 `timeout` 时给的是 `provider_unavailable`
      —— 那是「模型当时不可用」的兜底，与「报告里没有指标」是不同的一件事。
    """
    sentence = "timeout.jpg: 未提取到可确认的指标"
    assert classify("provider", text=sentence) == "provider_unavailable"
    assert not is_retryable("content_unrecognized")
    assert patient_action("content_unrecognized") == "upload_again"


def test_insufficient_quota_is_retryable():
    """额度耗尽是**当时**不可用 —— 今天它一次都不重试，因为 `insufficient_quota`
    里没有表内的任何子串。"""
    exc = _openai_error("RateLimitError", "该模型的账户额度已用尽 (insufficient_quota)")
    assert classify("provider", exc, text=str(exc)) == "provider_unavailable"
    assert is_retryable("provider_unavailable")


def test_a_rejected_credential_is_not_retryable():
    """凭据过期**不是**瞬时的。ADR-0006 已明确否决「把 401 当可重试」。"""
    exc = _ProviderError("Error code: 401 - Invalid 'Authorization' header or token.", 401)
    assert classify("provider", exc, text=str(exc)) == "provider_rejected"
    assert not is_retryable("provider_rejected")


def test_a_provider_protocol_error_is_retryable():
    """模型返回的不是它答应的 JSON —— 这一环当时不行。"""
    exc = ValueError("Expecting value: line 1 column 1 (char 0) while parsing json")
    assert classify("provider", exc, text=str(exc)) == "provider_unavailable"
    assert is_retryable("provider_unavailable")


# ── 结构化事实优先 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_transient_http_statuses_are_retryable_even_with_uninformative_text(status):
    exc = _ProviderError("upstream error", status)
    assert classify("provider", exc, text="upstream error") == "provider_unavailable"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_error_statuses_are_rejections_even_with_timeout_in_the_text(status):
    """文本里写着 `timeout` 也**不许**翻转判定 —— 状态码是结构化事实，它优先。"""
    exc = _ProviderError("request timeout while connecting", status)
    assert classify("provider", exc, text="request timeout while connecting") == "provider_rejected"


def test_an_httpx_timeout_that_crosses_as_a_bare_string_status():
    """`Error code: 503 - ...` 是文本里印着状态码的形式。它仍然只在本函数一处判定。"""
    exc = RuntimeError("Error code: 503 - Service Unavailable")
    assert classify("provider", exc, text=str(exc)) == "provider_unavailable"


def test_openai_sdk_error_classes_are_recognised_without_a_status_code():
    assert classify("provider", _openai_error("APITimeoutError")) == "provider_unavailable"
    assert classify("provider", _openai_error("AuthenticationError")) == "provider_rejected"
    assert classify("provider", _openai_error("RateLimitError")) == "provider_unavailable"


# ── 环节由位置决定，不由文本决定 ──────────────────────────────────────────────


def test_the_material_and_service_layers_are_decided_by_position_not_text():
    """一次数据库断连的错误文本里恰好写着什么，与它是不是基础设施故障无关。"""
    exc = _ProviderError("timeout talking to the database", 503)
    assert classify("service", exc) == "service_unavailable"
    assert classify("material", exc) == "material_unreadable"
    assert is_retryable("service_unavailable")
    assert not is_retryable("material_unreadable")


def test_a_real_httpx_timeout_is_classified():
    """真库的异常也走同一条判定（类名是跨库唯一的结构化抓手）。"""
    exc = httpx.ConnectTimeout("connection to provider timed out")
    assert classify("provider", exc, text=str(exc)) == "provider_unavailable"


# ── 报告级：取最上游的那一环 ──────────────────────────────────────────────────


def test_report_failure_takes_the_most_upstream_cause():
    assert report_failure(["content_unrecognized", "provider_unavailable"]) == "provider_unavailable"
    assert report_failure(["provider_unavailable", "material_unreadable"]) == "material_unreadable"
    assert report_failure([]) is None


def test_every_cause_has_an_action_and_a_retry_verdict():
    """词表与派生读法不许漂 —— 新增一类成因而不给它动作，是这里变红。"""
    for failure in FAILURES_ORDERED:
        assert patient_action(failure) in {"upload_again", "no_action"}
        assert is_retryable(failure) is (failure in {"service_unavailable", "provider_unavailable"})
    assert set(PATIENT_ACTION_BY_FAILURE) == set(FAILURES_ORDERED)


def test_only_two_causes_ever_ask_the_patient_to_re_upload():
    """ADR-0006 那句「请重新上传」误导的正是其余四类。"""
    asking = {name for name, action in PATIENT_ACTION_BY_FAILURE.items() if action == "upload_again"}
    assert asking == {"material_unreadable", "content_unrecognized"}
