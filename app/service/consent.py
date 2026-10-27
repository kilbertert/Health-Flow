"""健康数据同意的判定与写入（GLOSSARY.md 的「健康数据同意」，#269）。

词表在 `app/service/consent_vocabulary.py`（只有声明、不 import 任何项目模块）。本模块
在它之上放**唯一**的写入入口与唯一的状态投影：

- ``record_consent`` —— 写一条表态。只追加，从不更新或删除既有行：撤回是又一条记录。
- ``states`` —— 当前状态。每项取**最新一条**，并要求它的文本版本等于**当前要求的版本**。
- ``has_valid`` —— 门槛问的那一个问题（上传要「报告解读」，推荐要「商品推荐」）。

## 撤回的后果在服务端兑现，而且与那条记录同一次提交

撤回「报告解读」＝删除该主体的全部报告（原件、页图、指标、审计事件、抽取任务）。那不是
一个「顺便」的动作：患者撤回的是「你可以拿我的数据做什么」，留下报告就是把撤回说成一句
空话。所以 ``record_consent`` 在写撤回记录的那一次调用里发出删除，并由它**在同一次提交**
里提交 —— 两条事实要么同时成立（记录说删了 N 份，报告也确实不在了），要么都不成立。

撤回「商品推荐」**不动报告**：两项同意彼此独立，撤一项不该牵连另一项（GLOSSARY.md 的
「健康数据同意」）。唯一一处判断「哪一项撤回会删报告」在这里，调用方不重复它。

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
from app.data.models import HealthDataConsent, MedicalReport
from app.service.consent_vocabulary import (
    CONSENT_DECISIONS,
    CONSENT_ITEMS,
    CONSENT_ITEMS_ORDERED,
    CONSENT_SOURCES,
    REPORT_INTERPRETATION,
)
from app.service.report_deletion import Deletion, delete_reports

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
    #: 最新一条若是一次「删报告的撤回」，它删掉了多少份；其余情况是 ``None``。
    deleted_report_count: int | None = None

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
            deleted_report_count=(
                latest[item].deleted_report_count if item in latest else None
            ),
        )
        for item in CONSENT_ITEMS_ORDERED
    ]


def state(db: Session, owner_id: str | None, item: str) -> ConsentState:
    return next(entry for entry in states(db, owner_id) if entry.item == item)


def has_valid(db: Session, owner_id: str | None, item: str) -> bool:
    """门槛问的那一个问题。**唯一的判定入口**，调用方不得自己再拼一次 `valid`。"""
    return state(db, owner_id, item).valid


#: 撤回哪一项会删掉该主体的全部报告。**只在这一处**写死这个对应关系：调用方（路由、
#: 种子、将来别的通道）不判断「这次撤回要不要删数据」，它们只调用 ``record_consent``。
_REVOCATION_DELETES_REPORTS = frozenset({REPORT_INTERPRETATION})


def record_consent(
    db: Session,
    owner_id: str,
    item: str,
    decision: str,
    *,
    source: str,
) -> tuple[HealthDataConsent, Deletion | None]:
    """追加一条表态，**并在撤回解读同意时发出删除该主体全部报告**。

    **不 commit** —— 由调用方提交；撤回那一支必须用返回的句柄提交（``Deletion.commit``），
    否则数据库里的报告删了、磁盘上的文件还在。返回 ``(那条记录, 删除句柄或 None)``；
    不涉及删除的表态返回 ``None``，调用方照常用 ``db.commit()``。

    删除与撤回记录进的是**同一次提交**：要么「记录说删了 N 份、报告也确实不在了」，要么
    两者都不成立。中途失败时什么都不落下（见 `app/service/report_deletion.py`）。

    词表之外的取值当场报错（与「指标确认决策」同一取向，不 fail open）：一个拼错的表态
    落库之后，读路径只能猜它是什么意思。
    """
    item = _checked(item, CONSENT_ITEMS, "同意项")
    decision = _checked(decision, CONSENT_DECISIONS, "同意表态")
    deletion = None
    report_count = None
    if decision == "revoked" and item in _REVOCATION_DELETES_REPORTS:
        deletion = delete_reports(
            db,
            db.query(MedicalReport).filter(MedicalReport.owner_id == owner_id).all(),
        )
        report_count = deletion.count

    row = HealthDataConsent(
        owner_id=owner_id,
        consent_item=item,
        decision=decision,
        text_version=required_text_version(item),
        source=_checked(source, CONSENT_SOURCES, "同意来源"),
        # 只有「删报告的撤回」带这个数。授予与推荐撤回留空 —— 见模型的说明。
        deleted_report_count=report_count,
    )
    db.add(row)
    db.flush()
    return row, deletion


def commit_consent(db: Session, recorded: tuple[HealthDataConsent, Deletion | None]) -> None:
    """提交一次表态，并在提交**成功之后**删掉该删的文件。

    撤回路由与别的表态走同一条提交路径，区别只在「提交之后还有一件事要做」。把它收成
    一个函数，是为了让「写成 db.commit() 而忘了删文件」不可能出现在调用方。
    """
    _, deletion = recorded
    if deletion is None:
        db.commit()
    else:
        deletion.commit(db)


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
