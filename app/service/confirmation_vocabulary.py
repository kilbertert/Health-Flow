"""指标确认决策的词表（GLOSSARY.md 的「指标确认决策」）。

患者对一条结构化指标作出的决定是什么？**还没决定**算不算一个决定？有没有「没作决定」
这回事？—— 此前这四个问题由四份互不同步的声明回答，各自在自己的文件里写自己的名字：

| 位置 | 值 | 缺什么 |
| --- | --- | --- |
| 请求 `MetricConfirmation.decision` | 确认 / 修正 / 排除 | **没有 `pending`** —— 请求里根本无法表达「还没决定」 |
| 持久化 `MetricRecord.confirmation_status` | 尚未核对 / 确认 / 修正 / 排除 | 完整，但没有「名字该叫什么」的裁定 |
| 边界闸门 | `ADMITTED_STATUSES`（正面枚举）与 `_decidable`（反向排除） | 同一判定的**两份写法**，删一处另一处仍在 |
| 生效值来源 `EffectiveSource` | 修正 / 确认 / `extracted` / 排除 | 三个值与持久化相同，而 `pending` 改叫 `extracted` |

本模块是那份词表的家，**且只有这一个家**。与 `admission_vocabulary.py` 同样的约束：

- 不 import 任何项目模块。它必须能被**出域契约**（`app/schema/report.py`）导入，而契约
  在最底层 —— 一旦这里依赖了 service 层，导入就成了环（先例见 `admission_vocabulary.py`
  的模块 docstring，那条教训是实测来的）。
- 只用标准库、只放**声明**，不放函数。判定逻辑在 `app/service/confirmation_decision.py`。

## 一个名字，不是两个

`pending`（尚未决定）与 `extracted`（值来自抽取）说的是同一件事：**患者还没就这条表态**。
它们必须收成一个名字。取 `pending`，因为它同时也是持久化列上已经存在、数据库已经写着的那
个值（`app/data/models.py` 的列默认就是它），而 `extracted` 只是它的一个别称。

`source`（生效值从哪来）与 `status`（患者表了什么态）因此是**同一份词汇的两个读法**，
而不是两份词表 —— 见 `confirmation_decision.py` 的 `effective_source`。
"""

from __future__ import annotations

from typing import Literal, get_args

# ── 唯一词表 ────────────────────────────────────────────────────────────────
#
# 患者能说的话：三个决定 + 一个「还没有决定」。新增一种决策时，名字只加在这里 ——
# 请求契约、持久化契约、闸门、生效值来源都从这里取。
ConfirmationDecision = Literal["pending", "confirmed", "corrected", "excluded"]

# `Literal[*X]` 需要有序的取值（集合的迭代顺序不稳定，而契约的字面量顺序应当固定）。
DECISIONS_ORDERED: tuple[str, ...] = ("pending", "confirmed", "corrected", "excluded")
DECISIONS: frozenset[str] = frozenset(DECISIONS_ORDERED)

# 请求里可以出现的决定：**没有 `pending`**。
#
# 「还没决定」不是一个可以提交的决定 —— 患者对一条指标的表态要么是一个决定，要么那一条
# 不该出现在请求里。请求的语义是「我把这些行决定了」，而不是「我把这些行决定成了还没决定」。
# 所以请求词表是决策词表去掉 `pending`，而不是另写一份三值列表。
REQUEST_DECISIONS_ORDERED: tuple[str, ...] = ("confirmed", "corrected", "excluded")
REQUEST_DECISIONS: frozenset[str] = frozenset(REQUEST_DECISIONS_ORDERED)

# 闸门：这些决定会让一行**进入解读**。正面枚举是唯一的写法，反向排除（「不等于 excluded」）
# 是它的第二个实现 —— 那种写法在新增一个决定时会**默默放行**它，而正面枚举会要求有人
# 在这里显式表态。所以闸门从正面枚举读，反向那个表述由 `is_admitted` 提供。
ADMITTED_DECISIONS_ORDERED: tuple[str, ...] = ("confirmed", "corrected")
ADMITTED_DECISIONS: frozenset[str] = frozenset(ADMITTED_DECISIONS_ORDERED)

# 患者**没有就这条表态**的决定。它对应的生效值是「模型抽取值」—— 同一个事实、同一个名字。
UNDECIDED_ORDERED: tuple[str, ...] = ("pending",)
UNDECIDED_DECISIONS: frozenset[str] = frozenset(UNDECIDED_ORDERED)

# 患者明确不要它参与解读。
EXCLUDED_ORDERED: tuple[str, ...] = ("excluded",)
EXCLUDED_DECISIONS: frozenset[str] = frozenset(EXCLUDED_ORDERED)


def vocabulary() -> frozenset[str]:
    """词表的全部取值。供守卫使用：它必须与各子集的并集完全相等。"""
    return frozenset(get_args(ConfirmationDecision))
