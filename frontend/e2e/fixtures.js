// E2E 脚手架 fixture:后续所有 E2E 用例从这里导入 test/expect,
// 自动获得与本次运行测试数据库绑定的种子数据工具。
import { test as base, expect } from '@playwright/test';
import { seedTestData } from './seed.mjs';

export { expect };

export const test = base.extend({
  /**
   * 种子数据工具(绑定本次运行的一次性测试数据库)。
   * 每次调用创建一个全新账户(唯一邮箱)及所请求的报告,
   * 用例之间不共享任何数据:
   *
   *   const { account, reports } = await seed({
   *     reports: ['assessed', 'pending_confirmation'],
   *   });
   */
  seed: async ({}, use) => {
    await use(seedTestData);
  },
});
