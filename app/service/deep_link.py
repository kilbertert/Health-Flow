"""加购深链的构造与签名（genesis-evidence #175 的签发端）。

契约见 genesis-evidence 的 `docs/deep-link-contract.md`。本模块只做两件事：
**构造 URL** 与**签名**。它**不**调用商城任何写接口、不持有购物车状态——那是商城的事。

一条必须守住的性质：载荷里没有任何秘密（`detection_id` 会随报告回显给患者、会被转发、
会进日志），所以**安全性完全来自签名，不来自不可猜测**。任何「参数猜不到所以不用签名」
的推理都是错的。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: 深链有效期。契约写死 10 分钟；商城侧按 `exp` 校验，本仓签发后无法撤回。
LINK_TTL_SECONDS = 600

#: 待签串里代表路径的那一项的键名。契约要求签名绑定入口路径，否则同一组参数与签名
#: 可以整体搬到商城的另一个入口。
PATH_SIGNING_KEY = "path"


class DeepLinkError(RuntimeError):
    """深链构造不出来。调用方据此在跳转前如实提示，而不是发一个注定被拒的链接。"""


@dataclass(frozen=True)
class DeepLink:
    url: str
    detection_id: str
    spu_id: str
    expires_at: int


def sign_payload(payload: dict[str, object], secret: str, *, path: str) -> str:
    """按契约签名：HMAC-SHA256，十六进制小写。

    待签串 = `path=<入口路径>` 之后接**除 `sig` 外的全部参数**按键名 ASCII 升序拼
    `k=v`，以 `&` 连接；**不做 URL 编码**（契约如此约定，商城侧按同一规则算）。
    值先 `trim`，空值不参与拼接。
    """
    if not secret:
        raise DeepLinkError("深链签名密钥未配置")

    pairs: list[tuple[str, str]] = [(PATH_SIGNING_KEY, path.strip())]
    pairs.extend(
        sorted(
            (name, str(value).strip())
            for name, value in payload.items()
            if name not in {"sig", PATH_SIGNING_KEY} and value is not None and str(value).strip()
        )
    )
    signing_input = "&".join(f"{name}={value}" for name, value in pairs)
    return hmac.new(secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).hexdigest()


def build_deep_link(
    *,
    spu_id: str,
    detection_id: str,
    quantity: int = 1,
    settings: Settings | None = None,
    now: int | None = None,
) -> DeepLink:
    """构造「加入购物车」深链。缺配置或参数不合法时抛 `DeepLinkError`。

    **不做「尽量拼一个能用的 URL」的降级**：一个签名不对或指向错入口的链接，用户点过去
    只会看到商城的拒绝页，比在本页直接说明更糟。
    """
    settings = settings or get_settings()
    base = settings.MALL_STOREFRONT_BASE_URL.strip().rstrip("/")
    route = settings.MALL_STOREFRONT_CART_PATH.strip()
    secret = settings.MALL_DEEP_LINK_SECRET.strip()

    if not base:
        raise DeepLinkError("商城前台地址未配置")
    if not route:
        # H5 的外层路径**不猜**：契约只确定了详情页在 H5 内部的路由名，外层前缀
        # （是否带 /h5、是否用 history 模式）是部署事实，本仓读代码读不出来。
        raise DeepLinkError("加购入口路径未配置")
    if not secret:
        raise DeepLinkError("深链签名密钥未配置")
    if not spu_id.strip():
        raise DeepLinkError("缺少商品标识")
    if not detection_id.strip():
        raise DeepLinkError("缺少检测标识")
    if quantity < 1:
        raise DeepLinkError("数量必须为正整数")

    expires_at = int(now if now is not None else time.time()) + LINK_TTL_SECONDS
    payload: dict[str, object] = {
        "tenant_id": settings.MALL_WEBAPI_TENANT_ID.strip(),
        "spu_id": spu_id.strip(),
        "quantity": quantity,
        "detection_id": detection_id.strip(),
        "exp": expires_at,
    }
    signature = sign_payload(payload, secret, path=route)
    query = urlencode({**payload, "sig": signature})
    return DeepLink(
        url=f"{base}{route}?{query}",
        detection_id=str(payload["detection_id"]),
        spu_id=str(payload["spu_id"]),
        expires_at=expires_at,
    )


def _demo() -> None:
    """自检：契约里的两个性质。`python -m app.service.health_deep_link`。"""
    from types import SimpleNamespace

    settings = SimpleNamespace(
        MALL_STOREFRONT_BASE_URL="https://EXAMPLE.invalid",
        MALL_STOREFRONT_CART_PATH="/shopPackage/pages/goods/goods-detail/index",
        MALL_DEEP_LINK_SECRET="k",
        MALL_WEBAPI_TENANT_ID="tenant-from-config",
    )
    link = build_deep_link(spu_id="spu-1", detection_id="det-1", settings=settings, now=1_000_000)

    # 1) 签名绑定路径：同一组参数换一个入口，签名必须不同。
    same = {
        "tenant_id": "tenant-from-config",
        "spu_id": "spu-1",
        "quantity": 1,
        "detection_id": "det-1",
        "exp": 1_000_600,
    }
    assert sign_payload(same, "k", path="/a") != sign_payload(same, "k", path="/b")

    # 2) 改任一参数，签名必须不同（载荷不是秘密，防篡改只能靠签名）。
    tampered = dict(same, quantity=2)
    assert sign_payload(same, "k", path="/a") != sign_payload(tampered, "k", path="/a")

    # 3) 缺配置必须抛错，不降级成「拼一个能用的链接」。
    for field in ("MALL_STOREFRONT_BASE_URL", "MALL_STOREFRONT_CART_PATH", "MALL_DEEP_LINK_SECRET"):
        broken = SimpleNamespace(**{**settings.__dict__, field: ""})
        try:
            build_deep_link(spu_id="s", detection_id="d", settings=broken)
        except DeepLinkError:
            pass
        else:
            raise AssertionError(f"{field} 缺失时应拒绝构造")
    assert link.url.startswith("https://EXAMPLE.invalid/")
    print("deep_link self-check ok")


if __name__ == "__main__":
    _demo()
