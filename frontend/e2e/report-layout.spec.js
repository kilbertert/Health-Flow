// 报告解读流移动端布局:E2E 覆盖报告查看页在 375/414px 下无页面级横向溢出,
// 技术元数据默认收起且可展开,原文溯源弹窗在移动端全屏且高亮区域可见。
import { test, expect, loginWithSeed } from './fixtures.js';

async function openAssessedReport(page, seeded) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await expect(page.getByText('指标总览', { exact: true })).toBeVisible();
}

function horizontalExcess(page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

// 弹窗**入场动画结束后**的渲染盒。
//
// 为什么必须等：antd 的 Modal 有入场动画（`antZoomIn`，`scale(0.2)→scale(1)`，约 360ms），
// 而 `boundingBox()`/`getBoundingClientRect()` 返回的是**渲染盒** —— 动画期间被 transform
// 缩放。所以一打开就量宽度，取到的可能是 `960 × 0.2 = 192`，而不是 960（#234：全量并发下
// 断言偶尔读到 192；根因是断言与入场动画抢跑，**不是**响应式切换的缺陷 —— 产品的 layout
// 宽度始终是 960，`offsetWidth` 在动画的每一帧都一样）。
//
// 判据是**动画类名 + 动画状态**，不是 `waitForTimeout`：
//   - 打开后的第一帧带 `ant-zoom-appear-start`（此刻还没开始跑，`getAnimations()` 是空的
//     —— 只等「没有动画」会当场通过，读到那个瞬时的 960）；
//   - 动画期间带 `ant-zoom-appear-active`；
//   - 结束时两个类都被摘掉。
// 所以「已静止」= 类名里没有 `ant-zoom-appear` **且**没有在跑的动画。轮询到它就返回，
// 不靠猜 360ms 够不够，也不为保险白等。
async function settledBox(locator) {
  await expect
    .poll(() =>
      locator.evaluate((el) => {
        const appearing = el.className.includes('ant-zoom-appear');
        return appearing || el.getAnimations().length > 0;
      }),
    )
    .toBe(false);
  return locator.boundingBox();
}

[375, 414].forEach((width) => {
  test.describe(`报告解读流 ${width}px`, () => {
    test.use({ viewport: { width, height: 667 } });

    test('页面无横向溢出，技术详情默认收起可展开', async ({ page, seed }) => {
      const seeded = await seed({ reports: ['assessed'] });
      await openAssessedReport(page, seeded);

      await expect.poll(() => horizontalExcess(page)).toBeLessThanOrEqual(0);
      const knowledgeTitle = page.locator('.evidence-result-card .ant-card-head');
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
      const seeded = await seed({ reports: ['assessed'] });
      await openAssessedReport(page, seeded);

      await page.getByRole('button', { name: '查看空腹血糖原文' }).click();
      const modal = page.locator('.source-modal-wrap .ant-modal');
      await expect(modal).toBeVisible();
      await expect(page.getByAltText(/报告原文第 1 页/)).toBeVisible();

      // 移动端：弹窗铺满视口。**等入场动画结束后**再量 —— 动画期间量到的是缩放中的盒子
      // （`375 × 0.2 = 75`），不是它最终的几何量（#234）。
      const box = await settledBox(modal);
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
    const seeded = await seed({ reports: ['assessed'] });
    await openAssessedReport(page, seeded);

    await expect.poll(() => horizontalExcess(page)).toBeLessThanOrEqual(0);
    const technicalDetails = page.getByRole('button', { name: '技术详情' });
    await expect(technicalDetails).toHaveAttribute('aria-expanded', 'false');

    await page.getByRole('button', { name: '查看空腹血糖原文' }).click();
    const modal = page.locator('.source-modal-wrap .ant-modal');
    await expect(modal).toBeVisible();
    await expect(page.getByAltText(/报告原文第 1 页/)).toBeVisible();

    // 桌面端：960px（antd `width={960}`）。**等入场动画结束后**再量 —— 动画第一帧的渲染盒
    // 是 `960 × 0.2 = 192`，正是本票那条间歇性失败的读数（#234）。这里不用 `expect.poll`
    // 去「碰」它：poll 会在动画跑起来之前的那个瞬时帧读到 960 而当场通过，紧接着的断言又
    // 在动画中间读到 192 —— 那个窗口只在负载下才够宽，所以它是一条只在并发下红一次的竞态。
    const box = await settledBox(modal);
    expect(box).not.toBeNull();
    expect(box.width).toBeLessThanOrEqual(961);
    expect(box.width).toBeGreaterThanOrEqual(955);
  });
});
