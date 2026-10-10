// 回归：页面上出现两个值的行，不能被当成「确认」就这么过去（#129 → #204）。
//
// 病根：化验单常有两列数值（真实报告里 LDL-C 印着 3.63，患者手写 3.39，OCR 如实转录成
// "3.39 / 3.63"）。下游要求**恰好一个**数，于是整行被丢弃；而界面把这种行当「确认」，
// 用户点一下 → 值原样提交 → 后端丢掉 → 报告变成「没有发现异常指标」，没人告诉他。
//
// #129 的修法是「先标明用不了、默认不确认、提交时挡下」。#204 把它推进了一步：**两个数
// 都在**，患者要做的不是重输一遍数字（他本来就写着那两个数），而是**选一个**。所以这里
// 断言的是：两个候选都列出来、默认不确认、没选就提交会被挡下并说清要做什么、选了之后
// 提交的是一个「修正」且带的是他选的那个数。
//
// 三个时刻各一层，任何把默认改回「确认」、或把选择退回「让他重输」的改动都会变红。
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
  // 服务端的判定：两个数解析不出一个数 → 判定为空（reason=two_values）。
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

// 服务端给这条值的名字（`admission.value_reason` → 准入词表）是 `two_values`，界面把它
// 说成一句患者能懂的话 —— 而且**不是**「数值不是一个数」：那句话让他去重输一个他本来就
// 写着的数字（#204）。
const MARKER = '这一项页面上有两个值，需要选一个';

function json(route, body) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function wire(page, reportId, report, onConfirm) {
  await page.route(new RegExp(`/api/health/report/${reportId}/metrics`), (route) =>
    json(route, { metrics: [MULTI_VALUE, SINGLE_VALUE] }),
  );
  await page.route(new RegExp(`/api/health/report/${reportId}/confirm`), (route) => {
    onConfirm(route.request().postDataJSON());
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
    created_at: '2020-01-01T00:00:00.000Z',
    status: 'pending_confirmation',
    subject_consistency: 'same',
    metrics: [MULTI_VALUE, SINGLE_VALUE],
    files: [],
    processing_warnings: [],
  };
}

test('两个值都列出来，默认不确认，没选就提交被挡下并说清要做什么', async ({ page, seed }) => {
  test.setTimeout(90_000);
  const seeded = await seed({ reports: ['pending_confirmation'] });
  const report = pendingReport(seeded);
  let confirmed = false;
  await wire(page, report.id, report, () => { confirmed = true; });

  await openPendingReport(page, seeded, report.id);

  // 提交**之前**就要看到标记——这是本条修复的核心。
  await expect(page.getByText(MARKER).first()).toBeVisible();
  // 单值行不该被贴上同一个标记。
  expect(await page.getByText(MARKER).count()).toBe(1);

  // 两个候选都在页面上（患者**选**，不是重输）。
  await page.getByRole('button', { name: /Non-HDL.*指标卡片/ }).click();
  const choice = page.getByRole('radiogroup', { name: /Non-HDL.*取值/ });
  await expect(choice).toBeVisible();
  await expect(choice.getByRole('radio')).toHaveCount(2);
  // antd 的 Radio.Button 把 input 藏起来、只渲染标签，所以断言与点击都落在可见的
  // 选项文字上（input 是 hidden 的，对它 toBeVisible 永远失败）。
  await expect(choice.getByText('3.87', { exact: true })).toBeVisible();
  await expect(choice.getByText('4.00', { exact: true })).toBeVisible();

  // 什么都不选就提交：被挡下，且不得发出确认请求。话要说得清是「选一个」而不是
  // 泛泛的「需要确认、修正或排除」——后者说不清他到底要做什么。
  await page.getByRole('button', { name: '确认并生成健康提示' }).click();
  await expect(page.getByText(/页面上有两个值，请选择用哪一个/)).toBeVisible();
  expect(confirmed).toBe(false);
});

test('选了一个之后提交：那是一次「修正」，带的就是他选的那个数', async ({ page, seed }) => {
  test.setTimeout(90_000);
  const seeded = await seed({ reports: ['pending_confirmation'] });
  const report = pendingReport(seeded);
  let body = null;
  await wire(page, report.id, report, (posted) => { body = posted; });

  await openPendingReport(page, seeded, report.id);
  await page.getByRole('button', { name: /Non-HDL.*指标卡片/ }).click();
  await page.getByRole('radiogroup', { name: /Non-HDL.*取值/ }).getByText('4.00', { exact: true }).click();

  await page.getByRole('button', { name: '确认并生成健康提示' }).click();
  expect(body).toBeTruthy();
  const observation = body.observations.find((item) => item.metric_id === MULTI_VALUE.id);
  expect(observation.decision).toBe('corrected');
  expect(observation.value).toBe('4.00');
  // 单位与参考范围沿用这一行已有的，不用他再填一遍。
  expect(observation.unit).toBe('mmol/L');
  expect(observation.reference_range).toBe('<3.40');
});

test('显式把两个值的行改成确认也被挡下，并说明该怎么办', async ({ page, seed }) => {
  test.setTimeout(90_000);
  const seeded = await seed({ reports: ['pending_confirmation'] });
  const report = pendingReport(seeded);
  let confirmed = false;
  await wire(page, report.id, report, () => { confirmed = true; });

  await openPendingReport(page, seeded, report.id);

  // 卡片默认折叠，先展开才能改「处理」。
  await page.getByRole('button', { name: /Non-HDL.*指标卡片/ }).click();
  // 把多值行显式选成「确认」，再提交——仍应被挡下（后端会连行丢掉）。
  await page.getByLabel(/Non-HDL.*处理方式/).click();
  await page.locator('.ant-select-item-option').filter({ hasText: '确认' }).first().click();

  await page.getByRole('button', { name: '确认并生成健康提示' }).click();
  await expect(page.getByText(/项未进入解读.*请「修正」或「排除」/)).toBeVisible();
  expect(confirmed).toBe(false);
});

test('桌面表格形态同样给出选择', async ({ page, seed }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const seeded = await seed({ reports: ['pending_confirmation'] });
  const report = pendingReport(seeded);
  await wire(page, report.id, report, () => {});

  await openPendingReport(page, seeded, report.id);

  // 桌面端默认是**表格**（真实截图就是这个形态），选择必须也在那里。
  const row = page.locator('tr').filter({ hasText: 'Non-HDL' });
  await expect(row).toBeVisible();
  await expect(row.getByText(MARKER)).toBeVisible();
  const choice = row.getByRole('radiogroup', { name: /Non-HDL.*取值/ });
  await expect(choice.getByText('3.87', { exact: true })).toBeVisible();
  await expect(choice.getByText('4.00', { exact: true })).toBeVisible();
});
