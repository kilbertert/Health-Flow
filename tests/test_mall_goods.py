"""#174 检测页商品：服务端代理取货、空态归因与降级边界。

边界（ADR 0006 + docs/adr/0004）：本服务不自持商品、不做可售性判断、不写商城状态；
浏览器不得直接调商城。这三种"空"必须可区分，否则运维分不清该修配置还是提醒租户上货。
"""

import contextlib
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base
from app.data.models import MedicalReport as ReportModel
from app.service.mall_goods import (
    MallGoodsItem,
    MallGoodsResult,
    filter_by_labels,
    label_pairs_for,
    parse_items,
    sign_payload,
)

# 实测（2026-09-29）东宸药业返回的一条真实记录：价格是十进制元的浮点，库存为 null。
LIVE_RECORD = {
    "id": "1610079554940235778",
    "name": "力蜚能 多糖铁复合物胶囊 0.15g*10粒",
    "image": "https://oss.aliyuncs.com/example.jpg",
    "priceDown": 36.5,
    "priceUp": 36.5,
    "stock": None,
    "shopId": "1582258389846802433",
}


def _settings(**overrides):
    base = {
        "MALL_WEBAPI_BASE_URL": "https://mall.example.test",
        "MALL_WEBAPI_GOODS_PATH": "/mallapi/webapi/goods/read",
        "MALL_WEBAPI_APP_ID": "healthflow-genesis-readonly",
        "MALL_WEBAPI_APP_SECRET": "s3cret",
        "MALL_WEBAPI_TENANT_ID": "1578664130444005376",
        "MALL_WEBAPI_TIMEOUT_SECONDS": 5.0,
        "MALL_WEBAPI_PAGE_SIZE": 100,
    }
    base.update(overrides)
    required = (
        "MALL_WEBAPI_BASE_URL",
        "MALL_WEBAPI_APP_ID",
        "MALL_WEBAPI_APP_SECRET",
        "MALL_WEBAPI_TENANT_ID",
    )
    base["mall_webapi_configured"] = all(base[name] for name in required)
    return SimpleNamespace(**base)


def test_signature_uses_uppercase_hex_twice():
    """内层小写会被商城判"签名错误"（实测）；两层都必须大写。"""
    import hashlib

    payload = {"appId": "app", "timeStamp": 1730000000000, "current": 1, "size": 100}
    joined = "appId=app&current=1&size=100&timeStamp=1730000000000&appSecret=s3cret"
    inner = hashlib.md5(joined.encode()).hexdigest().upper()
    expected = hashlib.md5((inner + "s3cret").encode()).hexdigest().upper()

    assert sign_payload(payload, "s3cret") == expected
    assert sign_payload(payload, "s3cret").isupper()


def test_signature_skips_empty_values_and_sorts_by_name():
    """空值不参与签名；顺序按原始键名升序（不区分大小写地"看起来"相同也不行）。"""
    assert sign_payload({"b": "", "a": "1", "sign": "ignored"}, "k") == sign_payload(
        {"a": "1", "b": None}, "k"
    )


def test_parse_items_fails_closed_on_unexpected_shape():
    from app.service.mall_goods import MallGoodsError

    with pytest.raises(MallGoodsError, match="格式无效"):
        parse_items({"code": 0, "data": "not-a-list"})
    with pytest.raises(MallGoodsError, match="格式无效"):
        parse_items({"code": 0, "data": ["not-a-record"]})


def test_single_record_maps_price_and_keeps_null_stock():
    items = parse_items({"code": 0, "data": [LIVE_RECORD]})

    assert len(items) == 1
    assert items[0].id == "1610079554940235778"
    assert str(items[0].price_down) == "36.5"
    # null 是"商城未标注"，不是 0。
    assert items[0].stock is None


def test_labels_gate_returns_empty_today():
    """标签接缝今天如实为空：没有映射就不该凭空返回商品。

    即使调用方**传了**标签，端点返回的商品也不带标签字段可比对，判据不存在，
    因此结果仍为空——这正是"不放宽过滤"的具体形状。
    """
    assert label_pairs_for("COND_DYSLIPIDEMIA") == ()
    assert filter_by_labels((MallGoodsItem(id="1"),), ()) == ()
    assert filter_by_labels((MallGoodsItem(id="1"),), (("慢病风险", "血脂异常风险评估"),)) == ()


@pytest.mark.asyncio
async def test_unconfigured_mall_does_not_send_a_request():
    """缺配置时降级，不发一个注定被拒的请求。"""
    from app.service.mall_goods import fetch_goods

    settings = _settings(MALL_WEBAPI_APP_SECRET="")
    with patch("app.service.mall_goods.httpx.AsyncClient") as client:
        result = await fetch_goods(settings=settings, labels=())

    assert result == MallGoodsResult(items=(), reason="mall_unavailable")
    client.assert_not_called()


@pytest.mark.asyncio
async def test_unreachable_mall_degrades_instead_of_raising():
    from app.service.mall_goods import fetch_goods

    with patch(
        "app.service.mall_goods.httpx.AsyncClient.post",
        side_effect=httpx.ConnectTimeout("boom"),
    ):
        result = await fetch_goods(settings=_settings(), labels=())

    assert result.items == ()
    assert result.reason == "mall_unavailable"


