---
title: audit-events
desc: 审计事件的 action 词表——此前只活在 docstring 里
hue: 120
code:
  - app/service/audit_vocabulary.py
---
# audit-events

「这份报告发生过什么」由审计流回答。这条流此前**没有家**：两个互不相识的生产者各自写同一个
`ReportAuditEvent` 表、用同一批 action 名字，而**全部 action 名只出现在
`report_status.transition` 的 docstring 里**——契约把它出域成 `list[dict[str, Any]]`，
列是 `Column(String(64))`。**没有类型、没有约束、没有枚举**，拼错一个 action 名无人发现。

后果：一次确认落 **4 条**；同一条 `assessed` 有**两个 actor**；
「状态没动、只记了一笔」与「状态动过」长得一样。

## raw source

ADR-0016 定了两件事：审计事件有**一个生产者**、两个具名类别。本模块是那个 producer 的
action 词表，两个子集（迁移 / 审计专用）**不相交**。

## 不变量

- 契约 `audit_events[].action` **派生**自本词表，不再手写第二份。
- 一个事件只有一个生产者；两个生产者写同一批名字是这次缺陷的成因。
- 「记了一笔」与「状态动过」必须是可分辨的两件事。
