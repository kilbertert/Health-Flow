"""健康数据同意的词表（GLOSSARY.md 的「健康数据同意」）。

患者对其健康数据被**如何**使用的表态：两项彼此独立的同意、每条记录只追加、当前状态取
每项最新一条。词表的家就是这里，且只有这一个家 —— 与 `audit_vocabulary.py` /
`admission_vocabulary.py` 同样的约束：

- 不 import 任何项目模块。它必须能被**出域契约**（`app/schema/consent.py`）导入，
  而契约在最底层；一旦这里依赖了 service 层，导入就成了环（先例见
  `admission_vocabulary.py` 的模块 docstring，那条教训是实测来的）。
- 只用标准库、只放**声明**，不放函数。判定在 `app/service/consent.py`。

## 为什么「只追加」不是实现细节，而是这个词表要求的第一件事

一个「当前是否同意」的布尔字段足以让界面工作，但**举不了证**：患者何时同意、对哪一版文本、
从哪个通道表的态，全都没有了。而这三样正是合规要问的问题。所以同意的写入形状是
**一串事件**，当前状态是它的一次投影（最新一条 + 文本版本比对），不是另一份要维护的状态。

## 三个子集，各自回答一个问题

- ``CONSENT_ITEMS_ORDERED`` —— 同意的**对象**。`report_interpretation` 是必选的一项：
  没有它，服务端不接收上传。`product_recommendation` 是可选的另一项，没有记录即无效；
  它只放行「基于报告的商品推荐」，不影响报告解读。
- ``CONSENT_DECISIONS_ORDERED`` —— 患者的**表态**。撤回 (`revoked`) 是一条新记录，
  不是删除上一条。
- ``CONSENT_SOURCES_ORDERED`` —— 这次表态**从哪个通道来**。它是合规记录的一部分，
  一个拼错的来源与一个拼错的表态一样不可读。
"""

from __future__ import annotations

from typing import Literal

REPORT_INTERPRETATION = "report_interpretation"
PRODUCT_RECOMMENDATION = "product_recommendation"

# 元组而非 frozenset：`Literal[*X]` 的展开顺序应当稳定，而集合的迭代顺序不是。
CONSENT_ITEMS_ORDERED: tuple[str, ...] = (
    # 必选：读取上传的文件、交由模型服务商抽取、生成健康提示，保存至患者删除为止。
    REPORT_INTERPRETATION,
    # 可选、默认不勾选：只放行「基于报告的商品推荐」。
    PRODUCT_RECOMMENDATION,
)

CONSENT_DECISIONS_ORDERED: tuple[str, ...] = (
    "granted",
    # 撤回也是一条记录 —— 删掉上一条会让「他曾经同意过」这件事从历史里消失。
    "revoked",
)

CONSENT_SOURCES_ORDERED: tuple[str, ...] = (
    # 商城 H5 模块（Bearer 会话，ADR 0026）。
    "mall_h5",
    # 退役期的旧前端（Cookie 会话）。
    "legacy_web",
    # 端到端测试夹具代主体预置的表态。它不是患者的行为，所以有自己具名的来源。
    "seed",
)

CONSENT_ITEMS: frozenset[str] = frozenset(CONSENT_ITEMS_ORDERED)
CONSENT_DECISIONS: frozenset[str] = frozenset(CONSENT_DECISIONS_ORDERED)
CONSENT_SOURCES: frozenset[str] = frozenset(CONSENT_SOURCES_ORDERED)

#: 出域契约用的 `Literal`。`Literal[*X]` 是 Python 3.11+ 的写法；CI 与部署都钉在 3.13。
ConsentItemLiteral = Literal[*CONSENT_ITEMS_ORDERED]
ConsentDecisionLiteral = Literal[*CONSENT_DECISIONS_ORDERED]
ConsentSourceLiteral = Literal[*CONSENT_SOURCES_ORDERED]


def vocabulary() -> frozenset[str]:
    """词表的全部取值。供守卫使用：患者侧能说的取值必须落在这里面。"""
    return CONSENT_ITEMS | CONSENT_DECISIONS | CONSENT_SOURCES
