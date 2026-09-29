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

    待签串 = **除 `sig` 外的全部参与项**（含 `path`）按键名 **ASCII 升序**拼 `k=v`，
    以 `&` 连接；**不做 URL 编码**（契约如此约定，商城侧按同一规则算）。
    值先 `trim`，空值不参与拼接。

    **`path` 参与统一升序，不特殊放在最前。** 契约的字面是「全部参数按键名升序」，
    而商城侧只会按契约实现——本仓若把 `path` 单独提到最前，两边算出的串不同，
    **每一次签名都会被判不匹配**。契约是给对方的承诺，实现要符合它，不是反过来。
    """
    if not secret:
        raise DeepLinkError("深链签名密钥未配置")

    pairs = sorted(
        (name, str(value).strip())
        for name, value in {**payload, PATH_SIGNING_KEY: path}.items()
        if name != "sig" and value is not None and str(value).strip()
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
    tenant = settings.MALL_WEBAPI_TENANT_ID.strip()

    if not base:
        raise DeepLinkError("商城前台地址未配置")
    if not route:
        # H5 的外层路径**不猜**：契约只确定了详情页在 H5 内部的路由名，外层前缀
        # （是否带 /h5、是否用 history 模式）是部署事实，本仓读代码读不出来。
        raise DeepLinkError("加购入口路径未配置")
    if not secret:
        raise DeepLinkError("深链签名密钥未配置")
    if not tenant:
        # 空租户会签出 `tenant_id=`：商城侧要么拒绝，要么把它当成某个默认租户——
        # 后者更糟，因为那意味着「跳到了别人的店」。与其它几项同一种处理：拒绝构造。
        raise DeepLinkError("租户标识未配置")
    if not spu_id.strip():
        raise DeepLinkError("缺少商品标识")
    if not detection_id.strip():
        raise DeepLinkError("缺少检测标识")
    if quantity < 1:
        raise DeepLinkError("数量必须为正整数")

    expires_at = int(now if now is not None else time.time()) + LINK_TTL_SECONDS
    payload: dict[str, object] = {
        "tenant_id": tenant,
        # 契约参数：商城侧要验签的就是这些。
        "spu_id": spu_id.strip(),
        # 页面级别名：商城 H5 的商品详情页只认 `id`，不认 `spu_id`。**两个都带**——
        # 少了 `id`，落到详情页会是一个打不开商品的空页面（这是实测过的行为差异，
        # 不是猜测）；少了 `spu_id`，则与已交付的契约不一致。
        # 它同样参与签名（契约规则是「除 sig 外的全部参数」），所以两边算得一致。
        "id": spu_id.strip(),
        "quantity": quantity,
        "detection_id": detection_id.strip(),
        "exp": expires_at,
    }
    signature = sign_payload(payload, secret, path=route)
    # 查询串按**签名键名升序**排列，与待签串同序——不是为了美观，而是让
    # 「收到的 URL」与「被签的串」肉眼可比，排障时不必再心算一遍顺序。
    query = urlencode({**dict(sorted(payload.items())), "sig": signature})
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

    # 0) 固定向量：按契约字面的升序手算一遍，锁住串的构成与顺序。
    #    这是发现「顺序错」的唯一手段——只断言「不同 path → 不同签名」看不出顺序。
    fixed = {"spu_id": "spu-1", "quantity": 1, "detection_id": "det-1", "exp": 1000600,
             "tenant_id": "t"}
    expected = hmac.new(b"k", (
        b"detection_id=det-1&exp=1000600&path=/p&quantity=1&spu_id=spu-1&tenant_id=t"
    ), hashlib.sha256).hexdigest()
    assert sign_payload(fixed, "k", path="/p") == expected, sign_payload(fixed, "k", path="/p")

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
    for field in ("MALL_STOREFRONT_BASE_URL", "MALL_STOREFRONT_CART_PATH", "MALL_DEEP_LINK_SECRET",
                  "MALL_WEBAPI_TENANT_ID"):
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