@pytest.mark.asyncio
async def test_business_error_code_degrades():
    """商城把签名/租户类错误也放在 200 + code!=0 里，必须按失败处理。"""
    from app.service.mall_goods import fetch_goods

    response = MagicMock(status_code=200)
    response.json.return_value = {"code": 1, "msg": "签名错误", "data": None}
    with patch("app.service.mall_goods.httpx.AsyncClient.post", return_value=response):
        result = await fetch_goods(settings=_settings(), labels=())

    assert result.reason == "mall_unavailable"


@pytest.mark.asyncio
async def test_successful_call_without_labels_reports_no_label_data():
    """调用成功但没有标签 → 归因到标签，不是"商城挂了"。"""
    from app.service.mall_goods import fetch_goods

    response = MagicMock(status_code=200)
    response.json.return_value = {"code": 0, "data": [LIVE_RECORD]}
    with patch("app.service.mall_goods.httpx.AsyncClient.post", return_value=response) as post:
        result = await fetch_goods(settings=_settings(), labels=())

    assert result.items == ()
    assert result.reason == "no_label_data"
    # 无标签也照常调用商城：这条 AC 要的是"服务端真的发出去了"。
    assert post.call_count == 1
    sent = post.call_args.kwargs
    assert sent["headers"]["tenant-id"] == "1578664130444005376"
    assert sent["headers"]["mall-app-id"] == "healthflow-genesis-readonly"
    assert sent["json"]["sign"].isupper()


@contextlib.contextmanager
def _report_client(evidence_result):
    """一个只含报告的 SQLite 会话 + TestClient，报告已带 evidence_result。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        report = ReportModel(
            patient_id="P-goods",
            report_type="体检",
            status="assessed",
            owner_id="account:acct-1",
            access_token_hash=hashlib.sha256(b"report-token").hexdigest(),
            evidence_result=evidence_result,
        )
        session.add(report)
        session.commit()
        report_id = report.id

    def override_get_db():
        with SessionLocal() as session:
            yield session

    with (
        patch("app.data.get_db", override_get_db),
        patch("app.service.auth.account_for_request", return_value=SimpleNamespace(id="acct-1")),
        patch("app.api.report.resolve_owner", return_value=SimpleNamespace(storage_id="account:acct-1")),
    ):
        from app.main import app

        yield TestClient(app), report_id, "report-token"


def _finding(condition_code: str) -> dict:
    return {"condition_code": condition_code, "condition_name": "血脂异常"}


def test_endpoint_returns_condition_reasons():
    with _report_client({"findings": [_finding("COND_DYSLIPIDEMIA")]}) as (client, report_id, token):
        result = MallGoodsResult(items=(), reason="no_label_data")
        with patch("app.api.report.fetch_goods", return_value=result) as fetch:
            response = client.get(
                f"/api/health/report/{report_id}/recommendations",
                headers={"X-Report-Token": token},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["reason"] == "no_label_data"
    fetch.assert_awaited_once()
    # 无标签也真的调了商城：这是 #174 的验收项，不能只在测试里 mock。
    assert fetch.await_args.kwargs["labels"] == ()


def test_endpoint_reports_no_published_card_without_calling_mall():
    """没有健康风险就没有取货依据——不调商城，也不用营销内容填空。"""
    with (
        _report_client({"findings": []}) as (client, report_id, token),
        patch("app.api.report.fetch_goods") as fetch,
    ):
        response = client.get(
            f"/api/health/report/{report_id}/recommendations",
            headers={"X-Report-Token": token},
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "reason": "no_published_card"}
    fetch.assert_not_called()


def test_endpoint_surfaces_mall_outage_as_reason_not_error():
    with _report_client({"findings": [_finding("COND_DYSLIPIDEMIA")]}) as (client, report_id, token), patch(
        "app.api.report.fetch_goods",
        return_value=MallGoodsResult(items=(), reason="mall_unavailable"),
    ):
        response = client.get(
            f"/api/health/report/{report_id}/recommendations",
            headers={"X-Report-Token": token},
        )

    assert response.status_code == 200
    assert response.json()["reason"] == "mall_unavailable"


def test_endpoint_serializes_hit_items_without_touching_stock():
    hit = MallGoodsItem(
        id="1610079554940235778",
        name="力蜚能 多糖铁复合物胶囊",
        image="https://oss.aliyuncs.com/example.jpg",
        price_down="36.5",
        price_up="36.5",
        stock=None,
        shop_id="1582258389846802433",
    )
    with _report_client({"findings": [_finding("COND_DYSLIPIDEMIA")]}) as (client, report_id, token), patch(
        "app.api.report.fetch_goods",
        return_value=MallGoodsResult(items=(hit,), reason=None),
    ):
        response = client.get(
            f"/api/health/report/{report_id}/recommendations",
            headers={"X-Report-Token": token},
        )

    body = response.json()
    assert body["reason"] is None
    assert body["items"][0]["price_down"] == "36.5"
    assert body["items"][0]["stock"] is None
