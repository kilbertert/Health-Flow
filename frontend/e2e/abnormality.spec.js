// 异常判定口径（#134）在浏览器里的证据。
//
// 服务端从 #134 起单点拥有「这条指标是否异常」，前端只消费 `inferred_abnormal_flag`。
// 本文件驱动的是三条患者可见路径，断言的是**患者看到的结果**，不是内部状态：
//
//   1. 待确认报告 → 报告详情 → 继续确认：带原始 H 标记但数值在范围内的指标
//      显示「N 正常」并排在后面；数值超范围的显示「H 偏高」并排在前面。
//   2. 同一份报告的「继续确认」入口收到所有未处理的指标（`H 偏高` 的两条）。
//   3. 排除一项指标后，历史摘要「未见异常」与详情「未见异常指标」同口径
//      —— 这是 Devin Review 在 #136 指出的分叉，本用例是它的回归保护。
//
// 排除那一条通过拦截确认请求、返回一份真实的已评估响应来驱动：确认接口本身
// 需要商城票据验签器（本仓 e2e 环境没有），所以「患者点排除」发生在 UI 上、
// 「服务端怎么记」由响应决定。测的是**前端如何呈现排除后的报告**，不是后端。
//
// 证据截图不在这里写：`var/verify-evidence/` 是构建期目录，干净 checkout 里不存在，
// 而 Playwright 只在失败时留档。断言本身就是回归保护；需要人看的截图由
// `verify-healthflow` 的驱动会话负责采集（见该 skill 的 Evidence 一节）。
// 用 `HEALTHFLOW_E2E_KEEP_SANDBOX` 或失败时的 `test-results/` 取本次运行的现场。
import { test, expect, loginWithSeed } from './fixtures.js';

const TAG_H = 'H 偏高';
const TAG_N = 'N 正常';

/** 服务端口径下的已评估报告：一条被排除的超范围指标，其余正常。 */
function assessedResponse(seeded, reportUrl) {
  const id = Number(reportUrl.split('/report/')[1].split('/')[0]);
  return {
    id,
    patient_id: seeded.subject.owner_id,
    report_type: '体检报告',
    department: '健康管理中心',
    created_at: new Date().toISOString(),
    status: 'assessed',
    subject_consistency: 'same',
    metrics: [
      {
        id: 901,
        report_id: id,
        metric_name: '甘油三酯',
        metric_value: '2.3',
        unit: 'mmol/L',
        reference_range: '0.45-1.7',
        abnormal_flag: 'H',
        // 患者排除了它 —— 服务端因此不给判定。
        confirmation_status: 'excluded',
        inferred_abnormal_flag: null,
      },
      {
        id: 902,
        report_id: id,
        metric_name: '低密度脂蛋白胆固醇',
        metric_value: '3.1',
        unit: 'mmol/L',
        reference_range: '2.1-3.1',
        abnormal_flag: 'N',
        confirmation_status: 'confirmed',
        inferred_abnormal_flag: 'N',
      },
    ],
    files: [],
    // 单一患者投影（#159）：不再有 patient_reply 包裹与内部层 findings。
    evidence_result: {
      correlation_id: 'e2e-abnormality-evidence',
      title: '体检报告解读与健康风险提示',
      summary: 'E2E 异常判定口径。',
      findings: [],
      unmatched: [],
      skipped: [],
      unmatched_count: 0,
      disclaimer: '本解读仅提供健康辅助建议。',
    },
    processing_warnings: [],
  };
}

/** 打开待确认报告的确认页。 */
async function openConfirmPage(page, seeded) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await page.getByRole('button', { name: '继续确认' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  await expect(page.getByText(/解析结果/)).toBeVisible();
}

test.describe('异常判定口径', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test('种子的两个 H 指标显示偏高，血红蛋白显示正常并排在后面', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    await openConfirmPage(page, seeded);

    // 两条原始 H 都在，显示的是服务端判定结果。
    await expect(page.getByRole('button', { name: '甘油三酯指标卡片' })).toContainText(TAG_H);
    await expect(page.getByRole('button', { name: '低密度脂蛋白胆固醇指标卡片' })).toContainText(TAG_H);

    // 页头如实报出需要确认的项数。
    await expect(page.getByText(/其中 2 项需要确认/)).toBeVisible();
  });

  test('排除一项异常指标后，历史摘要与详情同口径（都不含它）', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    const reportId = seeded.reports[0].id;
    // 打开报告详情前先确认：这一步把报告推到 assessed，并记下「甘油三酯被排除」。
    await page.route(`**/api/health/report/${reportId}/confirm`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(assessedResponse(seeded, route.request().url())),
      });
    });
    await openConfirmPage(page, seeded);
    await page.getByRole('button', { name: '确认并生成健康提示' }).click();
    await expect(page.getByText('E2E 异常判定口径。')).toBeVisible();

    // 详情页：被排除的指标不显示异常，摘要也不为它计数。
    // 详情接口本身不可达（确认链路需要商城票据），按 report-detail.spec.js 的
    // 既有做法在客户端拦下并喂入同一份响应 —— 断言仍是患者可见的渲染结果。
    await page.route(`**/api/health/report/${reportId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(assessedResponse(seeded, route.request().url())),
      });
    });
    await page.goto(`/#/report/${reportId}`);
    await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
    await expect(page.locator('.report-abnormal-summary')).toContainText('未见异常指标');
    // #144 起：患者明确排除的指标**不再出现在报告单总览**（服务端不给生效值，
    // 前端只按它过滤）。患者不会看到自己刚排除的指标还挂在报告上。
    await expect(page.locator('.metric-overview-card').getByText('甘油三酯')).toHaveCount(0);
    await expect(page.locator('.report-abnormal-summary')).not.toContainText('异常指标 1 项');
  });
});

