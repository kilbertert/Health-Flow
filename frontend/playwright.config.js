import { defineConfig } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(frontendDir, '..');

// 一次运行一个一次性沙箱:测试数据库与报告文件都落在系统临时目录,
// 绝不触碰仓库内的 data/healthflow.db。目录本身由 server.mjs / seed.mjs 按需创建。
const runDir = path.join(
  os.tmpdir(),
  `healthflow-e2e-${process.pid}-${Date.now()}`,
);
const port = Number(process.env.HEALTHFLOW_E2E_PORT || 8137);
const baseURL = `http://127.0.0.1:${port}`;
const databaseUrl = `sqlite:///${path.join(runDir, 'healthflow-e2e.db')}`;
const reportFilesDir = path.join(runDir, 'report-files');
const frontendDist = path.join(frontendDir, 'dist');

// 运行期上下文:通过环境变量传给服务器启动器、种子工具、测试 worker 与收尾。
const e2eEnv = {
  HEALTHFLOW_E2E_REPO_ROOT: repoRoot,
  HEALTHFLOW_E2E_FRONTEND_DIST: frontendDist,
  HEALTHFLOW_E2E_RUN_DIR: runDir,
  HEALTHFLOW_E2E_PORT: String(port),
  HEALTHFLOW_E2E_DATABASE_URL: databaseUrl,
  HEALTHFLOW_E2E_REPORT_FILES_DIR: reportFilesDir,
};
Object.assign(process.env, e2eEnv);

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
  CORS_ORIGINS: baseURL,
};

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
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
