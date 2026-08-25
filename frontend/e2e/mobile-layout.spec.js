// 移动端布局地基:E2E 断言覆盖首页/登录注册/个人中心三个视图,
// 验证 375px 与 414px 下无横向溢出、固定导航不遮挡内容、页面底部完整可见。
import { test, expect } from './fixtures.js';

async function login(page, account) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
}

function horizontalExcess(page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
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

    test('登录注册视图无横向溢出且卡片完整落在视口内', async ({ page }) => {
      await page.goto('/');
      await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
      expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);

      const card = page.locator('.auth-card');
      await expect(card).toBeVisible();
      const box = await card.boundingBox();
      expect(box).not.toBeNull();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
    });

    test('首页与个人中心无横向溢出，固定导航不遮挡，底部退出按钮可见', async ({ page, seed }) => {
      const { account } = await seed({ reports: ['assessed'] });
      await login(page, account);

      expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);
      expect(await headerClearsContent(page, '.home-page')).toBeTruthy();

      await page.getByRole('button', { name: '个人中心', exact: true }).click();
      await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
      expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);
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
    const { account } = await seed();
    await login(page, account);

    const nav = page.getByRole('navigation', { name: '健康服务' });
    await expect(nav).toBeVisible();
    await expect(nav.getByRole('button')).toHaveCount(4);
    expect(await horizontalExcess(page)).toBeLessThanOrEqual(0);
  });
});
