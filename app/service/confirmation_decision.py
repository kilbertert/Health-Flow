"""指标确认决策的**判定**（GLOSSARY.md 的「指标确认决策」）。

词表在 `app/service/confirmation_vocabulary.py` —— 那里只有声明、不 import 任何项目
模块。本模块在词表之上放三个判定，它们此前散在三个地方、各写一遍：

1. `is_admitted` —— 这个决定会让一行进入解读吗（此前 `ADMITTED_STATUSES` 与
   `_decidable` 是同一条判定的两份写法）。
2. `effective_source` —— 生效值从哪来（此前把 `pending` **改叫 `extracted`**，于是
   同一条事实有了两个名字）。
3. `require_full_coverage` —— 一次确认是否覆盖了全部已解析指标（此前「没提供」被静默
   当成「排除」，而患者与客户端都无从知道）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.service.confirmation_vocabulary import (
    ADMITTED_DECISIONS,
    DECISIONS,
    EXCLUDED_DECISIONS,
    UNDECIDED_DECISIONS,
)

# 生效值的来源，与决策词汇**同名**。
#
# 这里刻意不再叫 `extracted`：那是 `pending` 的别称，而「同一条事实两个名字」正是本
# 概念此前无家可归的物证。取 `pending` 的另一个理由是它已经是数据库列上写着的值。
EffectiveSource = str  # 取值即 `DECISIONS` —— 别名保留给读代码的人，不引入第二套字面量


def is_decided(decision: str | None) -> bool:
    """患者就这条表过态吗（`pending` 不算）。"""
    return (decision or "pending") not in UNDECIDED_DECISIONS


def is_excluded(decision: str | None) -> bool:
    return (decision or "pending") in EXCLUDED_DECISIONS


def is_admitted(decision: str | None) -> bool:
    """这个决定会让一行**进入解读**吗。

    正面枚举，不是反向排除（「不等于 excluded」）。两者的差别在**新增一种决策**时才显形：
    反向排除会默默放行这个新值，而正面枚举要求有人在这里显式表态。此前这两份写法同时存在，
    删一处另一处仍在。
    """
    return (decision or "pending") in ADMITTED_DECISIONS


def effective_source(decision: str | None) -> str:
    """一条决策对应的**生效值来源** —— 就是它自己的名字。

    此前的实现把它映射成另一套名字（`pending` → `extracted`），于是「按 `source` 分支」
    与「按 `status` 分支」的消费者写出同一份逻辑的两份实现。现在这个函数存在只为了让那个
    事实**有一个名字**，而不是为了让两套词汇互译。
    """
    value = decision or "pending"
    if value not in DECISIONS:
        raise ValueError(f"未知的确认决策：{decision!r}")
    return value


@dataclass(frozen=True)
class CoverageGap:
    """一次确认没覆盖到的那些指标。"""

    missing_ids: tuple[int, ...]
    extra_ids: tuple[int, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_ids and not self.extra_ids

    def detail(self) -> str:
        """给患者/客户端的说明：**点名**缺了哪些，而不是一句「不完整」。"""
        parts: list[str] = []
        if self.missing_ids:
            parts.append("缺少指标 " + "、".join(str(value) for value in self.missing_ids))
        if self.extra_ids:
            parts.append("包含未知指标 " + "、".join(str(value) for value in self.extra_ids))
        return "确认列表不完整：" + "；".join(parts)


def require_full_coverage(supplied_ids: Iterable[int], parsed_ids: Sequence[int]) -> CoverageGap:
    """一次确认必须覆盖**全部**已解析指标。

    此前「没提供」被静默当成「排除」—— 任何一条没出现在请求里的已解析指标会被置成
    `excluded`，与患者明确排除**不可区分**（`confirmed_at` 照写、审计事件把两者合在一个
    数组里、文档里也没有这句话）。于是「一次确认漏提交了某些行」在患者看不到的地方生效。

    现在它是一次**解析错误**：缺谁就点名谁。前端本来就发送全部指标（见确认页的载荷构造），
    所以这条约束不会拒掉正常的调用。

    顺序无关：只比集合。
    """
    supplied = list(supplied_ids)
    parsed = set(parsed_ids)
    missing = tuple(sorted(parsed - set(supplied)))
    extra = tuple(sorted(set(supplied) - parsed))
    return CoverageGap(missing_ids=missing, extra_ids=extra)
