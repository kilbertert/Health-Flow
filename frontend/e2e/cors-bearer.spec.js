// 跨源 Bearer 会话（ADR 0026）：商城 H5 在**另一个源**上调用本仓的 API。
//
// 这是「票据 → 响应体里的会话令牌 → Bearer → 具名原因」的浏览器证据。请求刻意**不是**
// 从页面自己的源发出的：同源请求在浏览器里根本不经过 CORS 检查，用它证明跨源等于没证明。
//
// 「商城 H5」这里是一个独立的源（API 端口 +1，服务端只从 `CORS_ORIGINS` 里放行它），
// 由本文件起一个真的 HTTP 页面。**不能只 `route.fulfill` 一张空白页**：实测那样页面落在
// 一个不受信任的地址空间里，Chromium 会以「Permission was denied for this request to
// access the loopback address space」直接拒绝，测试红的原因与我们要证明的东西无关。
import { createSign, randomUUID } from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import process from 'node:process';

import { test, expect } from './fixtures.js';

const apiOrigin = process.env.HEALTHFLOW_E2E_BASE_URL;
const mallOrigin = process.env.HEALTHFLOW_E2E_MALL_ORIGIN;
const runDir = process.env.HEALTHFLOW_E2E_RUN_DIR;

/** 在一个给定的源上起一张空白页，返回它的 origin 与关闭函数。 */
async function serveOrigin(origin) {
  const { hostname, port } = new URL(origin);
  const server = http.createServer((request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end('<!doctype html><title>h5</title>');
  });
  await new Promise((resolve) => server.listen(Number(port), hostname, resolve));
  return () => new Promise((resolve) => server.close(resolve));
}

function b64url(value) {
  return Buffer.from(value).toString('base64url');
}

/**
 * 用 E2E 服务端启动时生成的密钥对签一张真票据。
 *
 * 不复用种子里的会话：这条用例要证明的正是「兑换会给出令牌」。公钥由 `server.mjs`
 * 交给服务端，私钥只在本次运行的沙箱里（只有 E2E 有）。
 */
function mintTicket() {
  const privateKey = fs.readFileSync(path.join(runDir, 'e2e-ticket.key'), 'utf8');
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
  const payload = b64url(
    JSON.stringify({
      aud: 'health-flow-e2e',
      sub: `cors-user-${randomUUID().slice(0, 8)}`,
      tenant_id: 'cors-tenant',
      jti: randomUUID(),
      iat: now,
      exp: now + 120,
    }),
  );
  const signingInput = `${header}.${payload}`;
  const signature = createSign('RSA-SHA256').update(signingInput).sign(privateKey, 'base64url');
  return `${signingInput}.${signature}`;
}

/** 从页面的源调 API。`Origin` 由浏览器按页面源填，脚本改不了它。 */
async function callApi(page, requestPath, init) {
  return page.evaluate(
    async ({ apiBase, requestPath: path, requestInit }) => {
      const res = await fetch(`${apiBase}${path}`, requestInit);
      return {
        status: res.status,
        // 只有出现在 `Access-Control-Expose-Headers` 里的头才读得到；其余一律 null。
        accessReason: res.headers.get('X-Report-Access-Reason'),
        body: await res.json().catch(() => null),
      };
    },
    { apiBase: apiOrigin, requestPath, requestInit: init },
  );
}

/** 记录浏览器实际收到的响应头（页面脚本读不到 `Access-Control-Allow-Origin`，它不在安全列表里）。 */
async function responseHeaders(page, requestPath, run) {
  const seen = page.waitForResponse((response) => response.url() === `${apiOrigin}${requestPath}`);
  const body = await run();
  const response = await seen;
  return { body, headers: response.headers() };
}

test.describe('跨源 Bearer 会话', () => {
  let closeMall;
  let closeOutsider;
  const outsider = `http://127.0.0.1:${Number(new URL(apiOrigin).port) + 2}`;

  test.beforeAll(async () => {
    closeMall = await serveOrigin(mallOrigin);
    closeOutsider = await serveOrigin(outsider);
  });

  test.afterAll(async () => {
    await closeMall?.();
    await closeOutsider?.();
  });

  test('票据换出的 Bearer 读到同一主体，失效时具名原因可读', async ({ page }) => {
    await page.goto(`${mallOrigin}/`);

    // 1) 兑换：响应体里有会话令牌与过期时刻，而且这个跨源请求真的被放行了。
    const exchange = await responseHeaders(page, '/api/auth/ticket', () =>
      callApi(page, '/api/auth/ticket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticket: mintTicket() }),
      }),
    );
    expect(exchange.body.status).toBe(201);
    expect(exchange.headers['access-control-allow-origin']).toBe(mallOrigin);
    expect(exchange.body.body.session_token).toBeTruthy();
    expect(exchange.body.body.session_expires_at).toBeTruthy();

    // 2) 只带 `Authorization`（跨源请求不带凭据，浏览器也不持有本仓的 Cookie）：
    //    解析出的仍是同一个主体。这条请求会触发预检，所以它也证明了
    //    `allow_headers` 里确实有 `Authorization`。
    const token = exchange.body.body.session_token;
    const me = await callApi(page, '/api/auth/me', { headers: { Authorization: `Bearer ${token}` } });
    expect(me.status).toBe(200);
    expect(me.body.subject_id).toBe(exchange.body.body.subject_id);

    // 3) 退出后令牌立即失效，而且**具名原因头能被跨源 JavaScript 读到**。没有
    //    `Access-Control-Expose-Headers` 的话 `res.headers.get` 只会返回 null，
    //    「会话失效」与「报告不存在」就退回猜文本（ADR 0018 的结论会在这里失守）。
    const logout = await callApi(page, '/api/auth/logout', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(logout.status).toBe(204);

    const after = await callApi(page, '/api/auth/me', { headers: { Authorization: `Bearer ${token}` } });
    expect(after.status).toBe(401);
    expect(after.accessReason).toBe('session_expired');
  });

  test('配置外的源拿不到放行', async ({ page }) => {
    await page.goto(`${outsider}/`);

    // 浏览器按缺 `Access-Control-Allow-Origin` 直接拒绝：脚本拿到的是 TypeError，
    // 而不是一份响应体。**负控**：一个 `allow_origins=["*"]` 的实现会让这条变红。
    const blocked = await page.evaluate(
      async (apiBase) => {
        try {
          const res = await fetch(`${apiBase}/api/auth/me`);
          return { reached: true, status: res.status };
        } catch (error) {
          return { reached: false, error: String(error) };
        }
      },
      apiOrigin,
    );
    expect(blocked.reached).toBe(false);
    expect(blocked.error).toContain('Failed to fetch');
  });
});
