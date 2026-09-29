"""#175 加购深链：构造、签名与失败口径。

契约见 genesis-evidence 的 `docs/deep-link-contract.md`。本模块只构造 URL 与签名，
**不调用商城任何写接口、不持有购物车状态**。

最关键的一条性质：载荷里没有秘密（`detection_id` 会随报告回显给患者），所以
**安全性完全来自签名**。这里的用例都要在「参数可猜」的前提下证明篡改会被发现。
"""

import hashlib
import hmac
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base
from app.data.models import MedicalReport as ReportModel
from app.service.deep_link import LINK_TTL_SECONDS, DeepLinkError, build_deep_link, sign_payload
from app.service.report_ownership import UNOWNED_SENTINEL  # noqa: F401
from app.service.sessions import SESSION_COOKIE, issue_session

BASE = "https://storefront.example.test"
ROUTE = "/shopPackage/pages/goods/goods-detail/index"
SECRET = "deep-link-secret"
TENANT = "tenant-under-test"


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "MALL_STOREFRONT_BASE_URL": BASE,
        "MALL_STOREFRONT_CART_PATH": ROUTE,
        "MALL_DEEP_LINK_SECRET": SECRET,
        "MALL_WEBAPI_TENANT_ID": TENANT,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _verify(url_query: dict[str, str], secret: str, path: str = ROUTE) -> bool:
    """按契约在「商城侧」复算签名，用来独立验证签发端。

    **这里刻意按契约字面写：「除 sig 外的全部参数按键名升序」，`path` 不特殊化。**
    如果签发端把 `path` 单独提到最前，这里就会算出不同的串——这正是要测出来的。
    """
    supplied = url_query["sig"]
    items = {k: v for k, v in url_query.items() if k != "sig"}
    items["path"] = path
    signing_input = "&".join(f"{k}={items[k]}" for k in sorted(items))
    expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def _query(url: str) -> dict[str, str]:
    from urllib.parse import parse_qs, urlparse

    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_link_carries_the_contracted_parameters():
    link = build_deep_link(spu_id="spu-1", detection_id="det-1", settings=_settings(), now=1_000_000)
    query = _query(link.url)

    assert link.url.startswith(f"{BASE}{ROUTE}?")
    assert query["spu_id"] == "spu-1"
    assert query["detection_id"] == "det-1"
    assert query["quantity"] == "1"
    assert query["tenant_id"] == TENANT
    assert query["exp"] == str(1_000_000 + LINK_TTL_SECONDS)
    assert query["sig"].islower()


def test_signature_verifies_against_an_independent_recomputation():
    """用商城侧的算法独立复算一遍——签发端与契约一致不是「自己证明自己」。"""
    link = build_deep_link(spu_id="spu-1", detection_id="det-1", settings=_settings(), now=1_000_000)

    assert _verify(_query(link.url), SECRET)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("spu_id", "spu-2"),
        ("detection_id", "det-2"),
        ("quantity", "9"),
        ("tenant_id", "999"),
        ("exp", "9999999999"),
    ],
)
def test_tampering_any_parameter_breaks_the_signature(field, value):
    """载荷不是秘密——`detection_id` 会回显、会转发、会进日志，所以防篡改只能靠签名。"""
    link = build_deep_link(spu_id="spu-1", detection_id="det-1", settings=_settings(), now=1_000_000)
    query = _query(link.url)
    query[field] = value

    assert not _verify(query, SECRET)


def test_signature_is_bound_to_the_entry_path():
    """同一组参数换一个入口，签名必须不再成立——否则票据可随参数搬到别的入口。"""
    link = build_deep_link(spu_id="spu-1", detection_id="det-1", settings=_settings(), now=1_000_000)

    assert _verify(_query(link.url), SECRET)
    assert not _verify(_query(link.url), SECRET, path="/another/entry")


def test_signature_uses_a_different_secret_than_the_mall_read_endpoint():
    """契约要求这两把密钥分开：一个泄漏不该让另一个也失效。"""
    link = build_deep_link(spu_id="spu-1", detection_id="det-1", settings=_settings(), now=1_000_000)
    query = _query(link.url)

    assert not _verify(query, "the-read-endpoint-appsecret")


def test_empty_values_do_not_participate_in_signing():
    base = {"spu_id": "spu-1", "quantity": "1"}
    assert sign_payload(base, SECRET, path=ROUTE) == sign_payload(
        {**base, "note": "", "other": None}, SECRET, path=ROUTE
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"MALL_STOREFRONT_BASE_URL": ""},
        {"MALL_STOREFRONT_CART_PATH": ""},
        {"MALL_DEEP_LINK_SECRET": ""},
        # 空租户会签出 `tenant_id=`：商城侧要么拒绝，要么把它当成某个默认租户
        # —— 后者意味着「跳到了别人的店」。与其它几项同一种处理。
        {"MALL_WEBAPI_TENANT_ID": ""},
    ],
)
def test_missing_configuration_refuses_to_build_instead_of_guessing(overrides):
    """缺配置就拒绝构造。一个指向错入口或签名不对的链接，比在本页直接说明更糟。"""
    with pytest.raises(DeepLinkError):
        build_deep_link(spu_id="spu-1", detection_id="det-1", settings=_settings(**overrides))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"spu_id": "", "detection_id": "d"}, "商品标识"),
        ({"spu_id": "s", "detection_id": ""}, "检测标识"),
        ({"spu_id": "s", "detection_id": "d", "quantity": 0}, "数量"),
    ],
)
def test_invalid_arguments_are_rejected(kwargs, match):
    with pytest.raises(DeepLinkError, match=match):
        build_deep_link(settings=_settings(), **kwargs)


