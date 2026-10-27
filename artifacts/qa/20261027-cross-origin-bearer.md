# 跨源 Bearer 会话：浏览器验证记录（2026-10-27）

## 验证的是什么

PRD #266 子单 #268 —— 商城 H5（另一个源）用票据换出的 **Bearer 会话**调用本仓 API。
服务端断言在 `tests/test_auth.py`、`tests/test_cors.py`；**跨源行为只有浏览器能证明**
（同源请求根本不经过 CORS 检查），所以这里留的是 `frontend/e2e/cors-bearer.spec.js` 的实测。

## 怎么跑的

```bash
cd frontend
npm run build
./node_modules/.bin/playwright test e2e/cors-bearer.spec.js   # 2 passed (4.1s)
npm run test:e2e                                              # 83 passed (49.4s)
uv run pytest -q                                              # 721 passed, 19 skipped
```

浏览器：Playwright 自带 Chromium，375×667 视口，一次性 SQLite 沙箱（`/tmp/healthflow-e2e-*`，
跑完由 `teardown.mjs` 删除）。

## 实测到的（`cors-bearer.spec.js` + 服务端日志）

```
OPTIONS /api/auth/ticket  200 OK      ← 商城源（端口 +1）预检被放行
POST    /api/auth/ticket  201 Created ← 响应体带 session_token / session_expires_at
OPTIONS /api/auth/me      200 OK      ← 预检放行 Authorization
GET     /api/auth/me      200 OK      ← 只有 Bearer，主体与兑换时相同
OPTIONS /api/auth/logout  200 OK
POST    /api/auth/logout  204 No Content
GET     /api/auth/me      401 Unauthorized   ← 同一令牌已吊销
        X-Report-Access-Reason: session_expired   ← 跨源 JS 读得到（Expose-Headers）
```

配置外的源（端口 +2）在页面里 `fetch` 得到 `TypeError: Failed to fetch`，即浏览器按缺
`Access-Control-Allow-Origin` 拒绝；服务端预检对该源返回 `400 Disallowed CORS origin`。

人工截图（`var/verify-evidence/cross-origin-bearer.png`，gitignored，沙箱里仍可见）显示的是
上述四步在商城源页面上的输出。

## 一条踩过的坑（已写进功能图）

用 `route.fulfill` 造一张空白页当「商城源」，浏览器会以
`Permission was denied for this request to access the loopback address space` 拒绝跨源
`fetch` —— 红的原因与本次改动无关。spec 里改用 `http.createServer` 起一个真的回环页面。

## 覆盖不到

- 商城 H5 自己怎么存令牌、怎么续期：在商城代码库里，不在本仓。
- 旧前端（同源 Cookie）退役：属另一张单，本次只是让它照常工作（`npm run test:e2e` 全绿）。
