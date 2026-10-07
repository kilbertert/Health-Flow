// 回归：多值行不能被当成「确认」就这么过去。
//
// 病根：化验单常有两列数值（真实报告里 LDL-C = "3.39 3.63"），而后端
// `evidence_bridge._single_number` 要求**恰好一个**数；两个数返回 None，
// 整行以 reason=invalid_value 被丢弃。界面却默认把这种行当「确认」，
// 用户点一下确认 → 值原样提交 → 后端丢掉 → 报告变成「没有发现异常指标」。
// 用户从没被告知那个值没被采纳。
//
// 断言三层：卡片/表格上先标明「用不了」、默认决策不是「确认」、
// 提交时被挡下。任何把默认改回「确认」的改动都会让它变红。
import { test, expect, loginWithSeed } from './fixtures.js';

const MULTI_VALUE = {
  id: 2008,
  metric_name: 'Non-HDL 非高密度脂蛋白胆固醇',
  metric_value: '3.87 4.00', // ← 两个数
  unit: 'mmol/L',
  reference_range: '<3.40',
  abnormal_flag: 'H',
  page_number: 1,
  evidence_text: '* Non-HDL 非高密度脂蛋白胆固醇 3.87 4.00 mmol/L (<3.40)',
  confirmation_status: null,
  // 服务端的判定:两个数解析不出一个数 → 判定为空（reason=invalid_value）。
  inferred_abnormal_flag: null,
  confirmed_value: null,
  confirmed_unit: null,
  confirmed_reference_range: null,
  confirmed_evidence_text: null,
};

const SINGLE_VALUE = {
  ...MULTI_VALUE,
  id: 2009,
  metric_name: 'Triglyceride 三酸甘油酯',
  metric_value: '1.05',
  reference_range: '<1.70',
  inferred_abnormal_flag: 'N',
  evidence_text: 'Triglyceride 三酸甘油酯 1.05 mmol/L (<1.70)',
};

const MARKER = '数值无法识别为单个数字';

function json(route, body) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function wire(page, reportId, report, onConfirm) {
  await page.route(new RegExp(`/api/health/report/${reportId}/metrics`), (route) =>
    json(route, { metrics: [MULTI_VALUE, SINGLE_VALUE] }),
  );
  await page.route(new RegExp(`/api/health/report/${reportId}/confirm`), (route) => {
    onConfirm();
    return json(route, report);
  });
  // 精确匹配报告本体（不带子路径），否则会把 confirm/metrics 一起吞掉。
  await page.route(new RegExp(`/api/health/report/${reportId}$`), (route) => json(route, report));
}

async function openPendingReport(page, seeded, reportId) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await page.getByRole('button', { name: '继续确认' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  expect(reportId).toBeGreaterThan(0);
}

function pendingReport(seeded) {
  return {
    id: seeded.reports[0].id,
    patient_id: seeded.subject.owner_id,
    report_type: '体检报告',
    department: '健康管理中心',
    created_at: '2026-10-06T00:00:00.000Z',
    status: 'pending_confirmation',
    subject_consistency: 'same',
    metrics: [MULTI_VALUE, SINGLE_VALUE],
    files: [],
    processing_warnings: [],
  };
}

test('多值行先标明用不了，默认不确认，提交被挡下', async ({ page, seed }) => {
  test.setTimeout(90_000);
  const seeded = await seed({ reports: ['pending_confirmation'] });
  const report = pendingReport(seeded);
  let confirmed = false;
  await wire(page, report.id, report, () => { confirmed = true; });

  await openPendingReport(page, seeded, report.id);

  // 提交**之前**就要看到标记——这是本条修复的核心。
  await expect(page.getByText(MARKER).first()).toBeVisible();
  // 单值行不该被贴上同一个标记。
  const markerCount = await page.getByText(MARKER).count();
  expect(markerCount).toBe(1);

  // 什么都不改就提交：被挡下，且不得发出确认请求。
  await page.getByRole('button', { name: '确认并生成健康提示' }).click();
  await expect(page.getByText(/个异常候选项需要确认、修正或排除/)).toBeVisible();
  expect(confirmed).toBe(false);
});

test('显式把多值行改成确认也被挡下，并说明该怎么办', async ({ page, seed }) => {
  test.setTimeout(90_000);
  const seeded = await seed({ reports: ['pending_confirmation'] });
  const report = pendingReport(seeded);
  let confirmed = false;
  await wire(page, report.id, report, () => { confirmed = true; });

  await openPendingReport(page, seeded, report.id);

  // 卡片默认折叠，先展开才能改「处理」。
  await page.getByRole('button', { name: /Non-HDL.*指标卡片/ }).click();
  // 把多值行显式选成「确认」，再提交——仍应被挡下。
  await page.getByLabel(/Non-HDL.*处理方式/).click();
  await page.locator('.ant-select-item-option').filter({ hasText: '确认' }).first().click();

  await page.getByRole('button', { name: '确认并生成健康提示' }).click();
  await expect(page.getByText(/无法识别为单个数字.*请「修正」为单个数值或「排除」/)).toBeVisible();
  expect(confirmed).toBe(false);
});

test('桌面表格形态同样标明多值行', async ({ page, seed }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const seeded = await seed({ reports: ['pending_confirmation'] });
  const report = pendingReport(seeded);
  await wire(page, report.id, report, () => {});

  await openPendingReport(page, seeded, report.id);

  // 桌面端默认是**表格**（真实截图就是这个形态），标记必须也在那里。
  const row = page.locator('tr').filter({ hasText: 'Non-HDL' });
  await expect(row).toBeVisible();
  await expect(row.getByText(MARKER)).toBeVisible();
});