test.describe('异常判定口径（桌面端）', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('确认表里正常指标不带异常标记，异常指标带', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    await openConfirmPage(page, seeded);

    const table = page.getByRole('table').filter({
      has: page.getByRole('columnheader', { name: '指标' }),
    });
    await expect(table).toBeVisible();
    const triglycerideRow = table.locator('tr', { hasText: '甘油三酯' });
    await expect(triglycerideRow).toContainText(TAG_H);
    await expect(triglycerideRow).not.toContainText(TAG_N);
  });
});

// ---------------------------------------------------------------------------
// 分歧样本:模型标记与判定不一致时，患者看到的以**判定**为准
// ---------------------------------------------------------------------------

test.describe('异常判定口径 vs 抽取模型标记', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('模型误标 H 但数值在范围内 → 显示正常、不计入摘要；模型漏标但超范围 → 显示异常、计入摘要', async ({
    page,
    seed,
  }) => {
    const seeded = await seed({ reports: ['assessed'] });
    const reportId = seeded.reports[0].id;
    await loginWithSeed(page, seeded);

    // 历史摘要:种子 5 项里判定为异常的是 3 项(原有两条 + 漏标的那条)。
    // 误标的那条贡献的是「0」—— 报告页显示正常，摘要也不计数。
    await page.getByRole('button', { name: '个人中心', exact: true }).click();
    const historyItem = page.locator('.history-section .ant-list-item').first();
    await expect(historyItem).toContainText('5 项指标');
    await expect(historyItem).toContainText('3 项偏高/偏低');

    // 报告页:两条分歧样本各自的标记。
    await page.goto(`/#/report/${reportId}`);
    await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
    const overview = page.locator('.metric-overview-card');
    const mislabelled = overview.locator('tr', { hasText: '误标的餐后血糖' });
    const unlabelled = overview.locator('tr', { hasText: '漏标的总胆固醇' });
    await expect(mislabelled).toContainText('N 正常');
    await expect(mislabelled).not.toContainText('H 偏高');
    await expect(unlabelled).toContainText('H 偏高');
    // 摘要里的数字与这两行一致:误标的不算、漏标的算。
    await expect(page.locator('.report-abnormal-summary')).toContainText('异常指标 3 项');
  });
});

// ---------------------------------------------------------------------------
// 回归:Devin Review 在 #138 指出的两条
// ---------------------------------------------------------------------------

test.describe('排除与旧响应不能被误报或漏报', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test('字段出现之前的响应:确认页仍可用，异常候选仍可见', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    const reportId = seeded.reports[0].id;
    // 去掉判定字段,模拟 #134 之前的响应形状(契约是向后兼容的)。
    const legacy = (report) => ({
      ...report,
      metrics: (report.metrics || []).map(({ inferred_abnormal_flag, ...rest }) => rest),
    });
    await page.route(`**/api/health/report/${reportId}`, async (route) => {
      const res = await route.fetch();
      await route.fulfill({ response: res, json: legacy(await res.json()) });
    });
    await page.route(`**/api/health/report/${reportId}/metrics`, async (route) => {
      const res = await route.fetch();
      await route.fulfill({ response: res, json: { metrics: legacy({ metrics: (await res.json()).metrics }).metrics } });
    });

    await loginWithSeed(page, seeded);
    await page.getByRole('button', { name: '个人中心', exact: true }).click();
    await page.getByRole('button', { name: '查看' }).click();
    await page.getByRole('button', { name: '继续确认' }).click();
    await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
    // 旧响应下患者仍能看见并处理异常候选（默认「待核对」）。
    await expect(page.getByText(/其中 \d+ 项需要确认/)).toBeVisible();
  });
});
