// 运行收尾:删除本次运行的一次性沙箱(测试数据库/报告文件)。
// 需要保留现场排查时,设置 HEALTHFLOW_E2E_KEEP_SANDBOX=1。
import fs from 'node:fs';
import process from 'node:process';

export default async function globalTeardown() {
  const runDir = process.env.HEALTHFLOW_E2E_RUN_DIR;
  if (!runDir) return;
  if (process.env.HEALTHFLOW_E2E_KEEP_SANDBOX) {
    console.log(`e2e: 保留沙箱目录 ${runDir}(HEALTHFLOW_E2E_KEEP_SANDBOX 已设置)`);
    return;
  }
  fs.rmSync(runDir, { recursive: true, force: true });
}
