---
title: session
desc: 患者会话与票据会话——本地换取，不由上游代验
hue: 100
related:
  - app/service/sessions.py
  - app/service/ticket_sessions.py
  - app/schema/auth.py
---
# session

本平台自己的会话。商城票据换本地会话的验签规则见 [[tickets]]。

## 不变量

- 会话由**本服务**签发与校验，不以上游"已经校验过"为前提（见 [[tickets]] 的 raw source）。
- 会话失效必须给出**可区分的**原因，不得与"商城不可用"合并成一个提示——
  把用户叫去"重新登录"一个不存在的页面，是 [[recommendation-empty-reason]] 记的同一形态。
