"""商品推荐同意闸门与按健康问题分组（PRD #266 子单 #271）。

两件事，都在 HTTP 层断言（沿用 `tests/test_mall_goods.py` 的商城替身与端点夹具）：

1. **闸门。** 没有对当前版本的「商品推荐」同意时，推荐端点返回空结果与
   `product_recommendation_consent_required`，且**一次商城读取都没有发生**。闸门在
   调用商城之前 —— 这正是「门槛在服务端」与「界面上一个勾」的区别。文本版本更新后
   旧同意同样不再有效（判据是 `consent.has_valid`，与状态接口同一次判定）。
2. **分组。** 有同意时响应同时含 `groups` 与扁平 `items` / `reason`；`groups` 里每件
   商品都出现在 `items` 中，并落在它所属的健康问题下；某个方向在商城侧没货时该组为空
   且带它自己的原因，不影响别的组。

「未同意」这个取值只在 `app/service/recommendation_empty_reason.py` 声明一次
（ADR 0019），由 `tests/test_recommendation_empty_reason.py` 的结构守卫保证。
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base, HealthDataConsent, TicketSubject
from app.data.models import MedicalReport as ReportModel
from app.service.consent import required_text_version
from app.service.consent_vocabulary import (
    PRODUCT_RECOMMENDATION,
    REPORT_INTERPRETATION,
)
from app.service.mall_goods import MallGoodsItem, MallGoodsResult
from app.service.sessions import SESSION_COOKIE, issue_session

DYSLIPIDEMIA = ("COND_DYSLIPIDEMIA", "血脂异常")
PREDIABETES = ("COND_PREDIABETES", "糖尿病前期 / 糖代谢异常")


def _conditions(*entries: tuple[str, str]) -> dict:
    return {
        "findings": [
            {"condition_code": code, "condition_name": name} for code, name in entries
        ]
    }


OWNER = "account:rec-tenant:rec-user"


def _consent_row(*, decision: str = "granted", version: str | None = None) -> HealthDataConsent:
    """一条「商品推荐」表态。每次调用返回**新行**，不跨用例复用 ORM 对象。"""
    return HealthDataConsent(
        owner_id=OWNER,
        consent_item=PRODUCT_RECOMMENDATION,
        decision=decision,
        text_version=version or required_text_version(PRODUCT_RECOMMENDATION),
        source="seed",
    )


def _granted():
    """有推荐同意的主体。分组用例用它把闸门打开，好测闸门之外的事。"""
    return [_consent_row()]


@contextlib.contextmanager
def _client(*, evidence_result: dict, consent: list[HealthDataConsent] | None = None):
    """报告 + 会话 + 可选的同意行，全都落在同一个内存库里。

    `consent=None` 表示「从未表过态」；传列表则原样落库（用来造撤回、旧版本这类状态）。
    「报告解读」同意总是给上：这张单测的是推荐闸门，不是上传闸门。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    owner_id = OWNER
    with SessionLocal() as session:
        report = ReportModel(
            patient_id="P-rec",
            report_type="体检",
            status="assessed",
            owner_id=owner_id,
            evidence_result=evidence_result,
        )
        session.add(report)
        token, session_row = issue_session(owner_id)
        session.add(session_row)
        # 会话主体必须能在 `TicketSubject` 里查到，否则会话被判为不可用（停用主体那一支）。
        session.add(
            TicketSubject(
                id="ticket-subject-rec",
                tenant_id="rec-tenant",
                external_subject="rec-user",
                display_name="商城用户",
            )
        )
        session.add(
            HealthDataConsent(
                owner_id=owner_id,
                consent_item=REPORT_INTERPRETATION,
                decision="granted",
                text_version=required_text_version(REPORT_INTERPRETATION),
                source="seed",
            )
        )
        for row in consent or []:
            session.add(row)
        session.commit()
        report_id = report.id

    def override_get_db():
        with SessionLocal() as session:
            yield session

    with (
        patch("app.data.get_db", override_get_db),
        patch("app.api.report.resolve_owner", return_value=SimpleNamespace(storage_id=owner_id)),
    ):
        from app.main import app

        yield TestClient(app), report_id, {SESSION_COOKIE: token}


