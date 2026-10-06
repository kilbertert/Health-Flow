# 报告详情与原文溯源

## Sub-features

- 指标总览（含异常标记）
- 指标卡片可展开，查看技术详情
- 原文溯源弹窗：定位到报告原始材料的页码/坐标并高亮
- hash 路由深链（刷新后恢复同一报告）
- 打印媒体下仅保留报告抬头、指标总览与健康提示
- 微信内置浏览器打印引导

## How to get to it (user POV)

报告列表项「查看」，或直接访问带 hash 的深链 URL。

## Driving it with Playwright

```js
const seeded = await seed({ reports: ['assessed'] });
await loginWithSeed(page, seeded);
await page.getByRole('button', { name: '查看' }).first().click();
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();

// 原文溯源：按钮名形如「查看<指标名>原文」
await page.getByRole('button', { name: '查看空腹血糖原文' }).click();

// 深链：刷新后应恢复到同一报告
await page.reload();
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
```

## Gotchas

- **深链刷新必须单独断言。** 应用内跳转通过，不代表刷新后能恢复 —— 这是 hash 路由
  最容易漏的一条。
- 打印行为用 `page.emulateMedia({ media: 'print' })` 驱动，不要靠截图肉眼判断。
- 原文弹窗在桌面视口保留 960px 宽度；移动端全屏。视口不同断言不同，用
  `test.use({ viewport })` 明确声明。
- 技术详情默认收起，需先点「技术详情」再断言内容。
