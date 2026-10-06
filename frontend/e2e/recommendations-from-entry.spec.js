// 回归：从**首页真实入口**走到商品推荐（修复「上传完看不到推荐」）。
//
// 病根不在推荐端点，而在入口：上传完停留在体检解读页，而商品推荐此前只挂在报告详情页，
// 于是「看不看得到商品」取决于用户点的是哪个入口，不取决于数据。
// 现有的 recommendations.spec.js 用 `page.goto('/#/report/<id>')` 直接进详情页，
// 正好绕过了这条路径——所以它对这次的缺口是**瞎的**。
// 本用例只能从首页按钮进入；一旦有人把推荐退回「只有详情页有」，它就变红。
import { test, expect, loginWithSeed } from './fixtures.js';

const MALL_HOST = 'lkf.h5.mall.qushiyun.com';
const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC';

// 与后端 seed 的 assessed 形状一致：findings 为空，所以真实端点会返回 no_published_card。
// 这里把端点 mock 成命中，是为了让「卡片出现」只取决于**渲染位置**，不取决于此刻有没有数据。
const HIT_ITEM = {
  id: '1610079554940235778',
  name: '力蜚能 多糖铁复合物胶囊',
  image: 'https://oss.aliyuncs.com/example.jpg',
  price_down: '36.5',
  price_up: '36.5',
  stock: null,
  shop_id: '1582258389846802433',
};

const UPLOADED = {
  id: 9001,
  patient_id: 'seed-subject',
  report_type: '体检',
  department: '',
  created_at: '2026-10-06T00:00:00.000Z',
  status: 'pending_confirmation',
  subject_consistency: 'same',
  metrics: [],
  files: [
    {
      file_index: 1,
      original_filename: 'report.png',
      media_type: 'image/png',
      page_count: 1,
      source_url: '/api/health/report/9001/files/1/pages/1',
    },
  ],
  processing_warnings: [],
};

const ASSESSED = {
  ...UPLOADED,
  status: 'assessed',
  evidence_result: {
    schema_version: '3',
    findings: [],
    unmatched: [],
    skipped: [],
    message: '暂无已审核内容',
  },
};

function json(route, body) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function openUploadPageFromHome(page, seeded) {
  await loginWithSeed(page, seeded);
  // 只走首页入口——不直接构造 /#/report/<id>，否则又绕过了本用例要守的那条路径。
  await page.getByRole('button', { name: '体检报告解读' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
}

async function pickFile(page) {
  await page.locator('.report-paste-zone input[type="file"]').setInputFiles({
    name: 'report.png',
    mimeType: 'image/png',
    buffer: Buffer.from(TINY_PNG_BASE64, 'base64'),
  });
}

test('从首页入口上传并生成健康提示后，商品推荐出现在当前页', async ({ page, seed }) => {
  const mallRequests = [];
  page.on('request', (request) => {
    if (request.url().includes(MALL_HOST)) mallRequests.push(request.url());
  });

  const seeded = await seed({ reports: [] });
  // mock 本应用自己的同源端点；**绝不可以 mock 商城域名**，那会掩盖
  // 「浏览器不请求商城」这条要保护的契约。
  await page.route('**/api/health/report/*/recommendations', (route) =>
    json(route, { items: [HIT_ITEM], reason: null }),
  );
  await page.route('**/api/health/report/upload', (route) => json(route, UPLOADED));
  // 确认即可直接得到 assessed（生产里也是这样：confirm 的响应可能已是最终结果）。
  await page.route('**/api/health/report/*/confirm', (route) => json(route, ASSESSED));

  await openUploadPageFromHome(page, seeded);
  await pickFile(page);
  await page.getByRole('button', { name: '上传并解析' }).click();
  await page.getByRole('button', { name: '确认并生成健康提示' }).click();

  // 断言落在**当前页**：没有跳转，也不要求用户再点一次。
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  const card = page.locator('.recommendations-card');
  await expect(card).toBeVisible();
  await expect(card).toContainText('力蜚能 多糖铁复合物胶囊');
  await expect(card).toContainText('¥36.5');
  await expect(card).toContainText('库存未标注');
  // 商品读取在服务端：浏览器始终不得对商城域名发起请求。
  expect(mallRequests).toEqual([]);
});

test('未生成健康提示时，当前页不渲染推荐区', async ({ page, seed }) => {
  const seeded = await seed({ reports: [] });
  await page.route('**/api/health/report/upload', (route) => json(route, UPLOADED));

  await openUploadPageFromHome(page, seeded);
  await pickFile(page);
  await page.getByRole('button', { name: '上传并解析' }).click();
  await expect(page.getByRole('button', { name: '确认并生成健康提示' })).toBeVisible();

  // 还没生成就没有推荐可谈：在这里渲染空态卡，会把「还没生成」说成「暂无商品」。
  await expect(page.locator('.recommendations-card')).toHaveCount(0);
});
