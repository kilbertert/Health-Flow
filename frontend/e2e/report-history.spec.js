// 个人中心历史列表(E2E):断言已完成报告项渲染异常摘要。
import { test, expect } from './fixtures.js';

async function login(page, account) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: /^登\s*录$/ }).click();
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
}

test('历史列表项渲染指标数与异常摘要', async ({ page, seed }) => {
  const { account } = await seed({ reports: ['assessed'] });
  await login(page, account);

  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '报告历史' })).toBeVisible();

  const historyItem = page.locator('.history-section .ant-list-item').first();
  await expect(historyItem).toContainText('体检报告');
  await expect(historyItem).toContainText('已完成');
  await expect(historyItem).toContainText('3 项指标');
  await expect(historyItem).toContainText('2 项偏高/偏低');
});
