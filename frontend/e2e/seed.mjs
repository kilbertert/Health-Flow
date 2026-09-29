// 种子数据工具:调用 scripts/e2e_seed.py 在 E2E 测试数据库中创建
// 登录账户与指定状态的报告(已完成 assessed / 待确认 pending_confirmation)。
// 每次调用都会创建全新账户(唯一邮箱),用例之间互不共享数据。
import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import process from 'node:process';
import { promisify } from 'node:util';
import { resolvePython } from './python.mjs';

const execFileAsync = promisify(execFile);

export const SEED_REPORT_STATUSES = ['assessed', 'pending_confirmation'];

/**
 * 在本次运行的测试数据库中写入票据主体、会话与报告种子数据。
 *
 * @param {object} [options]
 * @param {string[]} [options.reports] 需要的报告状态,默认已完成 + 待确认各一份。
 * @param {string} [options.displayName] 账户昵称。
 * @returns {Promise<{subject: {owner_id: string, tenant_id: string, external_subject: string, display_name: string, session_token: string}, reports: Array<{id: number, status: string, report_type: string}>}>}
 */
export async function seedTestData({
  reports = SEED_REPORT_STATUSES,
  displayName = '体检用户',
} = {}) {
  const repoRoot = process.env.HEALTHFLOW_E2E_REPO_ROOT;
  const databaseUrl = process.env.HEALTHFLOW_E2E_DATABASE_URL;
  const reportFilesDir = process.env.HEALTHFLOW_E2E_REPORT_FILES_DIR;
  if (!repoRoot || !databaseUrl) {
    throw new Error(
      '缺少 HEALTHFLOW_E2E_REPO_ROOT / HEALTHFLOW_E2E_DATABASE_URL;请通过 "npm run test:e2e" 运行(playwright.config.js 会注入)。',
    );
  }
  for (const status of reports) {
    if (!SEED_REPORT_STATUSES.includes(status)) {
      throw new Error(`未知的种子报告状态: ${status}`);
    }
  }
  const { command, prefixArgs } = resolvePython(repoRoot);
  // #172 之后不再有账号与密码：主体由「票据兑换」产生。种子直接构造等价的
  // 主体标识与一条会话，用例之间用不同的 (tenant, sub) 保持互不共享。
  const tenantId = `e2e-tenant-${randomUUID().slice(0, 8)}`;
  const externalSubject = `e2e-user-${randomUUID().slice(0, 8)}`;
  const seedArgs = [
    ...prefixArgs,
    path.join(repoRoot, 'scripts', 'e2e_seed.py'),
    '--database',
    databaseUrl,
    '--tenant-id',
    tenantId,
    '--external-subject',
    externalSubject,
    '--display-name',
    displayName,
    ...reports.flatMap((status) => ['--report', status]),
  ];
  if (reportFilesDir) {
    seedArgs.push('--report-files', reportFilesDir);
  }
  const { stdout } = await execFileAsync(command, seedArgs, {
    cwd: repoRoot,
    maxBuffer: 8 * 1024 * 1024,
  });
  return JSON.parse(stdout);
}
