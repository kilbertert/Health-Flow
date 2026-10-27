# 健康提示与商品推荐

## Sub-features

- 命中已发布知识卡时渲染推荐内容
- 商品卡片渲染，但浏览器**不请求商城域名**（读取发生在服务端）
- 商城不可达时如实降级
- 「无已发布知识卡」与「无可推荐商品」显示不同原因
- **「商品推荐」同意闸门**（#271）：没同意时服务端返回 `product_recommendation_consent_required`
  且**一次商城请求都不发**；旧前端显示「尚未开启商品推荐授权」，不显示商品
- **按健康问题分组**（#271）：响应同时含 `groups` 与扁平的 `items` / `reason`；每组带
  `condition_code`、`condition_name`、自己的商品、自己的为空原因

## 种子必须授予「商品推荐」同意

**推荐的每一条用例都要显式给同意**（`seed({ consent: [REPORT_INTERPRETATION,
PRODUCT_RECOMMENDATION] })`）。种子默认只种「报告解读」（#269 的上传闸门要它），而 #271
之后推荐端点也有一道闸门 —— 不给这一项，用例会落到「未同意」的空态，
而它想测的推荐/降级/分组根本没被驱动。`consent.spec.js` 里「推荐默认不勾」那条断言的是
**状态接口**，与这里的种子用法不冲突。

## How to get to it (user POV)

报告详情页下方「健康提示 / 推荐」区域。

## Driving it with Playwright

**监听必须在导航之前挂。** 推荐内容在报告详情挂载时拉取；先导航再挂监听，会在
渲染期间漏掉请求，使断言在没有请求时**假通过**。

```js
const seeded = await seed({ reports: ['assessed'] });

// 1. 先挂监听
const mallRequests = [];
page.on('request', (r) => {
  if (new URL(r.url()).host === 'lkf.h5.mall.qushiyun.com') mallRequests.push(r.url());
});

// 2. 再导航（单个报告，避免 查看 匹配到多个按钮）
await loginWithSeed(page, seeded);                                  // -> 首页
await page.getByRole('button', { name: '个人中心', exact: true }).click();
await page.getByRole('button', { name: '查看' }).click();            // -> 报告详情
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();

// 3. 等推荐区真正加载完再断言
await expect(page.locator('.recommendations-loading')).toHaveCount(0);
await expect(page.locator('.recommendations-card')).toBeVisible();
expect(mallRequests).toEqual([]);
```

**不要用文案当"加载完成"的判据。** 卡片标题「推荐商品」是静态的，页面一挂载就匹配——
拿它做等待条件等于没等，断言会跑在请求之前，漏检迟发的商城请求。等
`.recommendations-loading` 消失才是真正的 settled 条件。

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

## 错误分支的原因（#246）

空态的原因由**服务端具名**出域，错误分支同样如此。要证明归因不是从文本里猜的，必须让
同源端点**按失败形状**返回：

- 报告读不到：`status: 404` + 头 `X-Report-Access-Reason: report_not_found` →
  患者看到「这份报告打不开」，**不是**「商城暂时不可用」。
- 会话失效：`status: 401` + 头 `X-Report-Access-Reason: session_expired` →
  患者被指向**商城入口**（本仓没有登录页，「重新登录」指向一个不存在的入口）。
- 加购失败：`{url: null, reason: 'report_not_ready'}` → 「还没完成确认」那句，
  而不是一句一概而论的「暂时无法跳转到商城」。

可跑的先例：`e2e/recommendations-errors.spec.js`（三条都覆盖；`recommendations.spec.js`
只 mock 2xx，那正是旧的自造成因能活下来的原因）。

## 同意闸门与分组（#271）

**闸门不产生一个 HTTP 错误。** 没有对当前版本的「商品推荐」同意时，推荐端点照常
`200`，`reason` 是 `product_recommendation_consent_required`、`items` 与 `groups` 都是
空。患者侧要看到的那句话指向**开启推荐**，不是「商城暂时不可用」，也不是「暂无商品」。

```
GET /api/health/report/<id>/recommendations   （种子未授予 product_recommendation）
→ 200 {"items": [], "groups": [], "reason": "product_recommendation_consent_required"}
```

驱动方式（有 mock 与真实端点两条，两条都要留）：

```js
// 1) mock：证明旧前端认识这个取值，且不把它说成商城故障
await page.route('**/api/health/report/*/recommendations', (route) =>
  route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ items: [], reason: 'product_recommendation_consent_required', groups: [] }) }),
);
await expect(page.locator('.recommendations-card')).toContainText('尚未开启商品推荐授权');
await expect(page.locator('.recommendations-card')).not.toContainText('商城暂时不可用');

// 2) 真实端点：种子只给 report_interpretation，响应就是那个取值
const seeded = await seed({ reports: ['assessed'] });   // 默认不带推荐同意
```

**闸门「不调商城」只能由服务端证明。** 浏览器看到的空态在「先问商城、再丢掉结果」的
实现下一模一样，所以在 HTTP 层断言 `fetch_goods` **一次都没被调用**
（`tests/test_recommendation_gate.py`）才是负控，不是界面断言。

**分组**（`groups`）与扁平 `items` 同时出域。每组的 `items` 是**那一次按该健康方向取货**
的结果，所以归属由商城的标签查询给出；一件商品只归一组（按健康问题的出现顺序先到先得），
并且一定出现在扁平 `items` 里。组为空时带**它自己的**原因 `no_label_data`，
商城整体不可达时每组都带 `mall_unavailable` —— 后者是整次取货的事实，不能说成「这个
方向没货」。旧前端不读 `groups`，`items` / `reason` 的取值与改动前一致。