def _get(client, report_id: int, cookie: dict) -> dict:
    response = client.get(f"/api/health/report/{report_id}/recommendations", cookies=cookie)
    assert response.status_code == 200, response.text
    return response.json()


def _item(spu: str) -> MallGoodsItem:
    return MallGoodsItem(id=spu, name=f"商品 {spu}")


def _tagged(spu: str, condition_code: str) -> MallGoodsItem:
    """挂在某个健康方向上的商品：标签取自**服务端发出去的那一对**（`condition_tags`）。"""
    from app.service.mall_goods import label_pairs_for

    return _item(spu).model_copy(update={"labels": frozenset(label_pairs_for(condition_code))})


def _mall_answering(by_condition: dict[str, tuple[MallGoodsItem, ...]]):
    """按**这次取货带的那一对标签**回答的商城替身。

    每个健康方向是一次独立取货，所以替身必须按入参回答；一个固定返回值的 mock 会让
    两次调用得到同一批商品，那恰好掩盖了「分组靠哪一次取货」这件事。
    """

    async def answer(*, labels, settings=None):
        from app.service.mall_goods import label_pairs_for

        for code, items in by_condition.items():
            if labels == label_pairs_for(code):
                return MallGoodsResult(items=items, reason=None if items else "no_label_data")
        return MallGoodsResult(items=(), reason="no_label_data")

    return answer


# ── 闸门 ────────────────────────────────────────────────────────────────────


def test_without_product_consent_the_endpoint_returns_not_consented_and_never_reads_the_mall():
    """没有推荐同意 → 空结果 + 具名原因，且商城一次都没有被调用。

    **负控是 `fetch.assert_not_called()`**：只断言响应是闸门不够 —— 一个「先问了商城、
    再把结果丢掉」的实现会返回同样的 JSON，而它已经把一个敏感问题送到了商城手上。
    """
    with (
        _client(evidence_result=_conditions(DYSLIPIDEMIA)) as (client, report_id, cookie),
        patch("app.api.report.fetch_goods") as fetch,
    ):
        body = _get(client, report_id, cookie)

    assert body["items"] == []
    assert body["groups"] == []
    assert body["reason"] == "product_recommendation_consent_required"
    fetch.assert_not_called()


def test_the_gate_is_answered_before_the_report_conditions_are_read():
    """闸门先于「这份报告有没有健康风险」：没同意时不说 `no_published_card`。

    报告有没有风险是**同意之后**才谈得上的事。若先判条件，一个没有风险、也没同意的
    患者会得到「本次未能生成健康风险提示」，而那句话让他去找报告的问题，真正要做的是
    打开推荐同意。
    """
    with (
        _client(evidence_result=_conditions()) as (client, report_id, cookie),
        patch("app.api.report.fetch_goods") as fetch,
    ):
        body = _get(client, report_id, cookie)

    assert body["reason"] == "product_recommendation_consent_required"
    fetch.assert_not_called()


def test_a_revoked_product_consent_closes_the_gate():
    """撤回是又一条记录，当前状态取最新一条：撤回之后没有推荐。"""
    rows = [_consent_row(decision="revoked")]
    with (
        _client(evidence_result=_conditions(DYSLIPIDEMIA), consent=rows) as (client, report_id, cookie),
        patch("app.api.report.fetch_goods") as fetch,
    ):
        body = _get(client, report_id, cookie)

    assert body["reason"] == "product_recommendation_consent_required"
    fetch.assert_not_called()


def test_a_consent_for_the_old_text_version_does_not_open_the_gate():
    """文本版本一变，旧同意不再有效 —— 闸门与状态接口读的是同一份判据。"""
    rows = [_consent_row(version="draft-1999-01-01")]
    with (
        _client(evidence_result=_conditions(DYSLIPIDEMIA), consent=rows) as (client, report_id, cookie),
        patch("app.api.report.fetch_goods") as fetch,
    ):
        body = _get(client, report_id, cookie)

    assert body["reason"] == "product_recommendation_consent_required"
    fetch.assert_not_called()


