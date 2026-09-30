"""#174 检测页商品：服务端代理取货、空态归因与降级边界。

边界（ADR 0006 + docs/adr/0005）：本服务不自持商品、不做可售性判断、不写商城状态；
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
from app.service.sessions import SESSION_COOKIE, issue_session

# 一次真实调用返回的记录形状（价格是十进制元的浮点、库存为 null）。
# 租户标识**不写进测试**：夹具要的是形状，不是某个租户的业务数据。
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
        "MALL_WEBAPI_LABELS_PATH": "/mallapi/goodsspu/getGoodsByLabels",
        "MALL_WEBAPI_APP_ID": "healthflow-genesis-readonly",
        "MALL_WEBAPI_APP_SECRET": "s3cret",
        "MALL_WEBAPI_TENANT_ID": "tenant-under-test",
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

    payload = {"appId": "app", "timeStamp": 1730000000000, "current": 1, "size": 100}
    joined = "appId=app&current=1&size=100&timeStamp=1730000000000&appSecret=s3cret"
    inner = hashlib.md5(joined.encode()).hexdigest().upper()
    expected = hashlib.md5((inner + "s3cret").encode()).hexdigest().upper()

    assert sign_payload(payload, "s3cret") == expected
    assert sign_payload(payload, "s3cret").isupper()


def test_signature_skips_empty_values_and_sorts_by_name():
    """空值不参与签名；顺序按原始键名升序（不区分大小写地"看起来"相同也不行）。"""
    assert sign_payload({"b": "", "a": "1", "sign": "ignored"}, "k") == sign_payload({"a": "1", "b": None}, "k")


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


LABELS = (("慢病风险", "血脂异常风险评估"),)


def test_labels_gate_returns_empty_when_nothing_to_filter_by():
    """无标签可查 → 空，且**不退回"全都要"**：那是这道闸门要防的事。"""
    assert label_pairs_for("COND_DYSLIPIDEMIA") == ()
    assert filter_by_labels((MallGoodsItem(id="1"),), ()) == ()


def test_labels_gate_is_an_intersection_not_a_guard():
    """有标签时做真交集——判据是商品自己的标签，不是"函数忽略输入"。

    商城端点今天不返回商品的标签字段，所以现实里过滤结果为空；但那是**数据**的空。
    这里用带标签的商品证明过滤逻辑本身是通的：命中保留、未命中剔除、部分命中只留命中项。
    """
    tagged = MallGoodsItem(id="hit", labels=frozenset(LABELS))
    other = MallGoodsItem(id="miss", labels=frozenset({("慢病风险", "骨质疏松症风险评估")}))
    bare = MallGoodsItem(id="bare")

    assert [item.id for item in filter_by_labels((tagged,), LABELS)] == ["hit"]
    assert filter_by_labels((other, bare), LABELS) == ()
    assert [item.id for item in filter_by_labels((tagged, other, bare), LABELS)] == ["hit"]


def test_items_without_a_label_field_have_no_labels():
    """端点今天不返回标签字段 → 每件商品标签为空集（数据的空，不是解析失败）。"""
    items = parse_items({"code": 0, "data": [LIVE_RECORD]})

    assert items[0].labels == frozenset()
    assert filter_by_labels(items, LABELS) == ()


def test_label_field_is_parsed_when_the_endpoint_supplies_it():
    labelled = dict(LIVE_RECORD, labels=[{"labelName": "慢病风险", "optionName": "血脂异常风险评估"}])

    items = parse_items({"code": 0, "data": [labelled]})

    assert items[0].labels == frozenset(LABELS)
    assert [item.id for item in filter_by_labels(items, LABELS)] == [items[0].id]


def test_null_data_is_an_empty_hit_not_a_format_error():
    """`data: null` = 调用成功但没命中，不是格式错误。

    按标签取货在没有商品挂该标签时就是这个形状；把它当格式错误会归因成
    "商城不可达"——运维会去查配置，而真正要做的是让租户上货/挂标签。
    """
    assert parse_items({"code": 0, "data": None, "ok": True}) == ()


def test_missing_data_key_is_still_a_format_error():
    """**键缺失**不是空命中。

    区别在于键在不在，不是值是不是 null：`data: null` 是「查到了，没有」，
    没有 `data` 键是「这个接口不是我以为的那个接口」。后者当空命中，会把一次契约
    破坏伪装成「这家没上货」。
    """
    from app.service.mall_goods import MallGoodsError

    with pytest.raises(MallGoodsError, match="格式无效"):
        parse_items({"code": 0, "ok": True})


def test_first_image_falls_back_to_pic_urls():
    """两条取货路径的图片字段形状不同，必须归一到同一个展示字段。"""
    from app.service.mall_goods import first_image

    assert first_image({"image": "a.jpg", "picUrls": ["b.jpg"]}) == "a.jpg"
    assert first_image({"picUrls": ["b.jpg", "c.jpg"]}) == "b.jpg"
    assert first_image({"picUrls": []}) is None
    assert first_image({"image": "   ", "picUrls": None}) is None


@pytest.mark.asyncio
async def test_labelled_fetch_uses_the_label_endpoint_and_sends_every_pair():
    """有标签 → 走商城的标签查询，把每一对都发出去，且**不在本地再筛一次**。

    本地筛要求每件商品带 `labels` 字段，而商城的列表/批量接口**不返回它**——本地筛
    会把命中商品全部滤成空，且是静默滤空。命中集合由商城给，那才是权威。
    """
    from app.service.mall_goods import fetch_goods

    hit = {"id": "spu-1", "name": "命中商品", "picUrls": ["p.jpg"], "priceDown": 10}
    response = MagicMock(status_code=200)
    response.json.return_value = {"code": 0, "data": [hit], "ok": True}
    with patch("app.service.mall_goods.httpx.AsyncClient.post", return_value=response) as post:
        result = await fetch_goods(settings=_settings(), labels=LABELS)

    assert result.reason is None
    assert [item.id for item in result.items] == ["spu-1"]
    # `picUrls` 归一成展示用的 image
    assert result.items[0].image == "p.jpg"

    sent = post.call_args
    assert sent.args[0].endswith("/mallapi/goodsspu/getGoodsByLabels")
    body = sent.kwargs["json"]
    assert body["goodsSpuLabels"] == [{"labelName": "慢病风险", "optionName": "血脂异常风险评估"}]
    # 并集：一件商品命中任一方向即算相关
    assert body["returnUnion"] is True
    assert body["sign"].isupper()


@pytest.mark.asyncio
async def test_labelled_fetch_reports_no_label_data_on_empty_hit():
    """标签查到了但没有商品挂 → no_label_data（不是 mall_unavailable）。"""
    from app.service.mall_goods import fetch_goods

    response = MagicMock(status_code=200)
    response.json.return_value = {"code": 0, "data": None, "ok": True}
    with patch("app.service.mall_goods.httpx.AsyncClient.post", return_value=response):
        result = await fetch_goods(settings=_settings(), labels=LABELS)

    assert result.items == ()
    assert result.reason == "no_label_data"


@pytest.mark.asyncio
async def test_labelled_fetch_never_falls_back_to_the_unfiltered_endpoint():
    """标签端点未配置时**不发请求**，而不是退回只读端点。

    只读端点不认标签，退回它会得到一个「没过滤」的完整商品列表，而按标签取货一旦
    失败就会把整页商品推给任意健康风险——比直接降级糟得多。
    """
    from app.service.mall_goods import fetch_goods

    with patch("app.service.mall_goods.httpx.AsyncClient.post") as post:
        result = await fetch_goods(settings=_settings(MALL_WEBAPI_LABELS_PATH=""), labels=LABELS)

    assert post.call_count == 0
    assert result == MallGoodsResult(items=(), reason="mall_unavailable")


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
    assert sent["headers"]["tenant-id"] == "tenant-under-test"
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
    owner_id = "account:goods-tenant:goods-user"
    with SessionLocal() as session:
        report = ReportModel(
            patient_id="P-goods",
            report_type="体检",
            status="assessed",
            owner_id=owner_id,
            evidence_result=evidence_result,
        )
        session.add(report)
        # #172：访问必须带主体会话（报告令牌旁路已移除）。
        token, session_row = issue_session(owner_id)
        session.add(session_row)
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


def _finding(condition_code: str) -> dict:
    return {"condition_code": condition_code, "condition_name": "血脂异常"}


def test_endpoint_returns_condition_reasons():
    with _report_client({"findings": [_finding("COND_DYSLIPIDEMIA")]}) as (client, report_id, cookie):
        result = MallGoodsResult(items=(), reason="no_label_data")
        with patch("app.api.report.fetch_goods", return_value=result) as fetch:
            response = client.get(
                f"/api/health/report/{report_id}/recommendations",
                cookies=cookie,
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
        _report_client({"findings": []}) as (client, report_id, cookie),
        patch("app.api.report.fetch_goods") as fetch,
    ):
        response = client.get(
            f"/api/health/report/{report_id}/recommendations",
            cookies=cookie,
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "reason": "no_published_card"}
    fetch.assert_not_called()


def test_endpoint_surfaces_mall_outage_as_reason_not_error():
    with (
        _report_client({"findings": [_finding("COND_DYSLIPIDEMIA")]}) as (client, report_id, cookie),
        patch(
            "app.api.report.fetch_goods",
            return_value=MallGoodsResult(items=(), reason="mall_unavailable"),
        ),
    ):
        response = client.get(
            f"/api/health/report/{report_id}/recommendations",
            cookies=cookie,
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
    with (
        _report_client({"findings": [_finding("COND_DYSLIPIDEMIA")]}) as (client, report_id, cookie),
        patch(
            "app.api.report.fetch_goods",
            return_value=MallGoodsResult(items=(hit,), reason=None),
        ),
    ):
        response = client.get(
            f"/api/health/report/{report_id}/recommendations",
            cookies=cookie,
        )

    body = response.json()
    assert body["reason"] is None
    assert body["items"][0]["price_down"] == "36.5"
    assert body["items"][0]["stock"] is None


def test_readiness_reports_unconfigured_mall_in_development(client_unused=None):
    """开发环境未配商城是合法形态：字段说 unconfigured，status 不因此 degraded。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with (
        patch(
            "app.main.get_settings",
            return_value=SimpleNamespace(
                mall_webapi_configured=False,
                APP_ENV="development",
                GENESIS_EVIDENCE_API_URL="http://127.0.0.1:8125/api/evidence/matches",
                GENESIS_EVIDENCE_API_KEY="x" * 32,
                VLLM_API_KEY="k",
                OPENAI_API_KEY="",
                llm_api_base="http://127.0.0.1:8000/v1",
                VLLM_MODEL="m",
                basic_auth_enabled=False,
                HEALTHFLOW_BASIC_USER="healthflow",
                HEALTHFLOW_BASIC_PASSWORD="",
                report_account_required=False,
                database_url="sqlite://",
            ),
        ),
        patch("app.main.get_mysql_client") as mysql,
    ):
        mysql.return_value.engine.connect.return_value.__enter__.return_value.execute.return_value = None
        body = TestClient(app).get("/ready").json()

    assert body["mall_goods"] == "unconfigured"
    assert body["status"] == "ready"


