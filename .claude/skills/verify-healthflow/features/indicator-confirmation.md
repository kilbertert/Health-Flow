# 指标核对与确认

## Sub-features

- 卡片形态常显指标信息
- 展开后修正指标值并提交确认
- 确认表保持表格形态（不渲染卡片）
- 待确认报告先显示状态边界，再进入确认流程

## How to get to it (user POV)

用 `pending_confirmation` 种子报告 → 报告详情 → 「继续确认」。

## Driving it with Playwright

```js
const seeded = await seed({ reports: ['pending_confirmation'] });

await loginWithSeed(page, seeded);                                  // -> 首页
await page.getByRole('button', { name: '个人中心', exact: true }).click();
await page.getByRole('button', { name: '查看' }).click();            // -> 报告详情
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
await page.getByRole('button', { name: '继续确认' }).click();        // -> 确认流程
await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
await expect(page.getByText(/解析结果/)).toBeVisible();

// 卡片常显指标信息 → 展开修正 → 提交
await page.getByRole('button', { name: '甘油三酯指标卡片' }).click();
```

## Gotchas

- **必须先在「个人中心」打开待确认报告**，「继续确认」只存在于报告详情页；
  从首页直接找它必然超时。
- 确认流程的标题也是「体检报告解读」，与上传页同名。区分靠「解析结果」这个文本
  （确认流程有）；上传页则是「点击或拖拽多张报告文件到此区域」。
- **只有确认或修正后的指标才进入知识卡匹配与风险提示生成**；未确认的异常候选指标不参与解读。
  断言时不要期待未确认指标出现在解读结果里。
- 卡片与确认表是两种形态，断言要指明是哪一种 —— 混用会让用例在形态切换后假通过。
- `assessed` 报告已带已确认指标，走不到这个流程；必须用 `pending_confirmation`。
