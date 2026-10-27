import { defineConfig } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(frontendDir, '..');

// 一次运行一个一次性沙箱:测试数据库与报告文件都落在系统临时目录,
// 绝不触碰仓库内的 data/healthflow.db。目录本身由 server.mjs / seed.mjs 按需创建。
// Playwright 的 worker 进程会重新求值本配置模块,因此这里必须保持幂等:
// 主进程首次求值时把 runDir 等写入 process.env,worker 经进程环境继承后不再重算,
// 否则 webServer(主进程)与 seed/fixture(worker)会各自派生不同的沙箱目录。
const runDir =
  process.env.HEALTHFLOW_E2E_RUN_DIR ||
  path.join(os.tmpdir(), `healthflow-e2e-${process.pid}-${Date.now()}`);
const port = Number(process.env.HEALTHFLOW_E2E_PORT || 8137);
const baseURL = `http://127.0.0.1:${port}`;
const databaseUrl =
  process.env.HEALTHFLOW_E2E_DATABASE_URL ||
  `sqlite:///${path.join(runDir, 'healthflow-e2e.db')}`;
const reportFilesDir =
  process.env.HEALTHFLOW_E2E_REPORT_FILES_DIR ||
  path.join(runDir, 'report-files');
const frontendDist =
  process.env.HEALTHFLOW_E2E_FRONTEND_DIST || path.join(frontendDir, 'dist');

// 「商城 H5」在 E2E 里的替身：**与 API 不同的源**。跨源行为只能由一个真的跨源请求证明，
// 同源请求在浏览器里根本不经过 CORS 检查。仅绑回环，与 API 端口不同即可（源 = scheme +
// host + port）。
const mallOrigin =
  process.env.HEALTHFLOW_E2E_MALL_ORIGIN || `http://127.0.0.1:${port + 1}`;

// 运行期上下文:通过环境变量传给服务器启动器、种子工具、测试 worker 与收尾。
const e2eEnv = {
  HEALTHFLOW_E2E_REPO_ROOT: repoRoot,
  HEALTHFLOW_E2E_FRONTEND_DIST: frontendDist,
  HEALTHFLOW_E2E_RUN_DIR: runDir,
  HEALTHFLOW_E2E_PORT: String(port),
  HEALTHFLOW_E2E_DATABASE_URL: databaseUrl,
  HEALTHFLOW_E2E_REPORT_FILES_DIR: reportFilesDir,
  HEALTHFLOW_E2E_MALL_ORIGIN: mallOrigin,
  HEALTHFLOW_E2E_BASE_URL: baseURL,
};
for (const [key, value] of Object.entries(e2eEnv)) {
  process.env[key] ??= value;
}

// 显式覆盖关键开关,让本地 .env 的同名配置失效,保证 E2E 环境确定。
const serverEnv = {
  ...process.env,
  ...e2eEnv,
  APP_ENV: 'development',
  DATABASE_URL: databaseUrl,
  SERVE_FRONTEND: 'true',
  FRONTEND_DIST: frontendDist,
  REPORT_FILES_DIR: reportFilesDir,
  REPORT_ACCOUNT_REQUIRED: 'true',
  AUTH_COOKIE_SECURE: 'false',
  HEALTHFLOW_BASIC_AUTH_ENABLED: 'false',
  // 服务端从**配置**读放行的源（ADR 0026）。这里把自己与服务都算上，是为了让
  // `cors-bearer.spec.js` 能用第二个源真的发出跨源请求 —— 同源请求证明不了跨源。
  CORS_ORIGINS: [baseURL, mallOrigin].join(','),
};

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  // `list` is what a person reads while it runs; `html` is what they open AFTER a
  // failure. Keeping only `list` meant CI had nothing to upload — measured on the
  // first run: "No files were found with the provided path: playwright-report/",
  // so the artifact step that exists for the failing case produced nothing in the
  // case it was built for. The HTML report writes itself to `playwright-report/`.
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    // 移动端优先基线视口(375px);桌面用例可在 spec 内用 test.use 覆盖。
    viewport: { width: 375, height: 667 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'node e2e/server.mjs',
    url: `${baseURL}/health`,
    timeout: 120_000,
    reuseExistingServer: false,
    stdout: 'pipe',
    env: serverEnv,
  },
  globalTeardown: './e2e/teardown.mjs',
});
