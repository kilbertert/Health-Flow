// 移动端底部三标签导航(E2E):
// 断点以下用 首页/体检解读/我的 代替顶部业务导航与菜单抽屉,
// 未上线业务入口不进入移动端导航,桌面端顶部导航保持不变。
import { test, expect } from './fixtures.js';

async function login(page, account) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
}

const UNLAUNCHED_LABELS = ['中医问诊', '验血咨询', '体脂检测'];

[375, 414].forEach((width) => {
  test.describe(`移动端底部导航 ${width}px`, () => {
    test.use({ viewport: { width, height: 667 } });

    test('底部三标签可直达 首页/体检解读/我的', async ({ page, seed }) => {
      const { account } = await seed({ reports: ['assessed'] });
      await login(page, account);

      const bottomNav = page.getByRole('navigation', { name: '移动端主导航' });
      await expect(bottomNav).toBeVisible();
      await expect(bottomNav.getByRole('button')).toHaveCount(3);
      await expect(bottomNav.getByRole('button', { name: '首页' })).toHaveAttribute('aria-current', 'page');

      await bottomNav.getByRole('button', { name: '体检解读' }).click();
      await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
      await expect(bottomNav.getByRole('button', { name: '体检解读' })).toHaveAttribute('aria-current', 'page');

      await bottomNav.getByRole('button', { name: '我的' }).click();
      await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
      await expect(bottomNav.getByRole('button', { name: '我的' })).toHaveAttribute('aria-current', 'page');

      await bottomNav.getByRole('button', { name: '首页' }).click();
      await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
      await expect(bottomNav.getByRole('button', { name: '首页' })).toHaveAttribute('aria-current', 'page');
    });

    test('未上线业务入口与菜单抽屉不出现在移动端导航', async ({ page, seed }) => {
      const { account } = await seed();
      await login(page, account);

      const bottomNav = page.getByRole('navigation', { name: '移动端主导航' });
      await expect(bottomNav).toBeVisible();
      for (const label of UNLAUNCHED_LABELS) {
        await expect(bottomNav.getByRole('button', { name: label })).toHaveCount(0);
        await expect(page.getByRole('button', { name: label })).toHaveCount(0);
      }

      await expect(page.getByRole('navigation', { name: '健康服务' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: '打开菜单' })).toHaveCount(0);
    });
  });
});

test.describe('桌面端顶部导航', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('底部导航隐藏且顶部服务导航保持不变', async ({ page, seed }) => {
    const { account } = await seed();
    await login(page, account);

    const serviceNav = page.getByRole('navigation', { name: '健康服务' });
    await expect(serviceNav).toBeVisible();
    await expect(serviceNav.getByRole('button')).toHaveCount(4);
    await expect(page.getByRole('navigation', { name: '移动端主导航' })).toHaveCount(0);
  });
});