# --- 同源端点 ---------------------------------------------------------------


def _client(evidence_result, status="assessed"):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        owner_id = "account:link-tenant:link-user"
        report = ReportModel(
            patient_id="P-link",
            report_type="体检",
            status=status,
            owner_id=owner_id,
            evidence_result=evidence_result,
        )
        session.add(report)
        # #172：报告令牌旁路已移除，访问必须带主体会话。
        token, session_row = issue_session(owner_id)
        session.add(session_row)
        session.commit()
        report_id = report.id

    def override_get_db():
        with SessionLocal() as session:
            yield session

    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def ctx():
        with (
            patch("app.data.get_db", override_get_db),
            patch("app.api.report.resolve_owner", return_value=SimpleNamespace(storage_id=owner_id)),
        ):
            from app.main import app

            yield TestClient(app)

    return report_id, ctx(), {SESSION_COOKIE: token}


def test_endpoint_returns_a_signed_link_for_a_completed_report():
    from unittest.mock import patch

    report_id, client_ctx, cookie = _client({"findings": [{"condition_code": "COND_DYSLIPIDEMIA"}]})
    with client_ctx as client, patch(
        "app.api.report.build_deep_link",
        return_value=SimpleNamespace(url=f"{BASE}{ROUTE}?sig=abc"),
    ):
        response = client.post(
            f"/api/health/report/{report_id}/recommendations/spu-1/cart-link",
            cookies=cookie,
        )

    assert response.status_code == 200
    assert response.json() == {"url": f"{BASE}{ROUTE}?sig=abc", "reason": None}


def test_endpoint_refuses_for_a_report_that_is_not_ready():
    """未完成确认/评估的报告没有可据以取货的风险，也就没有加购可言。"""
    report_id, client_ctx, cookie = _client(None, status="pending_confirmation")
    with client_ctx as client:
        response = client.post(
            f"/api/health/report/{report_id}/recommendations/spu-1/cart-link",
            cookies=cookie,
        )

    assert response.status_code == 200
    assert response.json() == {"url": None, "reason": "report_not_ready"}


def test_endpoint_reports_an_unbuildable_link_instead_of_returning_a_broken_one():
    """构造不出来时如实说明，**不**回一个注定被商城拒绝的链接。"""
    from unittest.mock import patch

    from app.service.deep_link import DeepLinkError as Err

    report_id, client_ctx, cookie = _client({"findings": [{"condition_code": "COND_DYSLIPIDEMIA"}]})
    with client_ctx as client, patch("app.api.report.build_deep_link", side_effect=Err("未配置")):
        response = client.post(
            f"/api/health/report/{report_id}/recommendations/spu-1/cart-link",
            cookies=cookie,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["url"] is None
    assert body["reason"] == "link_unavailable"


# --- review 修正：签名顺序与落点 ---------------------------------------------


def test_signature_order_matches_the_contract_literal():
    """**固定向量**：给定输入，签名必须是这个确定的十六进制串。

    这是发现「顺序错」的唯一手段——`_verify` 是按契约字面（全部按键名升序、`path`
    不特殊化）写的，而只断言「不同 path → 不同签名」看不出顺序差别。契约已合并进
    genesis 的 `main`，商城侧只会按它的字面实现；本仓把 `path` 单独提到最前的话，
    **每一次签名都会被判不匹配**。
    """
    payload = {"tenant_id": "t", "spu_id": "spu-1", "id": "spu-1",
               "quantity": 1, "detection_id": "det-1", "exp": 1000600}
    signing_input = "detection_id=det-1&exp=1000600&id=spu-1&path=/p&quantity=1&spu_id=spu-1&tenant_id=t"
    expected = hmac.new(b"k", signing_input.encode(), hashlib.sha256).hexdigest()

    assert sign_payload(payload, "k", path="/p") == expected


def test_link_carries_both_spu_id_and_the_page_alias_id():
    """契约参数 `spu_id` 与页面参数 `id` 都要在。

    H5 商品详情页只认 `id`；只带 `spu_id` 会落到一个**打不开商品的空页面**
    （实测过的行为差异）。而 `spu_id` 是已交付契约的载荷，商城侧要验的就是它。
    """
    link = build_deep_link(spu_id="spu-9", detection_id="det-1", settings=_settings(), now=1_000_000)
    query = _query(link.url)

    assert query["spu_id"] == "spu-9"
    assert query["id"] == "spu-9"
    assert _verify(query, SECRET)


def test_query_string_is_ordered_like_the_signed_string():
    """查询串按签名键名升序，与待签串同序——排障时肉眼可比，不必心算顺序。"""
    link = build_deep_link(spu_id="spu-1", detection_id="det-1", settings=_settings(), now=1_000_000)
    keys = list(_query(link.url))

    assert keys[:-1] == sorted(keys[:-1])
    assert keys[-1] == "sig"
