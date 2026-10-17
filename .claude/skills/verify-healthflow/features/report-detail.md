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
const reportId = seeded.reports[0].id;

// 路径一：从个人中心进入
await loginWithSeed(page, seeded);                                  // -> 首页
await page.getByRole('button', { name: '个人中心', exact: true }).click();
await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
await page.getByRole('button', { name: '查看' }).click();
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
expect(page.url()).toContain(`#/report/${reportId}`);

// 路径二：深链直达（不需要经过列表）
await page.goto(`/#/report/${reportId}`);
await expect(page.getByText('指标总览', { exact: true })).toBeVisible();

// 深链的价值在**刷新后仍能恢复** —— goto 之后必须 reload 再断言一次，
// 只断言 goto 等于没测到这条契约。
await page.reload();
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
await expect(page.getByText('指标总览', { exact: true })).toBeVisible();

// 原文溯源：按钮 accessible name 形如「查看<指标名>原文」
await page.getByRole('button', { name: '查看空腹血糖原文' }).click();
```

## Sibling spec

**原文页数**（读得出 / 读不出）是这条链路的一部分，证据在
`frontend/e2e/unknown-page-count.spec.js`：服务端读不出页数时 `page_count` 是
`null`，页面**隐藏翻页器**而不是显示「共 1 页」；页数确定时翻页器照常。

## Gotchas

- **`loginWithSeed` 只到首页。** 报告详情在「个人中心」下，不经这一步
  `getByRole('button', { name: '查看' })` 必然超时。
- **深链刷新必须单独断言** —— 应用内跳转通过，不代表刷新后能恢复。这是 hash 路由
  最容易漏的一条，也是两条独立的真实用例。
- 打印行为用 `page.emulateMedia({ media: 'print' })` 驱动，不要靠截图肉眼判断。
- 原文弹窗在桌面视口保留 960px 宽度；移动端全屏。视口不同断言不同，用
  `test.use({ viewport })` 明确声明。
- **量弹窗几何量前必须等入场动画结束。** antd 的 Modal 有 `antZoomIn` 入场动画
  （`scale(0.2)→scale(1)`，约 360ms），而 `boundingBox()` / `getBoundingClientRect()`
  返回的是**渲染盒** —— 动画期间被 transform 缩放。一打开就量宽度会取到
  `960 × 0.2 = 192`（移动端同理 `375 × 0.2 = 75`）。这不是布局缺陷：`offsetWidth`
  在动画的每一帧都是 960。`report-layout.spec.js` 的 `settledBox()` 是正确写法
  （等类名摘掉 `ant-zoom-appear` 且没有在跑的动画）；**不要**用 `expect.poll` 去「碰」
  那个瞬时值 —— 它会在动画跑起来之前的一帧读到 960 而当场通过，紧接着的断言又读到动画
  中间的 192，于是只在并发负载下才红（#234）。
- 技术详情默认收起，需先点「技术详情」再断言内容。

## 报告指标计数（#245）

计数由**服务端**给出（`counts`：`parsed` / `abnormal` / `needs_review` / `undecided`），
前端只渲染。要证明这条，不能只看「页面上有数字」——要构造一条**计入一个数、不计入另一个
数**的行：

- 一行 `abnormal_flag: 'H'` 而 `metric_value: '3.39 / 3.63'`（值判不出单值）→
  `inferred_abnormal_flag: null`，它计入 `needs_review`、**不计入** `abnormal`，
  且 `undecided` 为真。
- 断言报告单**不出现**「均在参考区间内」（那句话在有未判定行时是假的），
  改为「另有 N 项未能判定」。

可跑的先例：`e2e/report-counts.spec.js`（mock 同源报告端点喂入上面那份响应）。
