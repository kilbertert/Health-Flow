"""健康数据同意的判定与写入（GLOSSARY.md 的「健康数据同意」，#269）。

词表在 `app/service/consent_vocabulary.py`（只有声明、不 import 任何项目模块）。本模块
在它之上放**唯一**的写入入口与唯一的状态投影：

- ``record_consent`` —— 写一条表态。只追加，从不更新或删除既有行：撤回是又一条记录。
- ``states`` —— 当前状态。每项取**最新一条**，并要求它的文本版本等于**当前要求的版本**。
- ``has_valid`` —— 门槛问的那一个问题（上传要「报告解读」，推荐要「商品推荐」）。

## 有效 = 最新一条是同意，且写在当前那一版文本上

两件事必须同时成立，缺一不可：

- 最新一条是 `granted`。``revoked`` 之后当前状态就是无效 —— 不是「还有一条更早的同意」。
- 那条记录上的 `text_version` 等于配置里的当前版本。文本变了，旧表态**不再是对当前文本
  的态度**，患者要被重新征求。这也在同一条判据里，所以「状态读取」与「上传闸门」
  不可能各说各话。

最新一条的行决定一切，包括它在旧版本上：**不是**「最近一条有效版本的记录」。后者会让
「他在新版本文本下撤回过」被一条更早的旧版同意悄悄盖过去。

## 文本版本由服务端写定，不由客户端声明

请求里没有版本字段（见 `app/schema/consent.py`）：患者表的是「对现在给他看的那一版」的
态度，而那一版是什么由服务端在写入那一刻定。让客户端带版本，等于让它可以声称自己对
一版从未展示过的文本表过态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.data.models import HealthDataConsent
from app.service.consent_vocabulary import (
    CONSENT_DECISIONS,
    CONSENT_ITEMS,
    CONSENT_ITEMS_ORDERED,
    CONSENT_SOURCES,
)

#: 每一项当前要求的文本版本从哪读。配置按项分开是刻意的：法务改一版「报告解读」的文本
#: 不该顺带作废患者对「商品推荐」的表态，反之亦然。
_REQUIRED_VERSION_SETTING = {
    "report_interpretation": "CONSENT_REPORT_INTERPRETATION_VERSION",
    "product_recommendation": "CONSENT_PRODUCT_RECOMMENDATION_VERSION",
}


@dataclass(frozen=True, slots=True)
class ConsentState:
    """一项同意的当前状态。``valid`` 是唯一的门槛判据，患者侧不得自行推导。"""

    item: str
    decision: str | None
    text_version: str | None
    recorded_at: datetime | None
    source: str | None
    required_text_version: str

    @property
    def valid(self) -> bool:
        return self.decision == "granted" and self.text_version == self.required_text_version


def _checked(value: str, vocabulary: frozenset[str], what: str) -> str:
    """词表之外的取值**当场报错**，不猜方向。

    与「指标确认决策」同一取向：一条拼错的表态落库之后，读路径只能猜它是什么意思，而猜
    错的方向不对称（`revoked` 拼错被读成 `granted`，就是一次没有发生过的同意）。
    """
    if value not in vocabulary:
        raise ValueError(f"未知的{what}：{value!r}")
    return value


def required_text_version(item: str) -> str:
    """该项当前要求的文本版本（配置注入；开发阶段是草稿版本号）。"""
    return str(getattr(get_settings(), _REQUIRED_VERSION_SETTING[_checked(item, CONSENT_ITEMS, "同意项")]))


def states(db: Session, owner_id: str | None) -> list[ConsentState]:
    """该主体每一项的当前状态，按 `CONSENT_ITEMS_ORDERED` 的顺序。

    没有记录项也照样出现（`decision=None`、`valid=False`）——「他从未表过态」是一个
    必须被读出来的状态，而不是一个缺失的键。
    """
    latest: dict[str, HealthDataConsent] = {}
    if owner_id:
        rows = (
            db.query(HealthDataConsent)
            .filter(HealthDataConsent.owner_id == owner_id)
            # `id` 是单调的：同一秒内的两条记录也有确定的先后，不靠时间戳的精度。
            .order_by(HealthDataConsent.id.desc())
            .all()
        )
        for row in rows:
            latest.setdefault(row.consent_item, row)
    return [
        ConsentState(
            item=item,
            decision=(latest[item].decision if item in latest else None),
            text_version=(latest[item].text_version if item in latest else None),
            recorded_at=(latest[item].created_at if item in latest else None),
            source=(latest[item].source if item in latest else None),
            required_text_version=required_text_version(item),
        )
        for item in CONSENT_ITEMS_ORDERED
    ]


def state(db: Session, owner_id: str | None, item: str) -> ConsentState:
    return next(entry for entry in states(db, owner_id) if entry.item == item)


def has_valid(db: Session, owner_id: str | None, item: str) -> bool:
    """门槛问的那一个问题。**唯一的判定入口**，调用方不得自己再拼一次 `valid`。"""
    return state(db, owner_id, item).valid


def record_consent(
    db: Session,
    owner_id: str,
    item: str,
    decision: str,
    *,
    source: str,
) -> HealthDataConsent:
    """追加一条表态并返回它。**不 commit** —— 由调用方在同一事务里提交。

    词表之外的取值当场报错（与「指标确认决策」同一取向，不 fail open）：一个拼错的表态
    落库之后，读路径只能猜它是什么意思。
    """
    row = HealthDataConsent(
        owner_id=owner_id,
        consent_item=_checked(item, CONSENT_ITEMS, "同意项"),
        decision=_checked(decision, CONSENT_DECISIONS, "同意表态"),
        text_version=required_text_version(item),
        source=_checked(source, CONSENT_SOURCES, "同意来源"),
    )
    db.add(row)
    db.flush()
    return row


def source_for_request(request) -> str:
    """这次表态从哪个通道来：带 `Authorization: Bearer` 的是商城 H5，否则是旧前端。

    来源由**服务端**按会话通道判定，不由客户端声明 —— 一个能被请求体写的合规字段
    等于没有。判据与 `request_session_token` 同形（Bearer 优先，其次 Cookie），
    所以「令牌从哪来」与「这次表态度从哪来」不会给出两个互相矛盾的答案。
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.casefold() == "bearer" and token.strip():
        return "mall_h5"
    return "legacy_web"
