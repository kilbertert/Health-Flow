"""跨源放行：只放配置里的源，不带凭据，暴露报告访问失败原因头（ADR 0026）。

商城 H5 在另一个源上调用本仓的 API。这是本仓第一次向浏览器暴露跨源接口，所以这里的
断言都是**暴露面**，不是便利：多放一个源，就是多一个能带令牌访问患者报告的页面；
`allow_credentials=true`，就是把「白名单配错」的后果从一个可撤销的字符串放大成一个
浏览器会**自动**附上的会话。

全部经 HTTP 层断言：预检响应与普通响应的头，就是浏览器实际看到的东西。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import ACCESS_REASON_HEADER
from app.config import get_settings
from app.main import app

#: 配置内的源。用配置里本来就有的两个开发源之一，不新造一个测试专用值。
ALLOWED_ORIGIN = "http://localhost:5173"
DISALLOWED_ORIGIN = "https://not-the-mall.example.com"


@pytest.fixture
def client():
    return TestClient(app)


def _preflight(client, origin, headers="authorization"):
    return client.options(
        "/api/auth/me",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": headers,
        },
    )


def test_a_preflight_from_a_configured_origin_allows_authorization(client):
    """预检必须放行 `Authorization` —— 否则 Bearer 令牌根本发不出去。"""
    response = _preflight(client, ALLOWED_ORIGIN)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    allowed = {h.strip().lower() for h in response.headers["access-control-allow-headers"].split(",")}
    assert "authorization" in allowed
    assert "content-type" in allowed


def test_a_preflight_from_an_unconfigured_origin_is_refused(client):
    """配置外的源不被放行。这一条是白名单的全部意义所在。"""
    response = _preflight(client, DISALLOWED_ORIGIN)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_the_allowed_origins_come_from_configuration_only(client, monkeypatch):
    """源的列表**只来自配置**：改配置即改放行集合，代码里没有第二份。

    负控是第二条断言：一个把商城源写死在代码里的实现，会在这里把新配的源漏掉。
    """
    monkeypatch.setenv("CORS_ORIGINS", "https://h5.example-mall-a.com,https://h5.example-mall-b.com")
    from app.config import Settings

    configured = Settings().cors_origins
    assert configured == ["https://h5.example-mall-a.com", "https://h5.example-mall-b.com"]

    # 运行中的中间件读的是同一个属性（启动时求值一次），所以这里验证的是「来源」而不是
    # 「每次请求重读配置」——前者才是要求。
    settings = get_settings()
    assert settings.cors_origins == [
        origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()
    ]


def test_the_reason_header_is_readable_cross_origin(client):
    """跨源响应必须暴露 `X-Report-Access-Reason`，否则前端读不到「会话失效」。"""
    response = client.get("/api/auth/me", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 401
    # 头在响应里，而且在 Expose-Headers 里 —— 后者才是浏览器肯把它交给 JavaScript 的原因。
    assert response.headers[ACCESS_REASON_HEADER] == "session_expired"
    exposed = {h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")}
    assert ACCESS_REASON_HEADER.lower() in exposed


def test_cross_origin_responses_do_not_allow_credentials(client):
    """`allow_credentials=false`：跨源不放凭据，Cookie 不会跟着跨源请求走。"""
    preflight = _preflight(client, ALLOWED_ORIGIN)
    simple = client.get("/api/auth/me", headers={"Origin": ALLOWED_ORIGIN})

    for response in (preflight, simple):
        assert "access-control-allow-credentials" not in response.headers


def test_the_cors_policy_is_not_wildcarded(client):
    """请求头不再是 `*`，源也不是 `*`。

    `*` 会让任何新加的自定义头自动获得跨源放行，也会让白名单失效 —— 两种都可以在没有
    任何测试变红的情况下发生，所以直接断言中间件的配置。
    """
    from starlette.middleware.cors import CORSMiddleware

    cors = next(
        middleware
        for middleware in app.user_middleware
        if middleware.cls is CORSMiddleware
    )
    assert cors.kwargs["allow_origins"] == get_settings().cors_origins
    assert "*" not in cors.kwargs["allow_headers"]
    assert set(cors.kwargs["allow_headers"]) == {"Authorization", "Content-Type"}
    assert cors.kwargs["allow_credentials"] is False
    assert ACCESS_REASON_HEADER in cors.kwargs["expose_headers"]
