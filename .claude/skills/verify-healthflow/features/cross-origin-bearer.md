# 跨源 Bearer 会话

## Sub-features

- 票据兑换的响应体里有会话令牌（`session_token`）与过期时刻（`session_expires_at`），Cookie 照常写
- 只用 `Authorization: Bearer`（不带 Cookie）也能解析出同一主体
- 退出登录对 Bearer 立即生效；失效的 Bearer 得到 401 + `X-Report-Access-Reason: session_expired`
- 预检放行配置里的源，且 `Access-Control-Allow-Headers` 含 `Authorization`
- 配置外的源不被放行
- 跨源响应的 `Access-Control-Expose-Headers` 含 `X-Report-Access-Reason`，且没有 `Access-Control-Allow-Credentials`

## How to get to it (user POV)

没有页面可点——这是商城 H5（另一个源）从这里取会话、再带着令牌调接口的那条边。
患者在商城模块里看到的是「不用第二次登录」，而这条边就是它的服务端一半。

## Driving it with Playwright

`e2e/cors-bearer.spec.js`。它**不是**从页面自己的源发请求：同源请求在浏览器里不经过
CORS 检查，用它证明跨源等于没证明。跑法：

```bash
cd frontend
npm run build && ./node_modules/.bin/playwright test e2e/cors-bearer.spec.js
```

用例自己起两个源：API 在 `HEALTHFLOW_E2E_PORT`，商城替身在 `+1`
（`playwright.config.js` 的 `mallOrigin`，同时写进服务端的 `CORS_ORIGINS`）。

```js
// 页面开到商城源上，再在那个页面的上下文里 fetch API —— Origin 由浏览器按页面源填。
await page.goto(mallOrigin);
const exchange = await page.evaluate(async (apiBase) => {
  const res = await fetch(`${apiBase}/api/auth/ticket`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticket }),
  });
  return res.json();     // 这里读得到 session_token
}, apiOrigin);
await fetch(`${apiOrigin}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
// 失效后：res.headers.get('X-Report-Access-Reason') === 'session_expired'
// 这个 get 只有在 Expose-Headers 里有它时才读得到 —— 用 res.headers.get 断言，
// 不要用 page.waitForResponse 读原始头：后者绕过了浏览器的那道过滤，
// 会把「没暴露」误判成「读到了」。
```

票据由用例用沙箱里的私钥现签（`server.mjs` 生成密钥对时一并落盘
`e2e-ticket.key`），所以这条用例覆盖的是真实的兑换入口，不是手动种出来的会话。

## Gotchas

- **空白页不能用 `route.fulfill` 造。** 实测：这样页面会落在一个不受信任的地址空间，
  Chromium 以 `Permission was denied for this request to access the loopback address space`
  拒绝，红的原因与跨源无关。用 `http.createServer` 起一个真的回环页面（用例里已有）。
- **`Origin` 请求头由浏览器填，脚本改不了。** 要测「配置外的源」，就把页面开在那个源上。
- `Access-Control-Allow-Origin` / `Access-Control-Expose-Headers` 不在脚本可读的安全列表里，
  只能从 `page.waitForResponse(...).headers()` 读；而 `X-Report-Access-Reason` 恰恰相反——
  要断言「前端读得到」，就必须在页面上下文里 `res.headers.get`。
- 配置里的源来自 `CORS_ORIGINS`。改这个环境变量即改放行集合；代码里没有第二份。
