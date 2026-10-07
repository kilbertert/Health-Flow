// 报告详情独立页(E2E):
// 历史列表进入 #/report/:id,深链可直达并在刷新后恢复;
// 已完成报告渲染指标总览/异常摘要/知识卡/原文/技术详情,修正值优先展示;
// 待确认报告先显示状态边界,再进入既有确认流程。
import { test, expect, loginWithSeed } from './fixtures.js';

async function openReportFromHistory(page, seeded) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
}

test('历史列表打开报告详情并读取 hash 路由', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  const reportId = seeded.reports[0].id;
  await openReportFromHistory(page, seeded);

  expect(page.url()).toContain(`#/report/${reportId}`);
  const meta = page.locator('.report-meta-card');
  await expect(meta).toContainText(seeded.subject.display_name);
  await expect(meta).toContainText(`#${reportId}`);
  await expect(meta).toContainText('已生成健康提示');
  await expect(page.getByText('指标总览', { exact: true })).toBeVisible();
  await expect(page.locator('.report-abnormal-summary')).toContainText('异常指标 3 项');
});

test('报告详情深链刷新后恢复', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  const reportId = seeded.reports[0].id;
  await loginWithSeed(page, seeded);

  await page.goto(`/#/report/${reportId}`);
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await expect(page.getByText('指标总览', { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await expect(page.getByText('指标总览', { exact: true })).toBeVisible();
  expect(page.url()).toContain(`#/report/${reportId}`);
});

test('修正后的指标值优先展示', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  const reportId = seeded.reports[0].id;

  await page.route(`**/api/health/report/${reportId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: reportId,
        patient_id: seeded.subject.owner_id,
        report_type: '体检报告',
        department: '健康管理中心',
        created_at: new Date().toISOString(),
        status: 'assessed',
        subject_consistency: 'same',
        metrics: [{
          id: 1,
          report_id: reportId,
          metric_name: '空腹血糖',
          metric_value: '6.5',
          unit: 'mmol/L',
          reference_range: '3.9-6.1',
          abnormal_flag: 'H',
          // 患者把 6.5 修正为 6.4,参考范围 3.9-6.1 —— 6.4 仍高于上限,判定为 H。
          inferred_abnormal_flag: 'H',
          page_number: 1,
          evidence_text: '空腹血糖 6.4 mmol/L ↑',
          confirmation_status: 'corrected',
          confirmed_value: '6.4',
          confirmed_unit: 'mmol/L',
          confirmed_reference_range: '3.9-6.1',
          confirmed_evidence_text: '空腹血糖 6.4 mmol/L ↑',
        }],
        files: [],
        evidence_result: null,
        processing_warnings: [],
      }),
    });
  });

  await openReportFromHistory(page, seeded);
  const row = page.locator('.metric-overview-card tr').filter({ hasText: '空腹血糖' }).last();
  await expect(row).toContainText('6.4');
  await expect(page.locator('.metric-overview-card').getByText('6.5', { exact: true })).toHaveCount(0);
});

test('待确认报告先显示状态边界，再进入既有确认流程', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['pending_confirmation'] });
  await openReportFromHistory(page, seeded);

  const meta = page.locator('.report-status-card');
  await expect(meta).toBeVisible();
  await expect(meta).toContainText('报告已解析，等待确认');
  await expect(page.getByRole('button', { name: '继续确认' })).toBeVisible();
  await expect(page.locator('.metric-overview-card')).toHaveCount(0);

  await page.getByRole('button', { name: '继续确认' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  await expect(page.getByText(/解析结果/)).toBeVisible();
});

test('报告原文与技术详情默认收起且可展开', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  await openReportFromHistory(page, seeded);

  const original = page.getByRole('button', { name: '报告原文' });
  const technical = page.getByRole('button', { name: '技术详情' });
  await expect(original).toHaveAttribute('aria-expanded', 'false');
  await expect(technical).toHaveAttribute('aria-expanded', 'false');

  await original.click();
  await expect(page.getByAltText(/报告原文第 1 页/)).toBeVisible();
  await expect(original).toHaveAttribute('aria-expanded', 'true');
  await expect(technical).toHaveAttribute('aria-expanded', 'false');
});

test('打印媒体下仅保留报告抬头、指标总览与健康提示', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  await openReportFromHistory(page, seeded);
  await expect(page.locator('.wechat-print-guide')).toHaveCount(0);

  await page.emulateMedia({ media: 'print' });

  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await expect(page.locator('.report-meta-card')).toBeVisible();
  await expect(page.locator('.metric-overview-card')).toBeVisible();
  await expect(page.locator('.evidence-result-card')).toBeVisible();

  await expect(page.locator('.app-header')).toBeHidden();
  await expect(page.locator('.bottom-nav')).toBeHidden();
  await expect(page.locator('.report-original-collapse')).toBeHidden();
  await expect(page.locator('.technical-details')).toBeHidden();
  await expect(page.locator('.report-detail-page .ant-pagination')).toBeHidden();
});

test.describe('微信内置浏览器打印引导', () => {
  test.use({
    userAgent: 'Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49.2600(0x28003135) WeChat/8.0.49.2600',
  });

  test('报告详情显示"用系统浏览器打开"引导', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['assessed'] });
    await openReportFromHistory(page, seeded);

    const guide = page.locator('.wechat-print-guide');
    await expect(guide).toBeVisible();
    await expect(guide).toContainText('用系统浏览器打开');
  });
});

// 状态呈现（#140）：同一份报告在历史列表与详情页必须是**同一套**说法与颜色。
//
// 收敛前这里有两套：历史列表只有二色（assessed 绿、其余金），详情页是五色映射
// —— 一份 failed 的报告在历史列表显示金色（与「待确认」同色），点开详情才是红色。
// 状态机收敛到服务端后，前端只映射，两处共用 frontend/src/reportStatus.js。
test.describe('状态呈现的一致性', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('failed 报告在历史列表与详情页同色同说法', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['assessed'] });
    const reportId = seeded.reports[0].id;
    // 把这份报告改成 failed（历史列表走 /api/auth/reports，详情走 /api/health/report/{id}）。
    const failedHistory = (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: reportId,
            report_type: '体检报告',
            department: '健康管理中心',
            status: 'failed',
            created_at: new Date().toISOString(),
            metric_count: 3,
            abnormal_count: 0,
            finding_count: 0,
          },
        ]),
      });
    await page.route('**/api/auth/reports', failedHistory);
    await page.route(`**/api/health/report/${reportId}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: reportId,
          patient_id: seeded.subject.owner_id,
          report_type: '体检报告',
          department: '健康管理中心',
          created_at: new Date().toISOString(),
          status: 'failed',
          subject_consistency: 'same',
          metrics: [],
          files: [],
          evidence_result: null,
          processing_warnings: [],
        }),
      }),
    );

    await loginWithSeed(page, seeded);
    await page.getByRole('button', { name: '个人中心', exact: true }).click();
    const historyItem = page.locator('.history-section .ant-list-item').first();
    await expect(historyItem).toContainText('解析失败');
    // 历史列表用的是 error 色（红），不再是 gold。
    await expect(historyItem.locator('.ant-tag')).toHaveClass(/ant-tag-error/);

    await page.goto(`/#/report/${reportId}`);
    await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
    // 详情页的状态标签在页头（report-heading 里），与历史列表用的是同一份映射。
    const statusTag = page.locator('.report-heading .ant-tag');
    await expect(statusTag).toContainText('解析失败');
    await expect(statusTag).toHaveClass(/ant-tag-error/);
  });
});
