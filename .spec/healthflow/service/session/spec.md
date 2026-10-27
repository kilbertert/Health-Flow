---
title: session
desc: 患者会话与票据会话——本地换取，不由上游代验
hue: 100
related:
  - app/service/sessions.py
  - app/service/ticket_sessions.py
  - app/schema/auth.py
  - app/api/auth.py
---
# session

本平台自己的会话。商城票据换本地会话的验签规则见 [[tickets]]。

## 不变量

- 会话由**本服务**签发与校验，不以上游"已经校验过"为前提（见 [[tickets]] 的 raw source）。
- 会话失效必须给出**可区分的**原因，不得与"商城不可用"合并成一个提示——
  把用户叫去"重新登录"一个不存在的页面，是 [[recommendation-empty-reason]] 记的同一形态。
- **令牌从哪读只有一个入口**（`request_session_token`：先 `Authorization: Bearer`，再
  Cookie），**一条会话行**同时服务两者，所以有效期、吊销、主体解析都只有一处判据。
  两处各读一次就是「退出登录对 Bearer 不生效」这类缺陷的形状（ADR 0026）。
- 浏览器侧的两种形态是**过渡期的**：Cookie 给同源的旧前端，Bearer 给跨源的商城 H5。
  Cookie 路径在旧前端退役时删除，不是要长期维护的第二套。
