// 推荐为空的**错误分支**（#246）：
//
// 现有 recommendations.spec.js 只 mock 2xx 响应 —— 那正是客户端自造的 `session_expired`
// 能活下来的原因（那条分支从没被驱动过）。本文件补上错误路径：
//
//   1. 报告读不到（404/401）→ 患者看到的是**报告访问**那句，**不是**「商城暂时不可用」；
//   2. 加购失败带具名原因 `report_not_ready` → 患者看到「尚未就绪」那句，而不是一句
//      一概而论的「暂时无法跳转到商城」。
//
// 归因读的是服务端给的结构化事实（响应头 `X-Report-Access-Reason`），不是从消息文本里
// 嗅探 HTTP 状态 —— 后者会随 `detail` 的措辞静默翻转。
import { test, expect, loginWithSeed } from './fixtures.js';
import { PRODUCT_RECOMMENDATION, REPORT_INTERPRETATION } from './seed.mjs';

// 推荐端点要求有效的「商品推荐」同意（#271）；本文件测的是**失败分支**，不是同意，
// 所以种子代主体把这一项给上。
const CONSENT = [REPORT_INTERPRETATION, PRODUCT_RECOMMENDATION];

async function openAssessedReport(page, seed) {
  const seeded = await seed({ reports: ['assessed'], consent: CONSENT });
  await loginWithSeed(page, seeded);
  await page.goto(`/#/report/${seeded.reports[0].id}`);
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  return seeded.reports[0].id;
}

test('报告读不到时，推荐区指向报告访问，不说「商城不可用」', async ({ page, seed }) => {
  const reportId = await openAssessedReport(page, seed);
  // 同源端点按服务端的真实形状失败：状态 + 具名成因头。
  await page.route('**/api/health/report/*/recommendations', (route) =>
    route.fulfill({
      status: 404,
      contentType: 'application/json',
      headers: { 'X-Report-Access-Reason': 'report_not_found' },
      body: JSON.stringify({ detail: '报告不存在' }),
    }),
  );
  await page.reload();

  const card = page.locator('.recommendations-card');
  await expect(card).toContainText('暂无推荐');
  await expect(card).toContainText('打不开');
  // 关键负控：不能把本服务自己的访问问题说成商城的问题。
  await expect(card).not.toContainText('商城暂时不可用');
  await expect(reportId).toBeGreaterThan(0);
});

test('会话失效时指向商城入口，不叫患者重新登录一个不存在的页面', async ({ page, seed }) => {
  await openAssessedReport(page, seed);
  await page.route('**/api/health/report/*/recommendations', (route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      headers: { 'X-Report-Access-Reason': 'session_expired' },
      body: JSON.stringify({ detail: '会话无效，请从商城入口重新进入' }),
    }),
  );
  await page.reload();

  const card = page.locator('.recommendations-card');
  await expect(card).toContainText('商城入口');
  await expect(card).not.toContainText('重新登录');
});

test('加购失败按服务端的具名原因说话', async ({ page, seed }) => {
  await openAssessedReport(page, seed);
  await page.route('**/api/health/report/*/recommendations', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [{
          id: 'spu-1', name: '示例商品', image: null,
          price_down: '10.0', price_up: '10.0', stock: 3, shop_id: 's1',
        }],
        reason: null,
      }),
    }),
  );
  // 服务端说「这份报告还没准备好」——它已经把结论送过来了。
  await page.route('**/api/health/report/*/recommendations/*/cart-link', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ url: null, reason: 'report_not_ready' }),
    }),
  );
  await page.reload();

  await expect(page.locator('.recommendation-item')).toHaveCount(1);
  await page.getByRole('button', { name: '加入购物车' }).click();
  await expect(page.locator('.ant-message')).toContainText('还没完成确认');
  await expect(page.locator('.ant-message')).not.toContainText('暂时无法跳转到商城');
});

// #271：没有「商品推荐」同意时，服务端在调用商城之前就返回「未同意」。旧前端只需要把
// 这个新取值翻成一句话 —— 它要说的是**去开启推荐**，而不是「商城暂时不可用」或「稍后
// 再试」，因为患者手里就有一个能解决它的动作。
test('未同意商品推荐时，患者看到的是开启推荐的指引，不是商城故障', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'], consent: [REPORT_INTERPRETATION] });
  await loginWithSeed(page, seeded);
  await page.route('**/api/health/report/*/recommendations', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [],
        reason: 'product_recommendation_consent_required',
        groups: [],
      }),
    }),
  );
  await page.goto(`/#/report/${seeded.reports[0].id}`);

  const card = page.locator('.recommendations-card');
  await expect(card).toContainText('暂无推荐');
  await expect(card).toContainText('尚未开启商品推荐授权');
  // 负控：这一条不是商城故障，也不是「没有可推荐的货」。
  await expect(card).not.toContainText('商城暂时不可用');
  await expect(card).not.toContainText('暂无推荐的商品');
});

// 真实端点契约（不 mock 推荐端点）：这个种子**没有**给「商品推荐」同意，服务端返回的
// 就是那个具名原因。这条用例守的是「两端说的是同一个取值」——mock 只能证明前端认识它。
test('真实端点下未同意推荐得到「未同意」，且一条商品都不会渲染', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  await loginWithSeed(page, seeded);
  await page.goto(`/#/report/${seeded.reports[0].id}`);

  const card = page.locator('.recommendations-card');
  await expect(card).toContainText('尚未开启商品推荐授权');
  await expect(card.locator('.recommendation-item')).toHaveCount(0);
});
