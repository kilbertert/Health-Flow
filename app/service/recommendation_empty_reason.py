"""取货为空的原因（GLOSSARY.md 的「推荐为空的原因」）。

一次检测页取货（推荐列表）或加购（深链）**为什么没有给出东西**，此前有四份答案，三份在
服务端、一份只在浏览器里：

- **服务端契约只认三种**：`no_published_card`（本报告没有可据以取货的健康风险）、
  `no_label_data`（商城调用成功但按标签交集为空）、`mall_unavailable`（不可达/超时/业务
  错误/凭据未配置）。ADR-0005 第 3 点点名要求「为空时必须说明是哪一种空」。
- **客户端自造了第四种 `session_expired`**，并从**人话字符串**里用正则把它捞出来：
  `const expired = /\\b(401|404)\\b/.test(err.message || '')`。那条错误信息由
  `frontend/src/api.js` 的 `parseError` 拼成 —— 它把 HTTP 状态压成一句
  `请求失败（HTTP 404）`，或**被服务端的 `detail` 覆盖**。于是这个正则的命中与否取决于
  **服务端那句话里有没有恰好出现 3 位数字**，而不是取决于发生了什么。
- **同一个组件还伪造了一个服务端原因**：`reportId` 为空时直接 `reason: 'no_published_card'`
  —— 报告都还没打开，谈不上「没有风险」。
- **加购失败时，服务端已经给出的原因被丢掉**：`CartLinkResponse(url=None,
  reason="report_not_ready"|"link_unavailable")` 出域了，而前端只看 `link.url`；
  `url` 为空时一律说「暂时无法跳转到商城」。

本模块是那组取值的家。ADR-0005 把这个概念**写进了 ADR** 却**没有写进一个模块** —— 于是
服务端点内联判定、`app/service/mall_goods.py` 再声明一份子集、前端自造第三种口径。
本仓已经把「报告状态」「解读失败」「准入结论」逐一收敛成具名单点，取货为空是漏掉的那一处。

## 三组取值，不是一个词表的三种读法

- ``RECOMMENDATION_REASONS_ORDERED`` —— **取货端点**（`GET /report/{id}/recommendations`）
  可能给出的原因。它比商城模块能产生的多一个 `no_published_card`：那一条是**本报告自身的
  事实**，不调商城就能答（报告没有已确认的健康风险，就没有据以取货的标签）。
- ``CART_LINK_REASONS_ORDERED`` —— **加购端点**的。它是另一对取值，与取货那三个**不重叠**：
  `report_not_ready`（这份报告还没确认/评估）与 `link_unavailable`（深链构造不出来）。
  两条路问的是不同的问题（「有没有货」与「能不能跳过去」），共用一个词表会把两件事混成一件。
- ``REPORT_ACCESS_REASONS_ORDERED`` —— **报告访问**那一类：这份报告读不到（会话失效，
  或报告不存在/不属于你）。它**不是**「取货为空」的原因之一，而是一个**报告级访问问题**
  —— 患者侧要据此说「请从商城入口重新进入/这份报告打不开」，而不是「商城暂时不可用」。
  它由服务端在响应体里具名，而不是让前端从一句人话里猜 HTTP 状态。

三条路各自出域**自己**那一组。患者侧不允许产生服务端产生不了的取值 —— 这是本模块存在的
理由，也是它与 #226（解读失败不再按自由文本子串决定重试）同一形状的地方。
"""

from __future__ import annotations

from typing import Literal, get_args

# ── 唯一词表 ────────────────────────────────────────────────────────────────
#
# 元组而非 frozenset：`Literal[*X]` 的展开顺序应当稳定，而集合的迭代顺序不是。
RECOMMENDATION_REASONS_ORDERED: tuple[str, ...] = (
    # 本报告没有可据以取货的健康风险（不调商城）。
    "no_published_card",
    # 商城调用**成功**，但按标签交集为空。
    "no_label_data",
    # 商城不可达 / 超时 / 返回业务错误 / 凭据未配置。
    "mall_unavailable",
)

CART_LINK_REASONS_ORDERED: tuple[str, ...] = (
    # 这份报告还没确认或评估 —— 没有可据以取货的风险，谈不上加购。
    "report_not_ready",
    # 深链构造不出来（签名密钥未配置这类运维细节不向患者透出）。
    "link_unavailable",
)

REPORT_ACCESS_REASONS_ORDERED: tuple[str, ...] = (
    # 会话失效或没有会话。本仓**没有登录页**（#172），所以患者侧那句话必须指向商城入口。
    "session_expired",
    # 报告不存在，或不属于当前主体 —— 两者对患者是同一件事，不分别暴露。
    "report_not_found",
)

#: **商城模块能产生**的那个子集。`app/service/mall_goods.py` 从这里派生 `Reason`
#: （端点自己判定的 `no_published_card` 不在其中 —— 它不调商城）。
MALL_GOODS_REASONS_ORDERED: tuple[str, ...] = ("no_label_data", "mall_unavailable")

RECOMMENDATION_REASONS: frozenset[str] = frozenset(RECOMMENDATION_REASONS_ORDERED)
CART_LINK_REASONS: frozenset[str] = frozenset(CART_LINK_REASONS_ORDERED)
REPORT_ACCESS_REASONS: frozenset[str] = frozenset(REPORT_ACCESS_REASONS_ORDERED)

#: 出域契约用的 `Literal`。`Literal[*X]` 是 Python 3.11+ 的写法；CI 与部署都钉在 3.13。
RecommendationReasonLiteral = Literal[*RECOMMENDATION_REASONS_ORDERED]
CartLinkReasonLiteral = Literal[*CART_LINK_REASONS_ORDERED]
ReportAccessReasonLiteral = Literal[*REPORT_ACCESS_REASONS_ORDERED]


def vocabulary() -> frozenset[str]:
    """三条路的取值并集。供守卫使用：患者侧能说的取值必须落在这里面。"""
    return frozenset(get_args(RecommendationReasonLiteral)) | frozenset(
        get_args(CartLinkReasonLiteral)
    ) | frozenset(get_args(ReportAccessReasonLiteral))


#: 报告访问失败时的 HTTP 状态 → 具名原因。**服务端判定的唯一一处**。
#:
#: `401` 是「会话失效」（`session_owner_dependency` 与 `_require_owner` 都给这个状态）；
#: `404` 是「报告不存在或不属于你」（`_authorized_report` 三个分支都给这个状态，刻意不
#: 分别暴露）。其余状态不属于这一类。
REPORT_ACCESS_BY_STATUS: dict[int, str] = {
    401: "session_expired",
    404: "report_not_found",
}
