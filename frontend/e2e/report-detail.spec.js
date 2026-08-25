// 报告详情独立页(E2E):
// 历史列表进入 #/report/:id,深链可直达并在刷新后恢复;
// 已完成报告渲染指标总览/异常摘要/知识卡/原文/技术详情,修正值优先展示;
// 待确认报告先显示状态边界,再进入既有确认流程。
import { test, expect } from './fixtures.js';

async function login(page, account) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
}

async function openReportFromHistory(page, account) {
  await login(page, account);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
}

test('历史列表打开报告详情并读取 hash 路由', async ({ page, seed }) => {
  const { account, reports } = await seed({ reports: ['assessed'] });
  const reportId = reports[0].id;
  await openReportFromHistory(page, account);

  expect(page.url()).toContain(`#/report/${reportId}`);
  const meta = page.locator('.report-meta-card');
  await expect(meta).toContainText(account.display_name);
  await expect(meta).toContainText(`#${reportId}`);
  await expect(meta).toContainText('已完成');
  await expect(page.getByText('指标总览', { exact: true })).toBeVisible();
  await expect(page.locator('.report-abnormal-summary')).toContainText('异常指标 2 项');
});

test('报告详情深链刷新后恢复', async ({ page, seed }) => {
  const { account, reports } = await seed({ reports: ['assessed'] });
  const reportId = reports[0].id;
  await login(page, account);

  await page.goto(`/#/report/${reportId}`);
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await expect(page.getByText('指标总览', { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await expect(page.getByText('指标总览', { exact: true })).toBeVisible();
  expect(page.url()).toContain(`#/report/${reportId}`);
});

test('修正后的指标值优先展示', async ({ page, seed }) => {
  const { account, reports } = await seed({ reports: ['assessed'] });
  const reportId = reports[0].id;

  await page.route(`**/api/health/report/${reportId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: reportId,
        patient_id: account.id,
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

  await openReportFromHistory(page, account);
  const row = page.locator('.metric-overview-card tr').filter({ hasText: '空腹血糖' }).last();
  await expect(row).toContainText('6.4');
  await expect(page.locator('.metric-overview-card').getByText('6.5', { exact: true })).toHaveCount(0);
});

test('待确认报告先显示状态边界，再进入既有确认流程', async ({ page, seed }) => {
  const { account } = await seed({ reports: ['pending_confirmation'] });
  await openReportFromHistory(page, account);

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
  const { account } = await seed({ reports: ['assessed'] });
  await openReportFromHistory(page, account);

  const original = page.getByRole('button', { name: '报告原文' });
  const technical = page.getByRole('button', { name: '技术详情' });
  await expect(original).toHaveAttribute('aria-expanded', 'false');
  await expect(technical).toHaveAttribute('aria-expanded', 'false');

  await original.click();
  await expect(page.getByAltText(/报告原文第 1 页/)).toBeVisible();
  await expect(original).toHaveAttribute('aria-expanded', 'true');
  await expect(technical).toHaveAttribute('aria-expanded', 'false');
});