def test_readiness_flags_missing_mall_config_in_production():
    """生产环境未配商城是配置事故：字段说 missing，status 必须 degraded。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with (
        patch(
            "app.main.get_settings",
            return_value=SimpleNamespace(
                mall_webapi_configured=False,
                APP_ENV="production",
                GENESIS_EVIDENCE_API_URL="http://127.0.0.1:8125/api/evidence/matches",
                GENESIS_EVIDENCE_API_KEY="x" * 32,
                VLLM_API_KEY="k",
                OPENAI_API_KEY="",
                llm_api_base="http://127.0.0.1:8000/v1",
                VLLM_MODEL="m",
                basic_auth_enabled=False,
                HEALTHFLOW_BASIC_USER="healthflow",
                HEALTHFLOW_BASIC_PASSWORD="",
                report_account_required=False,
                database_url="sqlite://",
            ),
        ),
        patch("app.main.get_mysql_client") as mysql,
    ):
        mysql.return_value.engine.connect.return_value.__enter__.return_value.execute.return_value = None
        body = TestClient(app).get("/ready").json()

    assert body["mall_goods"] == "missing"
    assert body["status"] == "degraded"


def test_response_contract_matches_produced_reasons():
    """`Literal` 是契约：这里断言它正好是端点会产生的四个值。

    之前它多带一个后端永不产生的 `no_labels`，等于对外承诺存在第四种空态。
    这条断言让新增/删除原因时两侧必须同时改。
    """
    from typing import get_args

    from app.schema.report import RecommendationReason
    from app.service.mall_goods import Reason

    endpoint_only = {"no_published_card"}
    assert set(get_args(RecommendationReason)) == set(get_args(Reason)) | endpoint_only


def test_response_rejects_an_undeclared_reason():
    """契约之外的原因值必须校验失败，不能悄悄通过。"""
    from pydantic import ValidationError

    from app.schema.report import RecommendationResponse

    with pytest.raises(ValidationError):
        RecommendationResponse(items=[], reason="no_labels")


@pytest.mark.asyncio
async def test_non_utf8_body_degrades_instead_of_escaping():
    """非 UTF-8 body 也曾穿透到 500：`response.json()` 抛的 UnicodeDecodeError
    不是 `json.JSONDecodeError`，漏掉它页面就从一个"降级"变成一个"报错"。"""
    from unittest.mock import AsyncMock

    from app.service.mall_goods import fetch_goods

    response = MagicMock(status_code=200)
    response.json.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    with patch("app.service.mall_goods.httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
        result = await fetch_goods(settings=_settings(), labels=())

    assert result.reason == "mall_unavailable"
