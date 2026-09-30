"""服务端只读读商城商品（genesis-evidence #167 的第三方只读端点）。

这条通路必须走服务端：商城网关的 CORS 头组合不合规（`Allow-Origin: *` 与
`Allow-Credentials: true` 同时出现，规范禁止），浏览器会拒绝该凭据请求，
且允许的请求头里不含租户与会话头。因此**浏览器不得直接调商城**，商品数据
由本服务取回、同源转发给前端。

边界（ADR 0006）：本服务不自持商品、不做商品可售性判断、不写商城任何状态。
可售性（审核通过 + 已上架 + 租户/门店归属）由商城在自己的端点里过滤，
本模块只负责搬运，不在其结果之上再加第二道闸门。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# 空标签集：今天商城端点没有标签入参，且金丝雀租户没有任何商品标签数据。
# 这里的空是**事实的空**，不是未实现的空——见 docs/adr/0005 与 qa-plan.md
# 中未通过的那条用例。标签数据与端点入参就绪后，本表由 semantic mapping 填充。
EMPTY_LABELS: tuple[tuple[str, str], ...] = ()

# 调用成功但没货可给 / 压根没调到商城。两者对运维的含义不同，不得合并。
# 这是响应契约词汇表（`app.schema.report.RecommendationReason`）里本模块**能产生**的
# 子集；第四个值 `no_published_card` 由端点判定（报告本身没有风险，不调商城）。
# 两侧一致由 `tests/test_mall_goods.py` 断言，避免多写一份枚举后各自漂移。
Reason = Literal["no_label_data", "mall_unavailable"]


class MallGoodsError(RuntimeError):
    """商城商品读取失败。调用方据此降级，不向患者暴露技术细节。"""


class MallGoodsItem(BaseModel):
    """商城返回的展示字段。刻意不整包承接实体：商城端点本身也只暴露这些。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = ""
    image: str | None = None
    price_down: Decimal | None = None
    price_up: Decimal | None = None
    # 实测（2026-09-29，金丝雀租户 20 条）该字段全为 null。null 表示"商城未标注"，
    # **不是 0**——渲染成 0 或"缺货"都是伪造。
    stock: int | None = None
    shop_id: str | None = None
    # 商品挂的（标签名，标签值）对。**商城端点今天不返回它**，因此恒为空；
    # 它在这里是因为按标签取货必须有一个可比的判据，而判据一旦存在就必须是
    # 商品自己的属性（不是本地维护的一张对应表）。端点加上该字段后自动生效。
    labels: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class MallGoodsResult:
    """一次读取的结果：条目 + 为空的原因（有条目时原因为 None）。"""

    items: tuple[MallGoodsItem, ...]
    reason: Reason | None


def sign_payload(payload: dict[str, object], key: str) -> str:
    """按接入面既有约定签名：MD5 两次，**两层都是十六进制大写**。

    串的构成：去掉 `sign` 后按键名升序拼 `k=v&`（值 str 化后 trim、跳过空值），
    末尾接 `appSecret=<key>`；外层是 `md5(内层大写十六进制 + key)`。

    内层大小写是这里唯一的坑：`WebApiSignUtils` 的 `md5()` 返回 `Hex.encodeHexString(...).toUpperCase()`，
    两次都大写。内层用小写会被服务端判"签名错误"（实测复现）。
    """
    pairs = sorted(
        (name, str(value).strip())
        for name, value in payload.items()
        if name != "sign" and value is not None and str(value).strip()
    )
    joined = "".join(f"{name}={value}&" for name, value in pairs) + f"appSecret={key}"
    inner = hashlib.md5(joined.encode("utf-8")).hexdigest().upper()
    return hashlib.md5((inner + key).encode("utf-8")).hexdigest().upper()


def parse_items(payload: object) -> tuple[MallGoodsItem, ...]:
    """把商城响应体解析成展示条目。结构不符即失败，不猜。

    **只有 `data` 这个键存在且为 `null` 才算空命中。** 键缺失或整个响应不是对象，
    都是「读不懂的响应」——把它当空命中会把一次契约破坏伪装成「这家没上货」，
    运维会去催租户上货，而真正坏的是接口。区分点在于**键在不在**，不是值是不是 null。
    """
    if not isinstance(payload, dict) or "data" not in payload:
        raise MallGoodsError("商城返回格式无效")
    records = payload["data"]
    if records is None:
        return ()
    if not isinstance(records, list):
        raise MallGoodsError("商城返回格式无效")
    items: list[MallGoodsItem] = []
    for record in records:
        if not isinstance(record, dict):
            raise MallGoodsError("商城返回格式无效")
        try:
            items.append(
                MallGoodsItem(
                    id=str(record.get("id", "")),
                    name=str(record.get("name") or ""),
                    image=first_image(record),
                    price_down=record.get("priceDown"),
                    price_up=record.get("priceUp"),
                    stock=record.get("stock"),
                    shop_id=record.get("shopId"),
                    labels=parse_labels(record),
                )
            )
        except ValidationError as exc:
            raise MallGoodsError("商城返回格式无效") from exc
    return tuple(items)


