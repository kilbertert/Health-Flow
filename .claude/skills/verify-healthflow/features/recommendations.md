# 健康提示与商品推荐

## Sub-features

- 命中已发布知识卡时渲染推荐内容
- 商品卡片渲染，但浏览器**不请求商城域名**（读取发生在服务端）
- 商城不可达时如实降级
- 「无已发布知识卡」与「无可推荐商品」显示不同原因

## How to get to it (user POV)

报告详情页下方「健康提示 / 推荐」区域。

## Driving it with Playwright

```js
const seeded = await seed({ reports: ['assessed'] });

await loginWithSeed(page, seeded);                                  // -> 首页
await page.getByRole('button', { name: '个人中心', exact: true }).click();
await page.getByRole('button', { name: '查看' }).click();            // -> 报告详情
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();

// 推荐区在报告详情下方。要断言「浏览器不请求商城域名」，先挂监听再导航：
const mallRequests = [];
page.on('request', (r) => {
  if (new URL(r.url()).host === 'lkf.h5.mall.qushiyun.com') mallRequests.push(r.url());
});
// ...驱动推荐区...
expect(mallRequests).toEqual([]);
```

## Gotchas

- **可以 mock 本应用自己的 `/api/health/report/*/recommendations`（同源），但绝不
  可以 mock 商城域名。** 真实的 `recommendations.spec.js` 正是这样做的：mock 同源
  端点来构造状态，同时用 `page.on('request')` 记录实际请求，断言**没有任何**请求打到
  `lkf.h5.mall.qushiyun.com`。这条"浏览器不请求商城域名"才是要保护的契约；
  mock 掉商城会直接掩盖它。
- 空态有两种且原因不同，**必须分别断言**；合并成一条会丢失"为什么没有"这个信息。
- 至少保留一条**不 mock 同源端点**的用例（回到真实服务端），否则 mock 本身会
  漂移到与真实契约不一致。
- 商品读取在服务端。断言时检查浏览器实际发出的请求集合，而不是只看渲染结果。
