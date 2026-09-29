// 375px 视口冒烟测试:登录 -> 首页渲染出内容。
// 这是 E2E 脚手架的最小基线,后续所有票的浏览器级断言都建立在这套脚手架上。
import { test, expect, loginWithSeed } from './fixtures.js';

test.use({ viewport: { width: 375, height: 667 } });

test('375px 视口:建立会话后首页渲染出内容', async ({ page, seed }) => {
  const seeded = await seed({
    reports: ['assessed', 'pending_confirmation'],
  });

  // #172:不再有登录页。会话由种子建立的主体会话给出,首页直接渲染。
  await loginWithSeed(page, seeded);

  // 首页渲染出内容。
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
  await expect(page.getByRole('button', { name: /体检报告解读/ })).toBeVisible();
});

test('种子数据可经主体会话读取:已完成与待确认报告各一份', async ({
  page,
  seed,
}) => {
  // #172:不再有 /auth/login。会话来自种子建立的主体会话,直接带 cookie 请求。
  const seeded = await seed();
  await page.context().addCookies([
    {
      name: 'healthflow_session',
      value: seeded.subject.session_token,
      url: 'http://127.0.0.1:8137',
    },
  ]);
  const history = await page.request.get('/api/auth/reports');
  expect(history.ok()).toBeTruthy();
  const reports = await history.json();
  expect(seeded.reports.map((report) => report.status).sort()).toEqual([
    'assessed',
    'pending_confirmation',
  ]);
  // 断言的是**服务端返回的**报告,不是种子脚本的回执——后者本来就不含 metric_count
  // （批量重命名时这里被误改了,原意一直是断言接口返回）。
  expect(reports.every((report) => report.metric_count > 0)).toBeTruthy();
});
