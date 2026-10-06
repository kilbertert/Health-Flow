# 移动端底部导航

## Sub-features

- 底部三标签直达 首页 / 体检解读 / 我的
- 未上线业务入口与菜单抽屉**不**出现在移动端导航
- 登录后桌面导航保留（视口切换时各自正确）

## How to get to it (user POV)

375px 视口下的任何已登录页面，底部固定导航。

## Driving it with Playwright

```js
const seeded = await seed({ reports: ['assessed'] });
await loginWithSeed(page, seeded);
const nav = page.getByRole('navigation', { name: '移动端主导航' });
await expect(nav).toBeVisible();
await nav.getByRole('button', { name: '我的' }).click();
await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
```

## Gotchas

- 默认视口就是 375×667，**不需要**显式设置；但桌面用例必须 `test.use({ viewport })`
  覆盖，否则两种导航的断言会互相打架。
- 底部导航与桌面「健康服务」导航是**两个** `navigation` landmark，用 accessible name 区分。
- 「未上线业务入口不出现」是一条真实契约，别只断言想要的入口存在。
- 固定导航不得遮挡内容、底部退出按钮可见 —— 属于布局契约，见 `responsive-layout`。
