// 指标确认表卡片化(E2E):
// 375/414px 下确认表渲染为常显名称/数值/异常状态的指标卡片,
// 展开卡片后可进行修正并提交确认;桌面端仍使用表格形态。
import { test, expect, loginWithSeed } from './fixtures.js';

async function openPendingReport(page, seeded) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await page.getByRole('button', { name: '继续确认' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  await expect(page.getByText(/解析结果/)).toBeVisible();
}

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
    metrics: [],
    files: [],
    evidence_result: {
      schema_version: '2',
      sorting_version: 'published-card-reference-range-v1',
      correlation_id: 'e2e-mobile-confirmation',
      findings: [{
        condition_code: 'COND_DYSLIPIDEMIA',
        condition_name: '血脂异常',
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
      const seeded = await seed({ reports: ['pending_confirmation'] });
      await openPendingReport(page, seeded);

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
          body: JSON.stringify(assessedResponse(seeded, route.request().url())),
        });
      });

      await page.getByRole('button', { name: '确认并生成健康提示' }).click();
      await expect(page.getByText('E2E 移动端确认完成。')).toBeVisible();
      // 证据仍在最上层可见：风险名与证据正文来自契约，不随商品退役一起消失。
      await expect(page.getByText('可能相关健康问题：血脂异常')).toBeVisible();
      await expect(page.getByText('血脂异常')).toBeVisible();
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
    const seeded = await seed({ reports: ['pending_confirmation'] });
    await openPendingReport(page, seeded);

    const confirmationTable = page.getByRole('table').filter({
      has: page.getByRole('columnheader', { name: '指标' }),
    });
    await expect(confirmationTable).toBeVisible();
    await expect(confirmationTable.getByRole('columnheader', { name: '指标', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '甘油三酯指标卡片' })).toHaveCount(0);
    await expect(page.locator('.metric-card-list')).toHaveCount(0);
  });
});
