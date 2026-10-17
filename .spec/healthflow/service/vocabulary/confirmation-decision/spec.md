---
title: confirmation-decision
desc: 指标确认决策的词表与三个判定
hue: 120
code:
  - app/service/confirmation_decision.py
related:
  - app/service/confirmation_vocabulary.py
---
# confirmation-decision

患者对一条结构化指标作出的决定是什么？**还没决定**算不算一个决定？有没有「没作决定」这回事？
——此前这四个问题由四份互不同步的声明回答：

| 位置 | 值 | 缺什么 |
| --- | --- | --- |
| 请求 `MetricConfirmation.decision` | 确认 / 修正 / 排除 | **没有 `pending`** ——请求里根本无法表达「还没决定」 |
| 持久化 `MetricRecord.confirmation…` | 另一套 | 与请求不同名 |
| 词表散处 | 又一套 | — |
| 前端 | 自造 | — |

## raw source

词表在 `confirmation_vocabulary.py`（只声明、不 import 项目模块）。本模块在词表之上放
**三个判定**，它们此前散在三个地方各写一遍。

其中一处值得记住：`ADMITTED_STATUSES` 与 `_decidable` 各写一遍，而它们其实是
**两个问题**——门禁只认已核对过的行，判定守卫还要对 `pending` 行给出结论。
把两个问题合成一个谓词，正是漂移的起点。

## 不变量

- 请求侧必须能表达「还没决定」（`pending` 是值的一部分，不是缺省）。
- 门禁问题与判定守卫问题**是两个谓词**，不得合并。
