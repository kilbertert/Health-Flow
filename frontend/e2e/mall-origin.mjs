// 「商城 H5」在 E2E 里的替身：一个**真的**回环页面，作为 `mallOrigin`。
//
// 为什么由 globalSetup 起一次，而不是每条跨源 spec 自己起：
//
// 跨源用例不止一条（`cors-bearer.spec.js`、`consent.spec.js`），而 Playwright 让
// **不同的 spec 文件并行跑在不同的 worker 进程里**。两个 worker 各自 `listen` 同一个端口，
// 后到的那个拿到 `EADDRINUSE` —— 红的原因与「跨源是否被放行」毫无关系，而失败信息里
// 只有一行端口冲突，下一读的人会以为脚手架坏了。实测就是如此（83 条用例里它单条挂掉）。
//
// 也不能改成「谁先到谁绑、后到的复用」：先到的那条 spec 结束时会把监听关掉，而那时
// 另一条可能才跑到一半。监听必须活得比任何一条 spec 都久，于是它的生命周期是**一次运行**。
//
// 为什么必须是真的 HTTP 页面：`route.fulfill` 造出来的空白页会落在浏览器不信任的地址空间里，
// Chromium 以 `Permission was denied for this request to access the loopback address space`
// 拒绝跨源 fetch —— 那同样与本次要证明的东西无关。见 `cors-bearer.spec.js` 的同一段注释。
import http from 'node:http';
import { pathToFileURL } from 'node:url';
import process from 'node:process';

/**
 * 在给定源上起一张页面。
 *
 * @param {string} origin 形如 `http://127.0.0.1:8138`
 * @returns {Promise<() => Promise<void>>} 关闭函数
 */
export async function startMallOrigin(origin) {
  const { hostname, port } = new URL(origin);
  const server = http.createServer((request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end('<!doctype html><title>h5</title>');
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(Number(port), hostname, resolve);
  });
  return () => new Promise((resolve) => server.close(resolve));
}

/** Playwright `globalSetup`：整个运行期间只有这一个商城源监听。 */
export default async function globalSetup() {
  const origin = process.env.HEALTHFLOW_E2E_MALL_ORIGIN;
  if (!origin) {
    throw new Error('缺少 HEALTHFLOW_E2E_MALL_ORIGIN;请通过 "npm run test:e2e" 运行(playwright.config.js 会注入)。');
  }
  let stop;
  if (process.env.HEALTHFLOW_E2E_CONSUMER_FIXTURE) {
    const { startConsumer } = await import(pathToFileURL(process.env.HEALTHFLOW_E2E_CONSUMER_FIXTURE).href);
    stop = await startConsumer({ origin, apiOrigin: process.env.HEALTHFLOW_E2E_BASE_URL, runDir: process.env.HEALTHFLOW_E2E_RUN_DIR });
  } else {
    stop = await startMallOrigin(origin);
  }
  // `globalSetup` 的返回值是 teardown 回调 —— 监听的关闭与它的启动配对，不靠"进程退出时
  // 顺带收掉"（那会让端口在 teardown 之后仍然被占，下一次运行撞上）。
  return async () => {
    await stop();
  };
}
