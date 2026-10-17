// #245 报告指标计数的浏览器证据：一条「模型标 H、值判不出来」的行不构成「异常」，
// 因此报告单**不再**在它存在时宣告「均在参考区间内」——那是本改动唯一的患者可见行为变化。
// 计数由服务端给出（`counts`），前端只渲染；这里断言的正是那句话的消失与替代说法。

import { test, expect, loginWithSeed } from './fixtures.js';

function assessedWithUndecided(seeded, reportUrl) {
  const id = Number(reportUrl.split('/report/')[1].split('/')[0]);
  return {
    id,
    patient_id: seeded.subject.owner_id,
    report_type: '体检报告',
    department: '健康管理中心',
    created_at: new Date().toISOString(),
    status: 'assessed',
    subject_consistency: 'same',
    // 服务端给的计数（唯一口径）：**0 项判定为异常**，但 1 项没判出来。
    counts: { parsed: 2, abnormal: 0, needs_review: 1, undecided: 1 },
    metrics: [
      {
        id: 801, report_id: id, metric_name: '血红蛋白', metric_value: '138',
        unit: 'g/L', reference_range: '115-150', abnormal_flag: 'N',
        confirmation_status: 'confirmed', inferred_abnormal_flag: 'N',
        effective_value: '138', effective_unit: 'g/L', effective_reference_range: '115-150',
        page_number: 1, evidence_text: '血红蛋白 138 g/L',
      },
      {
        // 正是 #245 描述的那条可达的行：模型标 H，值是两个数、判不出单值 → 未判定。
        // 它**不计入**「异常」——改动前正因如此，报告单会宣告「均在参考区间内」。
        id: 802, report_id: id, metric_name: '甘油三酯', metric_value: '3.39 / 3.63',
        unit: 'mmol/L', reference_range: '0.45-1.7', abnormal_flag: 'H',
        confirmation_status: 'confirmed', inferred_abnormal_flag: null,
        effective_value: '3.39 / 3.63', effective_unit: 'mmol/L',
        effective_reference_range: '0.45-1.7', page_number: 1,
        evidence_text: '甘油三酯 3.39 / 3.63 mmol/L',
      },
    ],
    files: [],
    evidence_result: {
      correlation_id: 'verify-245', title: '体检报告解读与健康风险提示',
      summary: '取证', findings: [], unmatched: [], skipped: [],
      unmatched_count: 0, disclaimer: '本解读仅提供健康辅助建议。',
    },
  };
}

test('报告单在存在未判定行时不再宣告「均在参考区间内」', async ({ page, seed }) => {
  const seeded = await seed({ reports: ['assessed'] });
  const reportId = seeded.reports[0].id;
  await page.route(`**/api/health/report/${reportId}`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(assessedWithUndecided(seeded, route.request().url())) }),
  );
  await loginWithSeed(page, seeded);
  await page.goto(`/#/report/${reportId}`);
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();

  const summary = page.locator('.report-abnormal-summary');
  await expect(summary).toBeVisible();
  await expect(summary).toContainText('未见异常指标');
  // 关键断言：那句话**不出现**。
  await expect(summary).not.toContainText('均在参考区间内');
  await expect(summary).toContainText('未能判定');

  await page.screenshot({ path: '../var/verify-evidence/245-report-detail-no-false-conclusion.png', fullPage: true });
});
