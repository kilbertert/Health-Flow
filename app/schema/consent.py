"""健康数据同意的出域契约（GLOSSARY.md 的「健康数据同意」）。

两个请求形状与一个响应形状。取值集都**派生**自 `app/service/consent_vocabulary.py`
（ADR 0019：一个封闭取值集只有一位主人），所以患者侧能说的取值不可能超服务端那套。

请求里**没有文本版本字段**：患者表的是「对现在给他看的那一版」的态度，而那一版是什么
由服务端在写入那一刻定（`app/service/consent.py`）。让客户端带版本，等于让它可以声称
自己对一版从未展示过的文本表过态。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.service.consent_vocabulary import (
    ConsentDecisionLiteral,
    ConsentItemLiteral,
    ConsentSourceLiteral,
)


class ConsentRecordRequest(BaseModel):
    """记录一次表态。撤回也是一次表态（`decision="revoked"`），不是删除。"""

    item: ConsentItemLiteral
    decision: ConsentDecisionLiteral


class ConsentItemState(BaseModel):
    """一项同意的当前状态：最新一条表态 + 当前要求的文本版本 + 是否有效。

    没有记录时 `decision` / `text_version` / `recorded_at` / `source` 都是 `None`
    且 `valid` 为假 —— 「他从未表过态」是一个要读得到的状态，不是一个缺失的键。

    `valid` 由**服务端**算定（`ConsentState.valid` 是**唯一**的判据）。患者侧只渲染它，
    不自己比字符串：两个版本号各比一次就是两条会漂移的判据，而「界面说已同意、服务端
    拒绝上传」是这条概念最坏的失败形状。
    """

    item: ConsentItemLiteral
    decision: ConsentDecisionLiteral | None = None
    text_version: str | None = None
    recorded_at: datetime | None = None
    source: ConsentSourceLiteral | None = None
    required_text_version: str
    valid: bool
    #: 最新一条若是一次「删报告的撤回」，它删掉了多少份报告（#272）。
    #:
    #: 报告行已经不存在了，「这次撤回删了什么」只能由这一行回答。非撤回、撤推荐的
    #: 记录与从未表态都是 `None` —— 它们不是「删了 0 份」，而是「这一条不涉及删除」。
    deleted_report_count: int | None = None


class ConsentStatusResponse(BaseModel):
    """当前主体的全部同意状态。两项**始终都在**，顺序固定。"""

    items: list[ConsentItemState]
