# 报告历史列表

## Sub-features

- 列出当前主体名下的报告
- 每项渲染指标数与异常摘要
- 每项提供「查看」入口进入报告详情

## How to get to it (user POV)

建立会话 → 首页 → 「个人中心」→ 「报告历史」。列表项右侧的「查看」进入报告详情。

**不在「体检解读」下** —— 那是另一个视图。历史列表挂在个人中心页面。

## Driving it with Playwright

```js
const seeded = await seed({ reports: ['assessed'] });
await loginWithSeed(page, seeded);

await page.getByRole('button', { name: '个人中心', exact: true }).click();
await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
await expect(page.getByRole('heading', { name: '报告历史' })).toBeVisible();

const historyItem = page.locator('.history-section .ant-list-item').first();
await expect(historyItem).toContainText('体检报告');
await expect(historyItem).toContainText('已完成');
await expect(historyItem).toContainText('3 项指标');
await expect(historyItem).toContainText('2 项偏高/偏低');
```

## Gotchas

- 列表项本身**不是** button，`getByRole('button', { name: '查看' })` 只在展开的
  列表行里存在，且依赖渲染完成。稳定的锚点是 `.history-section .ant-list-item`。
- 「查看」按钮的 accessible name 就是裸的 `查看`，页面上可能同时存在多个 ——
  要 `.first()` 或先限定到具体列表项。
- 报告按账户隔离；`seed()` 每次调用创建全新账户，所以列表**只会**出现本次 seed 的报告。
- reduced-motion 环境下断言同样成立（见 `report-history.spec.js`）。
