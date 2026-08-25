// 375px 视口冒烟测试:登录 -> 首页渲染出内容。
// 这是 E2E 脚手架的最小基线,后续所有票的浏览器级断言都建立在这套脚手架上。
import { test, expect } from './fixtures.js';

test.use({ viewport: { width: 375, height: 667 } });

test('375px 视口:登录后首页渲染出内容', async ({ page, seed }) => {
  const { account } = await seed({
    reports: ['assessed', 'pending_confirmation'],
  });

  await page.goto('/');

  // 登录页渲染,使用种子账户走真实登录接口。
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();

  // 登录成功后首页渲染出内容。
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
  await expect(page.getByRole('button', { name: /体检报告解读/ })).toBeVisible();
});

test('种子数据可经登录会话读取:已完成与待确认报告各一份', async ({
  page,
  seed,
}) => {
  const { account } = await seed();
  const login = await page.request.post('/api/auth/login', {
    data: { email: account.email, password: account.password },
  });
  expect(login.ok()).toBeTruthy();

  const history = await page.request.get('/api/auth/reports');
  expect(history.ok()).toBeTruthy();
  const reports = await history.json();
  expect(reports.map((report) => report.status).sort()).toEqual([
    'assessed',
    'pending_confirmation',
  ]);
  expect(reports.every((report) => report.metric_count > 0)).toBeTruthy();
});
