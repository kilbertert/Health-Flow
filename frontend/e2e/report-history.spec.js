// 个人中心历史列表(E2E):断言已完成报告项渲染异常摘要。
import { test, expect, loginWithSeed } from './fixtures.js';

test('历史列表项渲染指标数与异常摘要', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  await loginWithSeed(page, seeded);

  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '报告历史' })).toBeVisible();

  const historyItem = page.locator('.history-section .ant-list-item').first();
  await expect(historyItem).toContainText('体检报告');
  await expect(historyItem).toContainText('已生成健康提示');
  await expect(historyItem).toContainText('5 项指标');
  // 5 项里 2 项判定为异常(原有两条 H);误标的第 3 条判成 N 不计数,
  // 漏标的第 4 条判成 H **计入** —— 摘要从此是「判定口径」。
  await expect(historyItem).toContainText('3 项偏高/偏低');
});
