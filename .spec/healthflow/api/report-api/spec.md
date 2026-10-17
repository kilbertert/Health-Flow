---
title: report-api
desc: HTTP 面——上传、确认、匹配、鉴权
hue: 0
code:
  - app/api/report.py
related:
  - app/api/auth.py
  - app/api/metric.py
  - app/api/chat.py
  - app/api/train.py
  - app/main.py
---
# report-api

对外 HTTP 面。**当前 API 只覆盖报告线**：上传、解析、确认、原文追溯、已发布知识卡匹配。
Chat 与 train 路由存在但是冻结代码，不属于当前运行时承诺。

## 不变量

- **权限不从请求体取**：报告访问经逐报告令牌（`access_token`），历史记录没有令牌即不可读。
- 兜底不得把基础设施故障伪装成业务结论——那正是 [[interpretation-failure]] 记录的
  ADR-0006 事故形状：数据库断连被包成"这份报告读不出来，请重新上传"。
