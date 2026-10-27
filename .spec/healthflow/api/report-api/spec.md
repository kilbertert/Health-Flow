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
- **两道同意闸门都在读取昂贵的东西之前**：上传在读取任何文件之前判「报告解读」，
  推荐在调用商城之前判「商品推荐」（#269、#271）。闸门只存在于路由函数体的第一句，
  不放依赖层——依赖在上传路径上跑在文件被接收之后，那时「没同意」会表现为「格式不对」。
- **删除只有一条路，顺序是先库后文件**：`DELETE /report/{id}` 与「撤回报告解读同意」都走
  `app/service/report_deletion.py`。数据库提交成功之后才删磁盘文件；提交之前失败就整体回滚
  （#272，ADR 0029）。反过来时，失败的提交会留下指向不存在文件的报告行。