def test_the_report_interpretation_consent_does_not_open_the_recommendation_gate():
    """两项彼此独立：同意报告解读不等于允许拿它推荐商品。"""
    # `_client` 永远给「报告解读」同意；这里刻意**不**给推荐同意。
    with (
        _client(evidence_result=_conditions(DYSLIPIDEMIA)) as (client, report_id, cookie),
        patch("app.api.report.fetch_goods") as fetch,
    ):
        body = _get(client, report_id, cookie)

    assert body["reason"] == "product_recommendation_consent_required"
    fetch.assert_not_called()


def test_a_granted_consent_lets_the_request_through_to_the_mall():
    """有同意时闸门放行，响应里不再出现那个原因。"""
    rows = [_consent_row()]
    with (
        _client(evidence_result=_conditions(DYSLIPIDEMIA), consent=rows) as (client, report_id, cookie),
        patch(
            "app.api.report.fetch_goods",
            return_value=MallGoodsResult(items=(), reason="no_label_data"),
        ) as fetch,
    ):
        body = _get(client, report_id, cookie)

    assert body["reason"] == "no_label_data"
    fetch.assert_awaited_once()


def test_the_new_reason_is_declared_only_in_its_owner():
    """「未同意」是「取货为空」那一组的成员，且只在词表模块里声明（ADR 0019）。"""
    from app.schema.report import RecommendationResponse
    from app.service.recommendation_empty_reason import RECOMMENDATION_REASONS

    assert "product_recommendation_consent_required" in RECOMMENDATION_REASONS
    assert RecommendationResponse(items=[], reason="product_recommendation_consent_required").reason == (
        "product_recommendation_consent_required"
    )


# ── 分组 ────────────────────────────────────────────────────────────────────


def test_groups_and_flat_items_carry_the_same_goods_under_their_condition():
    """`groups` 与 `items` 是同一批商品：组里每件都出现在 `items`，且落在它所属的方向下。

    两组的标签各自只命中自己的商品，所以归属是**可判的**：商品带的标签与哪一组发出去的
    标签相交，它就属于哪一组。一件同时挂两个方向的商品只应出现一次（先到先得），而它在
    `items` 里也只出现一次。
    """
    with (
        _client(
            evidence_result=_conditions(DYSLIPIDEMIA, PREDIABETES),
            consent=_granted(),
        ) as (client, report_id, cookie),
        patch(
            "app.api.report.fetch_goods",
            side_effect=_mall_answering(
                {
                    DYSLIPIDEMIA[0]: (_tagged("spu-lipid", DYSLIPIDEMIA[0]),),
                    PREDIABETES[0]: (_tagged("spu-sugar", PREDIABETES[0]),),
                }
            ),
        ),
    ):
        body = _get(client, report_id, cookie)

    assert body["reason"] is None
    assert [item["id"] for item in body["items"]] == ["spu-lipid", "spu-sugar"]

    groups = {group["condition_code"]: group for group in body["groups"]}
    assert groups["COND_DYSLIPIDEMIA"]["condition_name"] == "血脂异常"
    assert [item["id"] for item in groups["COND_DYSLIPIDEMIA"]["items"]] == ["spu-lipid"]
    assert groups["COND_DYSLIPIDEMIA"]["reason"] is None
    assert [item["id"] for item in groups["COND_PREDIABETES"]["items"]] == ["spu-sugar"]
    assert groups["COND_PREDIABETES"]["reason"] is None

    # 组里每件商品都能在扁平 `items` 中找到 —— 两个形状说的是同一批货。
    flat_ids = {item["id"] for item in body["items"]}
    for group in body["groups"]:
        assert {item["id"] for item in group["items"]} <= flat_ids


