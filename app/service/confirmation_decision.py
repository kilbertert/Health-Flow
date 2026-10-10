"""指标确认决策的**判定**（GLOSSARY.md 的「指标确认决策」）。

词表在 `app/service/confirmation_vocabulary.py` —— 那里只有声明、不 import 任何项目
模块。本模块在词表之上放三个判定，它们此前散在三个地方、各写一遍：

1. `is_admitted` / `is_excluded` —— 这个决定会让一行进入解读吗（此前
   `ADMITTED_STATUSES` 与 `_decidable` 各写一遍，而它们其实是**两个问题**：门禁只认
   已核对过的行，判定守卫还要对 `pending` 作答）。
2. `effective_source` —— 生效值从哪来（此前把 `pending` **改叫 `extracted`**，于是
   同一条事实有了两个名字）。
3. `require_full_coverage` —— 一次确认是否覆盖了全部已解析指标（此前「没提供」被静默
   当成「排除」，而患者与客户端都无从知道）。

## 未知取值一律当场失败，不 fail open

三个判定的输入都可能来自**数据库列**（`VARCHAR(16)`，**没有 CHECK 约束**）—— 一个拼错的
字面量能落库。所以它们对词表外的值一律 `raise`，而不是猜一个方向：

- fail open 的代价不对称：`is_admitted("Confrimed")` 若返回 True，一条**患者从未确认过**的
  行会被当成已确认参与解读；`is_excluded("excluuded")` 若返回 False，一条患者明确排除的
  行会重新进入解读。两个方向都是「静默地按错误的一方行事」。
- 报错则相反：一条坏数据让**这一处**失败，而那是可查的（读路径是「报告打不开」而不是
  「报告里少了/多了几行」，后者没人会发现）。

这条与 `metric_effective_value.effective_source` 的取向一致 —— 它已经在读路径上这样做了。
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


def _checked(decision: str | None) -> str:
    """把输入规整成一个词表内的取值，词表外**当场报错**。

    `None` 是「没给」的合法写法，按「尚未决定」处理（DB 列的默认值就是它）；而一个
    **不认识的字符串**是数据坏了，不是一种决定 —— 见模块 docstring 里 fail-open 的那一节。
    """
    value = decision or "pending"
    if value not in DECISIONS:
        raise ValueError(f"未知的确认决策：{decision!r}")
    return value


def is_excluded(decision: str | None) -> bool:
    return _checked(decision) in EXCLUDED_DECISIONS


def is_admitted(decision: str | None) -> bool:
    """这个决定会让一行**进入解读**吗。

    正面枚举，不是反向排除（「不等于 excluded」）。两者的差别在**新增一种决策**时才显形：
    反向排除会默默放行这个新值，而正面枚举要求有人在这里显式表态。此前这两份写法同时存在，
    删一处另一处仍在。
    """
    return _checked(decision) in ADMITTED_DECISIONS


def request_decision(decision: str | None, *, status: str | None) -> str:
    """把**请求里的**一条表态解成落库决策。

    请求必须覆盖全部已解析指标（否则分不清「没提交」与「排除了」），所以它必须能表达
    「这条我还没动」—— 那由 `pending` 表示，而它**不是**一个「患者作的决定」：

    - 这条行**已经落定过**（重入确认）→ 沿用那个状态。患者在界面上没动它，意思就是
      「和上次一样」，不是「撤回上次的决定」。
    - 从没落定过 → 仍然是「尚未决定」，落库为 `pending`。

    这样就不存在「客户端替患者猜一个默认」这条路：客户端说的 `pending` 是**诚实的**，
    而它解出来是什么由**服务端**决定。
    """
    value = _checked(decision)
    if value not in UNDECIDED_DECISIONS:
        return value
    settled = status or "pending"
    # 落定过的状态必须是词表里的（坏数据要报错，与其余入口一致）。
    return settled if settled in DECISIONS else _checked(settled)


def effective_source(decision: str | None) -> str:
    """一条决策对应的**生效值来源** —— 就是它自己的名字。

    此前的实现把它映射成另一套名字（`pending` → `extracted`），于是「按 `source` 分支」
    与「按 `status` 分支」的消费者写出同一份逻辑的两份实现。现在这个函数存在只为了让那个
    事实**有一个名字**，而不是为了让两套词汇互译。
    """
    return _checked(decision)


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
