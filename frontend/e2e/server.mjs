// E2E 服务器启动器:用真实构建产物(frontend/dist)+ 一次性测试数据库
// 启动 HealthFlow FastAPI(uvicorn),由 playwright.config.js 注入运行参数。
import { generateKeyPairSync } from 'node:crypto';
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

// 票据验签是启动前置（未配置即拒绝启动，见 app/service/tickets.py）。E2E 不覆盖票据
// 链路，但仍需要一个自造的密钥对让服务能起来——生成在这里而不是提交进仓库，
// 因为它是一次性的测试夹具，不是配置。
function ensureTicketKeypair(dir) {
  const keyPath = path.join(dir, 'e2e-ticket.pub.pem');
  if (!fs.existsSync(keyPath)) {
    const { publicKey } = generateKeyPairSync('rsa', {
      modulusLength: 2048,
      publicKeyEncoding: { type: 'spki', format: 'pem' },
      privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
    });
    fs.writeFileSync(keyPath, publicKey, { mode: 0o600 });
  }
  return keyPath;
}

// 目录此时可能还不存在（server.mjs 下面才 mkdir），所以按 runDir 放，先建好它。
const runDir = requiredEnv('HEALTHFLOW_E2E_RUN_DIR');
fs.mkdirSync(runDir, { recursive: true });
const ticketPublicKeyPath = ensureTicketKeypair(runDir);

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
      MALL_TICKET_PUBLIC_KEY_PATH: ticketPublicKeyPath,
      MALL_TICKET_AUDIENCE: 'health-flow-e2e',
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