def first_image(record: dict[str, object]) -> str | None:
    """商品主图。两个来源、两种形状，按优先级取第一个非空值。

    只读端点返回拼好的 `image` 字符串；按标签取货返回的是 `picUrls` 数组。
    两个来源必须得出同一个字段，否则同一件商品在两条路径上会有两种渲染结果。
    """
    image = record.get("image")
    if isinstance(image, str) and image.strip():
        return image
    pic_urls = record.get("picUrls")
    if isinstance(pic_urls, list):
        for candidate in pic_urls:
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return None


def parse_labels(record: dict[str, object]) -> frozenset[tuple[str, str]]:
    """取该商品挂的（标签名，标签值）对。

    商城端点今天不返回这个字段，所以现实里恒为空集——**这是数据的空，不是解析的失败**。
    字段出现后（形如 `[{labelName, optionName}]`）无需改这里。
    """
    raw = record.get("labels") or record.get("goodsSpuLabelList")
    if not isinstance(raw, list):
        return frozenset()
    pairs = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("labelName")
        value = entry.get("optionName")
        if name and value:
            pairs.add((str(name), str(value)))
    return frozenset(pairs)


def label_pairs_for(condition_code: str) -> tuple[tuple[str, str], ...]:
    """`condition_code` → 商城（标签名，标签值）对。

    这是**显式的接缝**：商城侧今天只返回该租户全部可售商品，没有标签维度
    （`WebApiReadGoodsRequest` 只有 shopId/current/size），且金丝雀租户没有任何
    商品标签数据，所以这里如实返回空元组。空 → 过滤后为空 → 原因 `no_label_data`。

    标签数据与端点入参到位后，这里接**语义映射**的取值即可。那份映射是
    genesis-evidence 的交付物（`docs/condition-to-mall-tag.md`，交付给商城、由商城把
    标签打到商品上），**不在本仓**；本仓只消费它的结果，不复制一份——复制就会漂移。
    """
    return EMPTY_LABELS


def filter_by_labels(
    items: tuple[MallGoodsItem, ...],
    labels: tuple[tuple[str, str], ...],
) -> tuple[MallGoodsItem, ...]:
    """按标签交集取货：只保留命中任一 (标签名, 标签值) 的商品。

    判据是**商品自己的 `labels`**，不是本地维护的一张商品-标签对应表——后者是商城的
    数据在本仓的第二份副本，必然漂移。商城端点今天不返回该字段，于是每件商品的
    `labels` 都是空集，交集因此为空；**这个空由数据产生，不由函数忽略输入产生**，
    端点补上字段后本函数无需修改即生效。

    无标签可查时直接返回空，不退回"全都要"：把该租户全部商品推给任意健康风险，
    正是这道闸门要防的事。
    """
    wanted = set(labels)
    if not wanted:
        return ()
    return tuple(item for item in items if item.labels & wanted)


def _labels_request(settings: Settings, wanted: tuple[tuple[str, str], ...]) -> tuple[str, dict[str, object]]:
    """按标签取货的 (路径, 请求体)。

    路径来自 `MALL_WEBAPI_LABELS_PATH`，**没有兜底**：这条请求体只有标签接口能读，
    发给只读端点会得到一个「没有按标签过滤」的完整商品列表，而那会被当成命中集合
    返回给患者——比报错更糟。缺配置时调用方直接降级，不发这个请求。
    """
    path = settings.MALL_WEBAPI_LABELS_PATH.strip()
    if not path:
        return "", {}
    body: dict[str, object] = {
        "appId": settings.MALL_WEBAPI_APP_ID.strip(),
        "timeStamp": int(time.time() * 1000),
        "goodsSpuLabels": [{"labelName": name, "optionName": value} for name, value in wanted],
        # 并集：一件商品命中任一健康方向即算相关。交集会把「同时挂多个方向」的商品
        # 排除掉，那不是我们要的语义。
        "returnUnion": True,
    }
    body["sign"] = sign_payload(body, settings.MALL_WEBAPI_APP_SECRET.strip())
    return path, body


def _goods_request(settings: Settings) -> tuple[str, dict[str, object]]:
    """无标签时的 (路径, 请求体)：#167 那条只读端点，行为一字不变。"""
    body: dict[str, object] = {
        "appId": settings.MALL_WEBAPI_APP_ID.strip(),
        "timeStamp": int(time.time() * 1000),
        "current": 1,
        "size": settings.MALL_WEBAPI_PAGE_SIZE,
    }
    body["sign"] = sign_payload(body, settings.MALL_WEBAPI_APP_SECRET.strip())
    return settings.MALL_WEBAPI_GOODS_PATH, body


