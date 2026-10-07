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
          // 生效值由服务端算好；夹具必须给出真实形状，否则测的是不存在的响应。
          effective_value: '6.4',
          effective_unit: 'mmol/L',
          effective_reference_range: '3.9-6.1',
          effective_evidence_text: '空腹血糖 6.4 mmol/L ↑',
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

// 指标生效值（#144）：确认页与报告单必须显示**同一个**值。
//
// 收敛前，报告单抄了 `confirmed_x || x`、确认页指标卡只读模型值 —— 患者从历史
// 列表重入一份已修正的报告，报告单显示 6.4、确认页显示 6.5，两个界面两个「结果」。
// 修正草稿同样只读模型值：重入后输入框是空的，得从零重输二十项核对结果。
/** 一份已修正的报告：模型值 6.5、患者改成 6.4。 */
function correctedReport(seeded, reportId) {
  return {
    id: reportId,
    patient_id: seeded.subject.owner_id,
    report_type: '体检报告',
    department: '健康管理中心',
    created_at: new Date().toISOString(),
    status: 'confirmed',
    subject_consistency: 'same',
    metrics: [
      {
        id: 1,
        report_id: reportId,
        metric_name: '空腹血糖',
        metric_value: '6.5',
        unit: 'mmol/L',
        reference_range: '3.9-6.1',
        abnormal_flag: 'H',
        inferred_abnormal_flag: 'H',
        page_number: 1,
        evidence_text: '空腹血糖 6.5 mmol/L ↑',
        confirmation_status: 'corrected',
        confirmed_value: '6.4',
        confirmed_unit: 'mmol/L',
        confirmed_reference_range: '3.9-6.1',
        confirmed_evidence_text: '空腹血糖 6.4 mmol/L ↑',
        effective_value: '6.4',
        effective_unit: 'mmol/L',
        effective_reference_range: '3.9-6.1',
        effective_evidence_text: '空腹血糖 6.4 mmol/L ↑',
      },
    ],
    files: [],
    evidence_result: null,
    processing_warnings: [],
  };
}

test.describe('指标生效值（桌面确认表）', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('桌面确认表显示生效值，与移动端卡片、报告单一致', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    const reportId = seeded.reports[0].id;
    const report = correctedReport(seeded, reportId);
    report.status = 'pending_confirmation';
    await page.route(`**/api/health/report/${reportId}`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(report) }),
    );
    await page.route(`**/api/health/report/${reportId}/metrics`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ metrics: report.metrics }),
      }),
    );

    await loginWithSeed(page, seeded);
    await page.getByRole('button', { name: '个人中心', exact: true }).click();
    await page.getByRole('button', { name: '查看' }).click();
    await page.getByRole('button', { name: '继续确认' }).click();
    await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();

    const row = page.getByRole('table').filter({ has: page.getByRole('columnheader', { name: '结果' }) })
      .locator('tr', { hasText: '空腹血糖' });
    await expect(row).toContainText('6.4');
    await expect(row).not.toContainText('6.5');
  });

});

test.describe('指标生效值（重入确认页，移动端卡片）', () => {
  test.use({ viewport: { width: 375, height: 667 } });


  test('重入已修正的报告：报告单与确认页显示同一个值，草稿预填它', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    const reportId = seeded.reports[0].id;
    const report = correctedReport(seeded, reportId);
    // 报告是 `pending_confirmation`（患者还没确认，正要在这一屏确认），
    // 指标行带的是上次核对过的值 —— 这正是「重入确认页」的真实状态。
    report.status = 'pending_confirmation';
    await page.route(`**/api/health/report/${reportId}`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(report) }),
    );
    await page.route(`**/api/health/report/${reportId}/metrics`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ metrics: report.metrics }),
      }),
    );

    await loginWithSeed(page, seeded);
    // 从报告单的确认入口进入（报告单本身的生效值断言在桌面那组，那里是表格形态）。
    await page.goto(`/#/report/${reportId}`);
    await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
    await page.getByRole('button', { name: '继续确认' }).click();
    await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
    const card = page.getByRole('button', { name: '空腹血糖指标卡片' });
    await expect(card).toContainText('6.4');
    await expect(card).not.toContainText('6.5');

    // 展开卡片：修正草稿预填 6.4（不是空，也不是 6.5）。
    await card.click();
    await expect(page.getByLabel('空腹血糖修正值')).toHaveValue('6.4');
  });

  test('患者排除的指标不出现在报告单总览', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    const reportId = seeded.reports[0].id;
    const report = correctedReport(seeded, reportId);
    report.status = 'assessed';
    report.metrics = [
      ...report.metrics,
      {
        id: 2,
        report_id: reportId,
        metric_name: '被排除的指标',
        metric_value: '9.9',
        unit: 'mmol/L',
        reference_range: '3.9-6.1',
        abnormal_flag: 'H',
        inferred_abnormal_flag: null,
        page_number: 1,
        evidence_text: '被排除的指标 9.9 mmol/L ↑',
        confirmation_status: 'excluded',
        effective_value: null,
        effective_unit: null,
        effective_reference_range: null,
        effective_evidence_text: null,
      },
    ];
    await page.route(`**/api/health/report/${reportId}`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(report) }),
    );

    await loginWithSeed(page, seeded);
    await page.goto(`/#/report/${reportId}`);
    await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
    const overview = page.locator('.metric-overview-card');
    await expect(overview.locator('tr', { hasText: '空腹血糖' })).toHaveCount(1);
    await expect(overview.getByText('被排除的指标')).toHaveCount(0);
  });
});
