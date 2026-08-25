// E2E 服务器启动器:用真实构建产物(frontend/dist)+ 一次性测试数据库
// 启动 HealthFlow FastAPI(uvicorn),由 playwright.config.js 注入运行参数。
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { resolvePython } from './python.mjs';

function requiredEnv(name) {
  const value = process.env[name];
  if (!value) {
    console.error(
      `e2e/server.mjs: 缺少环境变量 ${name};请通过 "npm run test:e2e" 运行(playwright.config.js 会注入)。`,
    );
    process.exit(1);
  }
  return value;
}

const repoRoot = requiredEnv('HEALTHFLOW_E2E_REPO_ROOT');
const frontendDist = requiredEnv('HEALTHFLOW_E2E_FRONTEND_DIST');
const port = requiredEnv('HEALTHFLOW_E2E_PORT');
const databaseUrl = requiredEnv('HEALTHFLOW_E2E_DATABASE_URL');
const reportFilesDir = requiredEnv('HEALTHFLOW_E2E_REPORT_FILES_DIR');

if (!fs.existsSync(path.join(frontendDist, 'index.html'))) {
  console.error(
    `e2e/server.mjs: 未找到前端构建产物 ${frontendDist}/index.html;请先执行 "npm run build"("npm run test:e2e" 已包含)。`,
  );
  process.exit(1);
}

fs.mkdirSync(reportFilesDir, { recursive: true });

const { command, prefixArgs } = resolvePython(repoRoot);
const server = spawn(
  command,
  [
    ...prefixArgs,
    '-m',
    'uvicorn',
    'app.main:app',
    '--host',
    '127.0.0.1',
    '--port',
    port,
  ],
  {
    cwd: repoRoot,
    stdio: ['ignore', 'inherit', 'inherit'],
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      SERVE_FRONTEND: 'true',
      FRONTEND_DIST: frontendDist,
      REPORT_FILES_DIR: reportFilesDir,
    },
  },
);

console.log(
  `e2e/server.mjs: 启动 ${command} uvicorn app.main:app (port=${port}, database=${databaseUrl})`,
);

const shutdown = () => {
  if (server.exitCode === null) server.kill('SIGTERM');
};
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
server.on('error', (error) => {
  console.error(`e2e/server.mjs: 无法启动 ${command}:`, error);
  process.exit(1);
});
server.on('exit', (code) => process.exit(code === null ? 0 : code));