async def fetch_goods(
    *,
    settings: Settings | None = None,
    labels: tuple[tuple[str, str], ...] | None = None,
) -> MallGoodsResult:
    """取该租户可售商品，有标签判据时按标签取货。始终调用商城，失败与为空分别归因。

    未配置商城凭据时**不发请求**：空密钥签出来的请求必然被拒，发出去只是把一次
    配置缺失伪装成一次网络失败。

    两条路径的取舍：给了标签就走商城的标签查询（命中集合由**商城**给出，即权威），
    没给标签仍走 #167 的只读端点并在本地按 `item.labels` 过滤（历史上就是这么验收的，
    不改它）。标签查询**只认标签、不认分页**，返回的是命中集合本身，没有截断问题。
    """
    settings = settings or get_settings()
    if not settings.mall_webapi_configured:
        logger.warning("商城只读端点未配置（缺少 base url / app id / secret / tenant id），降级为无推荐")
        return MallGoodsResult(items=(), reason="mall_unavailable")

    wanted = tuple(labels or EMPTY_LABELS)
    if wanted:
        path, body = _labels_request(settings, wanted)
        if not path:
            # 标签端点未配置：**不发**。发给只读端点会得到「没按标签过滤」的完整商品
            # 列表，而那会被当成命中集合——静默地把不相关商品推给患者。
            logger.warning("按标签取货未配置（MALL_WEBAPI_LABELS_PATH 为空），降级为无推荐")
            return MallGoodsResult(items=(), reason="mall_unavailable")
    else:
        path, body = _goods_request(settings)

    url = settings.MALL_WEBAPI_BASE_URL.strip().rstrip("/") + path
    headers = {
        "Content-Type": "application/json",
        "tenant-id": settings.MALL_WEBAPI_TENANT_ID.strip(),
        "mall-app-id": settings.MALL_WEBAPI_APP_ID.strip(),
    }
    try:
        async with httpx.AsyncClient(timeout=settings.MALL_WEBAPI_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("商城只读端点不可达：%s", type(exc).__name__)
        return MallGoodsResult(items=(), reason="mall_unavailable")

    if response.status_code != 200:
        logger.warning("商城只读端点返回 HTTP %s", response.status_code)
        return MallGoodsResult(items=(), reason="mall_unavailable")
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 非 UTF-8 字节同样走降级：这是"商城返回了读不懂的东西"，不是本服务的错误。
        # 漏掉 UnicodeDecodeError 会让它穿透到 500，而不是返回"暂无推荐"。
        logger.warning("商城只读端点返回非 JSON")
        return MallGoodsResult(items=(), reason="mall_unavailable")

    code = payload.get("code") if isinstance(payload, dict) else None
    if code != 0:
        # 商城把签名/租户/应用类错误放在 code + msg 里，均为 200。这里只记 code，
        # 不记 msg（msg 可能带调用参数），也不把细节透给患者。
        logger.warning("商城只读端点返回业务错误 code=%s", code)
        return MallGoodsResult(items=(), reason="mall_unavailable")

    try:
        items = parse_items(payload)
    except MallGoodsError:
        logger.warning("商城只读端点返回结构无法解析")
        return MallGoodsResult(items=(), reason="mall_unavailable")

    # **带标签查询时不再本地筛。** 命中集合由商城的标签查询给出，那是权威；本地那份
    # 判据要求每件商品都带 `labels` 字段，而事实是**商品的列表/批量接口不带它**
    # （只有单品详情带），本地筛会把命中商品全部滤成空——而且是静默滤空。
    # 无标签路径保持既有语义：本地按商品自带的标签交集过滤。
    filtered = items if wanted else filter_by_labels(items, wanted)

    # 调用成功之后的每一种"空"都是 no_label_data：可能是没有标签可查（映射还没建），
    # 也可能是标签查到了但商品没挂（含挂着但已下架）——两者的修复动作都在商城侧，
    # 对患者是同一种沉默。真正要区分的是**没调到**商城（mall_unavailable）。
    if not filtered:
        return MallGoodsResult(items=(), reason="no_label_data")
    return MallGoodsResult(items=filtered, reason=None)


def serialized(items: tuple[MallGoodsItem, ...]) -> list[dict[str, object]]:
    """同源响应体。价格用字符串传出，避免二进制浮点在 JSON 里抖动。"""
    return [
        {
            "id": item.id,
            "name": item.name,
            "image": item.image,
            "price_down": str(item.price_down) if item.price_down is not None else None,
            "price_up": str(item.price_up) if item.price_up is not None else None,
            "stock": item.stock,
            "shop_id": item.shop_id,
        }
        for item in items
    ]


def _demo() -> None:
    """自检：签名大小写与过滤为空的行为。`python -m app.service.mall_goods`。"""
    # 大小写是本模块最容易写错的一处：内层小写会被商城判"签名错误"。
    assert (
        sign_payload({"appId": "a", "timeStamp": 1}, "k")
        == hashlib.md5((hashlib.md5(b"appId=a&timeStamp=1&appSecret=k").hexdigest().upper() + "k").encode())
        .hexdigest()
        .upper()
    )
    # 没有标签判据 → 过滤恒为空，且这个空属于"调用成功但没货"，不是"商城不可达"。
    assert filter_by_labels((MallGoodsItem(id="1"),), ()) == ()
    assert MallGoodsResult(items=(), reason="no_label_data").reason == "no_label_data"
    print("mall_goods self-check ok")


if __name__ == "__main__":
    _demo()
