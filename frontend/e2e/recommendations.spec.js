// 检测页推荐商品(E2E,#174):
// 商品由本服务端代理取回,浏览器只与自身同源通信;
// 命中时渲染卡片,未命中时如实显示"暂无推荐"且区分原因;
// 无论哪种情况,浏览器都不得对商城域名发起请求。
import { test, expect } from './fixtures.js';

const MALL_HOST = 'lkf.h5.mall.qushiyun.com';

async function login(page, account) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: /^登\s*录$/ }).click();
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
}

async function openAssessedReport(page, seed, reportId) {
  const { account, reports } = await seed({ reports: ['assessed'] });
  const id = reportId || reports[0].id;
  await login(page, account);
  await page.goto(`/#/report/${id}`);
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  return { account, reportId: id };
}

function stubRecommendations(page, body) {
  return page.route('**/api/health/report/*/recommendations', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  );
}

test('命中时渲染商品卡片,且浏览器不请求商城域名', async ({ page, seed }) => {
  const mallRequests = [];
  page.on('request', (request) => {
    if (request.url().includes(MALL_HOST)) mallRequests.push(request.url());
  });
  await stubRecommendations(page, {
    items: [
      {
        id: '1610079554940235778',
        name: '力蜚能 多糖铁复合物胶囊',
        image: 'https://oss.aliyuncs.com/example.jpg',
        price_down: '36.5',
        price_up: '36.5',
        stock: null,
        shop_id: '1582258389846802433',
      },
    ],
    reason: null,
  });

  const { reportId } = await openAssessedReport(page, seed);
  await page.goto(`/#/report/${reportId}`);
  const card = page.locator('.recommendations-card');
  await expect(card).toBeVisible();
  await expect(card).toContainText('力蜚能 多糖铁复合物胶囊');
  await expect(card).toContainText('¥36.5');
  // 商城的 null 是"未标注",不是 0。
  await expect(card).toContainText('库存未标注');
  expect(mallRequests).toEqual([]);
});

// 这条**不 mock**推荐端点：它回到真实服务端，验证端点契约与浏览器读到的是同一个形状。
// 种子的 assessed 报告没有 findings，所以真实服务端应当返回 no_published_card，
// 且**不调用商城**——渲染通过但两端形状不一致时，这条会红。
test('真实端点契约下渲染空态（不 mock，回到服务端）', async ({ page, seed }) => {
  const { reportId } = await openAssessedReport(page, seed);
  await page.goto(`/#/report/${reportId}`);

  const card = page.locator('.recommendations-card');
  await expect(card).toContainText('暂无推荐');
  await expect(card).toContainText('本次未能生成健康风险提示');
  await expect(card.locator('.recommendation-item')).toHaveCount(0);
});

test('商城不可达时如实降级为"暂无推荐"且原因可区分', async ({ page, seed }) => {
  await stubRecommendations(page, { items: [], reason: 'mall_unavailable' });
  const { reportId } = await openAssessedReport(page, seed);
  await page.goto(`/#/report/${reportId}`);

  const card = page.locator('.recommendations-card');
  await expect(card).toContainText('暂无推荐');
  await expect(card).toContainText('商城暂时不可用');
  // 不报错、不伪造商品。
  await expect(card.locator('.ant-alert-error')).toHaveCount(0);
  await expect(card.locator('.recommendation-item')).toHaveCount(0);
});

test('无已发布知识卡与无可推荐商品的原因不同', async ({ page, seed }) => {
  await stubRecommendations(page, { items: [], reason: 'no_label_data' });
  const { reportId } = await openAssessedReport(page, seed);
  await page.goto(`/#/report/${reportId}`);

  const card = page.locator('.recommendations-card');
  await expect(card).toContainText('暂无推荐');
  await expect(card).toContainText('暂无可推荐的已上架商品');
});
