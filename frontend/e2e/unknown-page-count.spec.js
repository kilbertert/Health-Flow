// 报告原文页数未知（E2E）。
//
// 服务端读不出页数时 `files[].page_count` 是 `null`（未知），不再是「共 1 页」。
// 患者看到的是**没有翻页器**，而不是一个看起来很确定的错值。
//
// 阴面对照（写这条时实际跑过）：把 ReportDetail 的 `hasKnownPageCount` 换成
// `(file?.page_count ?? 1) > 0`，本用例立刻变红（未知的那份会渲染出翻页器）。
import { test, expect, loginWithSeed } from './fixtures.js';

function reportWithPageCount(seeded, reportId, pageCount) {
  return {
    id: reportId,
    patient_id: seeded.subject.owner_id,
    report_type: '体检报告',
    department: '健康管理中心',
    created_at: new Date().toISOString(),
    status: 'assessed',
    subject_consistency: 'same',
    metrics: [],
    files: [
      {
        file_index: 1,
        original_filename: '报告.pdf',
        media_type: 'application/pdf',
        page_count: pageCount,
        source_url: `/api/health/report/${reportId}/files/1/pages/1`,
      },
    ],
    evidence_result: null,
    processing_warnings: [],
  };
}

async function openOriginalSection(page, seeded, reportId, pageCount) {
  await page.route(`**/api/health/report/${reportId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(reportWithPageCount(seeded, reportId, pageCount)),
    }),
  );
  await loginWithSeed(page, seeded);
  await page.goto(`/#/report/${reportId}`);
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await page.getByRole('button', { name: '报告原文' }).click();
  await expect(page.locator('.original-report')).toBeVisible();
}

test('页数未知时隐藏翻页器，不显示「共 1 页」', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  const reportId = seeded.reports[0].id;
  await openOriginalSection(page, seeded, reportId, null);

  // 原文本身照常显示 —— 读不出页数不等于读不出内容。
  await expect(page.getByAltText(/报告原文第 1 页/)).toBeVisible();
  // 但页数**不说**，因为它不知道。
  await expect(page.locator('.original-report-pagination')).toHaveCount(0);
  await expect(page.getByText(/共\s*1\s*页/)).toHaveCount(0);
});

test('页数确定时翻页器照常显示', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  const reportId = seeded.reports[0].id;
  await openOriginalSection(page, seeded, reportId, 3);

  await expect(page.locator('.original-report-pagination')).toBeVisible();
});

test('只有一页时翻页器仍是「只有一页」的形态', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  const reportId = seeded.reports[0].id;
  await openOriginalSection(page, seeded, reportId, 1);

  // 单页的翻页器是 simple 形态（一个「1/1」而不是可点的页码组）；
  // 断言它**存在**，与「未知」那半边的断言相反。
  const pagination = page.locator('.original-report-pagination');
  await expect(pagination).toBeVisible();
  await expect(pagination.locator('.ant-pagination-simple')).toHaveCount(1);
});