def test_a_condition_without_goods_is_an_empty_group_and_does_not_affect_the_others():
    """一个方向在商城侧没货只让那一组为空，别的组照常有货。"""
    with (
        _client(
            evidence_result=_conditions(DYSLIPIDEMIA, PREDIABETES),
            consent=_granted(),
        ) as (client, report_id, cookie),
        patch(
            "app.api.report.fetch_goods",
            side_effect=_mall_answering(
                {
                    DYSLIPIDEMIA[0]: (),
                    PREDIABETES[0]: (_tagged("spu-sugar", PREDIABETES[0]),),
                }
            ),
        ),
    ):
        body = _get(client, report_id, cookie)

    groups = {group["condition_code"]: group for group in body["groups"]}
    assert groups["COND_DYSLIPIDEMIA"]["items"] == []
    assert groups["COND_DYSLIPIDEMIA"]["reason"] == "no_label_data"
    assert [item["id"] for item in groups["COND_PREDIABETES"]["items"]] == ["spu-sugar"]
    assert groups["COND_PREDIABETES"]["reason"] is None
    # 整体仍有货，所以顶层原因为空（旧前端只看这个）。
    assert body["reason"] is None


def test_a_condition_without_a_label_mapping_gets_an_empty_group_without_calling_the_mall():
    """没有映射的健康方向不去问商城，它的组说 `no_label_data`（与既有口径一致）。"""
    with (
        _client(
            evidence_result=_conditions(("COND_NOT_MAPPED_YET", "未映射的方向")),
            consent=_granted(),
        ) as (
            client,
            report_id,
            cookie,
        ),
        patch("app.api.report.fetch_goods") as fetch,
    ):
        body = _get(client, report_id, cookie)

    fetch.assert_not_called()
    assert body["groups"] == [
        {
            "condition_code": "COND_NOT_MAPPED_YET",
            "condition_name": "未映射的方向",
            "items": [],
            "reason": "no_label_data",
        }
    ]
    assert body["reason"] == "no_label_data"


def test_every_group_carries_the_outage_when_the_mall_is_unreachable():
    """商城不可达是**整次取货**的事实：每组都带 `mall_unavailable`，不说成「这个方向没货」。"""
    with (
        _client(
            evidence_result=_conditions(DYSLIPIDEMIA, PREDIABETES),
            consent=_granted(),
        ) as (client, report_id, cookie),
        patch(
            "app.api.report.fetch_goods",
            return_value=MallGoodsResult(items=(), reason="mall_unavailable"),
        ),
    ):
        body = _get(client, report_id, cookie)

    assert body["reason"] == "mall_unavailable"
    assert {group["reason"] for group in body["groups"]} == {"mall_unavailable"}
    assert all(group["items"] == [] for group in body["groups"])


def test_the_old_flat_shape_is_unchanged_for_an_old_client():
    """**旧前端契约**：`items` 与 `reason` 仍在，且与改动前的取值一致。"""
    hit = _item("spu-1")
    from app.schema.report import RecommendationResponse

    body = RecommendationResponse(
        items=[{"id": hit.id, "name": hit.name}],
        reason=None,
    ).model_dump()
    assert set(body) >= {"items", "reason"}
    assert body["groups"] == [], "旧形状不因为新字段而要求调用方提供它"

    with (
        _client(
            evidence_result=_conditions(DYSLIPIDEMIA), consent=_granted()
        ) as (client, report_id, cookie),
        patch(
            "app.api.report.fetch_goods",
            return_value=MallGoodsResult(items=(_item("spu-1"),), reason=None),
        ),
    ):
        live = _get(client, report_id, cookie)
    assert [item["id"] for item in live["items"]] == ["spu-1"]
    assert live["reason"] is None


@pytest.mark.parametrize("decision", ["granted", "revoked"])
def test_the_gate_reads_the_same_judgement_as_the_status_endpoint(decision):
    """闸门与 `/api/auth/consent` 读同一次判定：不可能「界面说已同意、服务端拒绝推荐」。"""
    rows = [_consent_row(decision=decision)]
    with _client(evidence_result=_conditions(DYSLIPIDEMIA), consent=rows) as (client, report_id, cookie):
        with patch(
            "app.api.report.fetch_goods",
            return_value=MallGoodsResult(items=(), reason="no_label_data"),
        ):
            body = _get(client, report_id, cookie)
        status = client.get("/api/auth/consent", cookies=cookie).json()

    published = {entry["item"]: entry for entry in status["items"]}[PRODUCT_RECOMMENDATION]
    assert published["valid"] is (decision == "granted")
    assert (body["reason"] == "product_recommendation_consent_required") is (not published["valid"])
