// 报告解读流移动端布局:E2E 覆盖报告查看页在 375/414px 下无页面级横向溢出,
// 技术元数据默认收起且可展开,原文溯源弹窗在移动端全屏且高亮区域可见。
import { test, expect } from './fixtures.js';

async function login(page, account) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
}

async function openAssessedReport(page, account) {
  await login(page, account);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  await expect(page.getByText(/解析结果/)).toBeVisible();
}

function horizontalExcess(page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

[375, 414].forEach((width) => {
  test.describe(`报告解读流 ${width}px`, () => {
    test.use({ viewport: { width, height: 667 } });

    test('页面无横向溢出，技术详情默认收起可展开', async ({ page, seed }) => {
      const { account } = await seed({ reports: ['assessed'] });
      await openAssessedReport(page, account);

      expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);
      const knowledgeTitle = page.getByText('正式知识卡匹配结果');
      await expect(knowledgeTitle).toBeVisible();
      expect(await knowledgeTitle.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);

      const evidenceSummary = page.getByText('E2E 种子报告:暂无匹配的已发布知识卡。');
      await expect(evidenceSummary).toBeVisible();
      expect(await evidenceSummary.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);

      const technicalDetails = page.getByRole('button', { name: '技术详情' });
      await expect(technicalDetails).toBeVisible();
      await expect(technicalDetails).toHaveAttribute('aria-expanded', 'false');
      await technicalDetails.click();
      await expect(page.getByText('抽取模型', { exact: true })).toBeVisible();
      await expect(technicalDetails).toHaveAttribute('aria-expanded', 'true');
    });

    test('原文溯源弹窗全屏且高亮区域可见', async ({ page, seed }) => {
      const { account } = await seed({ reports: ['assessed'] });
      await openAssessedReport(page, account);

      await page.getByRole('button', { name: '查看空腹血糖原文' }).click();
      const modal = page.locator('.source-modal-wrap .ant-modal');
      await expect(modal).toBeVisible();
      await expect(page.getByAltText(/报告原文第 1 页/)).toBeVisible();

      const box = await modal.boundingBox();
      expect(box).not.toBeNull();
      expect(box.x).toBeLessThanOrEqual(0);
      expect(box.y).toBeLessThanOrEqual(0);
      expect(box.x + box.width).toBeGreaterThanOrEqual(width - 1);
      expect(box.y + box.height).toBeGreaterThanOrEqual(667 - 1);

      const highlight = page.getByLabel('指标原文位置');
      await expect(highlight).toBeVisible();
      const highlightBox = await highlight.boundingBox();
      expect(highlightBox).not.toBeNull();
      expect(highlightBox.width).toBeGreaterThan(0);
      expect(highlightBox.height).toBeGreaterThan(0);
    });
  });
});

test.describe('桌面端报告解读流', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('技术详情收起且原文弹窗保留 960px 宽度', async ({ page, seed }) => {
    const { account } = await seed({ reports: ['assessed'] });
    await openAssessedReport(page, account);

    expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);
    const technicalDetails = page.getByRole('button', { name: '技术详情' });
    await expect(technicalDetails).toHaveAttribute('aria-expanded', 'false');

    await page.getByRole('button', { name: '查看空腹血糖原文' }).click();
    const modal = page.locator('.source-modal-wrap .ant-modal');
    await expect(modal).toBeVisible();
    const box = await modal.boundingBox();
    expect(box).not.toBeNull();
    expect(box.width).toBeLessThanOrEqual(961);
    expect(box.width).toBeGreaterThanOrEqual(955);
  });
});
