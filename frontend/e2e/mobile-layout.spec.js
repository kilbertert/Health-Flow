// 移动端布局地基:E2E 断言覆盖首页/登录注册/个人中心三个视图,
// 验证 375px 与 414px 下无横向溢出、固定导航不遮挡内容、页面底部完整可见。
import { test, expect, loginWithSeed } from './fixtures.js';

function horizontalExcess(page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

// 把指针移开，**并且等悬浮浮层（antd Tooltip）消失**，再量横向溢出。
//
// 为什么：Playwright 的 `click()` 会先把指针移到按钮中心 —— 于是点完页头那个「个人中心」
// 图标按钮之后，它的 antd Tooltip 是**显示着**的。Tooltip 的定位容器贴着屏幕右缘（375
// 视口下 right=379），会给文档带来 4px 的横向溢出。而它出现有约 100ms 的延迟 —— 于是
// 「点完立刻量」平时量到 0（浮层还没出来）、并发负载下量到 3~4（浮层已经出来了），
// 这就是 mobile-layout 那条偶发红的根因（#234：它和弹窗几何量那条不是同一个机制，但同
// 一类错误 —— 量了一个由测试自己的交互造成的、会变的中间状态）。
//
// 判据用「浮层是否还在 DOM 里」，不是 `waitForTimeout`：移开指针后 antd 会先藏着再移除，
// 轮询到它消失为止，不靠猜。
async function excessWithoutHoverOverlay(page) {
  await page.mouse.move(1, 1);
  await expect.poll(() => page.locator('.ant-tooltip:visible').count()).toBe(0);
  return horizontalExcess(page);
}

function headerClearsContent(page, selector) {
  return page.evaluate(({ selector }) => {
    const header = document.querySelector('.app-header');
    const content = document.querySelector(selector);
    if (!header || !content) return false;
    return header.getBoundingClientRect().bottom <= content.getBoundingClientRect().top + 1;
  }, { selector });
}

[375, 414].forEach((width) => {
  test.describe(`移动端 ${width}px`, () => {
    test.use({ viewport: { width, height: 667 } });

    test('无会话视图无横向溢出且卡片完整落在视口内', async ({ page, seed }) => {
      // #172:登录注册页整体退役,未建立会话时展示的是「请从商城入口进入」页。
      // 这条用例断言的布局约束没变(无横向溢出、卡片落在视口内),换的是那个页面。
      await seed({ reports: ['assessed'] });
      await page.goto('/');
      await expect(page.getByRole('heading', { name: '请从商城入口进入' })).toBeVisible();
      expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);

      const card = page.locator('.auth-card');
      await expect(card).toBeVisible();
      const box = await card.boundingBox();
      expect(box).not.toBeNull();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
    });

    test('首页与个人中心无横向溢出，固定导航不遮挡，底部退出按钮可见', async ({ page, seed }) => {
      const seeded = await seed({ reports: ['assessed'] });
      await loginWithSeed(page, seeded);

      expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);
      expect(await headerClearsContent(page, '.home-page')).toBeTruthy();

      await page.getByRole('button', { name: '个人中心', exact: true }).click();
      await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
      expect(await excessWithoutHoverOverlay(page)).toBeLessThanOrEqual(0);
      expect(await headerClearsContent(page, '.profile-page')).toBeTruthy();

      const email = page.locator('.profile-identity p');
      await expect(email).toBeVisible();
      expect(await email.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);

      const logout = page.getByRole('button', { name: '退出登录' });
      await logout.scrollIntoViewIfNeeded();
      const box = await logout.boundingBox();
      expect(box).not.toBeNull();
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.y + box.height).toBeLessThanOrEqual(667 + 1);
    });
  });
});

test.describe('桌面端', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('登录后首页保留桌面导航且无横向溢出', async ({ page, seed }) => {
    const seeded = await seed();
    await loginWithSeed(page, seeded);

    const nav = page.getByRole('navigation', { name: '健康服务' });
    await expect(nav).toBeVisible();
    await expect(nav.getByRole('button')).toHaveCount(4);
    expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);
  });
});
