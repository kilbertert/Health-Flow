// 指标确认表卡片化(E2E):
// 375/414px 下确认表渲染为常显名称/数值/异常状态的指标卡片,
// 展开卡片后可进行修正并提交确认;桌面端仍使用表格形态。
import { test, expect } from './fixtures.js';

async function login(page, account) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: /^登\s*录$/ }).click();
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
}

async function openPendingReport(page, account) {
  await login(page, account);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await page.getByRole('button', { name: '继续确认' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  await expect(page.getByText(/解析结果/)).toBeVisible();
}

function assessedResponse(account, reportUrl) {
  const id = Number(reportUrl.split('/report/')[1].split('/')[0]);
  return {
    id,
    patient_id: account.id,
    report_type: '体检报告',
    department: '健康管理中心',
    created_at: new Date().toISOString(),
    status: 'assessed',
    subject_consistency: 'same',
    metrics: [],
    files: [],
    evidence_result: {
      schema_version: '2',
      sorting_version: 'published-card-reference-range-v1',
      correlation_id: 'e2e-mobile-confirmation',
      findings: [{
        condition_code: 'COND_DYSLIPIDEMIA',
        condition_name: '血脂异常',
        product_status: 'available',
        recommendation_message: '以下为可考虑的健康管理建议',
        recommendations: [{
          recommendation_id: 'recommendation-1',
          product_id: 'product-1',
          product_name: '郅臻堂®植物甾醇咀嚼片',
          nutrient: '植物甾醇',
          reason: '可作为血脂相关营养管理的一种膳食补充方向考虑。',
          safety_message: '请结合个人情况咨询专业人士。',
          disclaimer: '本建议为健康管理参考，不构成医疗或用药指令。',
          evidence_links: ['classification:test.xlsx#血脂管理'],
          evidence_strength: 'low',
          priority: 10,
        }],
      }],
      unmatched: [],
      skipped: [],
      message: 'E2E 移动端确认完成。',
      patient_reply: {
        title: '体检报告解读与健康风险提示',
        summary: 'E2E 移动端确认完成。',
        findings: [{
          condition_code: 'COND_DYSLIPIDEMIA',
          condition_name: '血脂异常',
          product_status: 'available',
          recommendation_message: '以下为可考虑的健康管理建议',
          recommendations: [{
            recommendation_id: 'recommendation-1',
            product_id: 'product-1',
            product_name: '郅臻堂®植物甾醇咀嚼片',
            nutrient: '植物甾醇',
            reason: '可作为血脂相关营养管理的一种膳食补充方向考虑。',
            safety_message: '请结合个人情况咨询专业人士。',
            disclaimer: '本建议为健康管理参考，不构成医疗或用药指令。',
            evidence_links: ['classification:test.xlsx#血脂管理'],
            evidence_strength: 'low',
            priority: 10,
          }],
        }],
        unmatched_count: 0,
        disclaimer: '本解读仅提供健康辅助建议。',
      },
    },
    processing_warnings: [],
  };
}

[375, 414].forEach((width) => {
  test.describe(`指标确认卡片 ${width}px`, () => {
    test.use({ viewport: { width, height: 667 } });

    test('卡片常显指标信息，可展开修正并提交确认', async ({ page, seed }) => {
      const { account } = await seed({ reports: ['pending_confirmation'] });
      await openPendingReport(page, account);

      const card = page.getByRole('button', { name: '甘油三酯指标卡片' });
      await expect(card).toBeVisible();
      await expect(card).toHaveAttribute('aria-expanded', 'false');
      await expect(card).toContainText('甘油三酯');
      await expect(card).toContainText('2.3');
      await expect(card).toContainText('mmol/L');
      await expect(card).toContainText('H 偏高');

      await card.click();
      await expect(card).toHaveAttribute('aria-expanded', 'true');
      await page.getByLabel('甘油三酯处理方式').click();
      await page
        .locator('.ant-select-item-option')
        .filter({ hasText: '修正' })
        .click();

      await page.getByLabel('甘油三酯修正值').fill('1.9');
      await page.getByLabel('甘油三酯修正单位').fill('mmol/L');
      await page.getByLabel('甘油三酯修正参考范围').fill('0.45-1.7');
      await page
        .getByLabel('甘油三酯修正原文证据')
        .fill('甘油三酯 1.9 mmol/L 参考范围 0.45-1.7');

      let confirmationBody;
      await page.route('**/api/health/report/*/confirm', async (route) => {
        confirmationBody = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(assessedResponse(account, route.request().url())),
        });
      });

      await page.getByRole('button', { name: '确认并生成健康提示' }).click();
      await expect(page.getByText('E2E 移动端确认完成。')).toBeVisible();
      await expect(page.getByText('郅臻堂®植物甾醇咀嚼片')).toBeVisible();
      await expect(page.getByRole('img', { name: '郅臻堂®植物甾醇咀嚼片产品包装图' })).toBeVisible();
      await expect(page.getByText('植物甾醇', { exact: true })).toBeVisible();
      await expect(page.getByText('本建议为健康管理参考，不构成医疗或用药指令。')).toBeVisible();
      expect(confirmationBody).toBeTruthy();
      expect(
        confirmationBody.observations.some(
          (observation) =>
            observation.decision === 'corrected' &&
            observation.value === '1.9' &&
            observation.unit === 'mmol/L' &&
            observation.reference_range === '0.45-1.7',
        ),
      ).toBeTruthy();
    });
  });
});

test.describe('指标确认表格桌面端', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('确认表保持表格形态，不渲染卡片', async ({ page, seed }) => {
    const { account } = await seed({ reports: ['pending_confirmation'] });
    await openPendingReport(page, account);

    const confirmationTable = page.getByRole('table').filter({
      has: page.getByRole('columnheader', { name: '指标' }),
    });
    await expect(confirmationTable).toBeVisible();
    await expect(confirmationTable.getByRole('columnheader', { name: '指标', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '甘油三酯指标卡片' })).toHaveCount(0);
    await expect(page.locator('.metric-card-list')).toHaveCount(0);
  });
});
