# HealthFlow feature map

One file per user-facing feature. Each answers, from the patient's point of view:
what it is, how to reach it, how to drive it, and what observable end state proves it works.

The map is the maintained verification source. **A proof that drives one convenient
entry point is incomplete when this index lists others.**

## Canonical navigation

`loginWithSeed()` lands on **首页 only**. Almost nothing on this map is reachable from
there — every example below must traverse first. These are the real paths, taken from
the specs themselves, not inferred:

**每条路径都从首页独立开始。** 不要把它们串成一个连续序列——「体检报告解读」这个
导航按钮只在**首页**存在，进了确认流程之后再点它是找不到的。在每个用例里重新
`loginWithSeed` 即可回到首页。

```js
// A. 报告历史 / 报告详情 / 指标确认 —— 都挂在个人中心下
const seeded = await seed({ reports: ['assessed'] });
await loginWithSeed(page, seeded);                          // -> 首页「呵护您的健康」
await page.getByRole('button', { name: '个人中心', exact: true }).click();
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible(); // 或 报告历史

// B. 报告详情（从历史进入，或深链直达）
await page.getByRole('button', { name: '查看' }).click();
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
// 或：await page.goto(`/#/report/${seeded.reports[0].id}`);

// C. 指标确认（从报告详情进入）
await page.getByRole('button', { name: '继续确认' }).click();
await expect(page.getByText(/解析结果/)).toBeVisible();

// D. 上传页 —— 回到首页再导航，按钮名是「体检报告解读」
await loginWithSeed(page, seeded);                          // 重置回首页
await page.getByRole('button', { name: '体检报告解读' }).click();
await expect(page.getByText('点击或拖拽多张报告文件到此区域')).toBeVisible();
```

两个容易踩的点：

- **「体检报告解读」是双关**：首页上它是导航按钮（→ 上传页），确认流程里它是一级标题。
  按 name 找 heading 会撞车，用所在页面或伴随文本区分（上传页「点击或拖拽…」／
  确认流程「解析结果」）。
- **种多个报告时「查看」不唯一**。历史列表每个报告一个「查看」按钮，
  `getByRole('button', { name: '查看' })` 会匹配多个并在 strict mode 下报错。
  只种一个报告，或把定位限定到具体列表项。

| Feature | Surface | Existing spec | Reaches it by |
|---|---|---|---|
| [report-history](report-history.md) | 报告列表 | `e2e/report-history.spec.js` | 首页 → **个人中心 → 报告历史** |
| [report-detail](report-detail.md) | 报告详情（含原文溯源） | `e2e/report-detail.spec.js`, `report-layout.spec.js` | **个人中心 → 查看**，或 hash 深链 `#/report/<id>` |
| [indicator-confirmation](indicator-confirmation.md) | 指标核对与确认 | `e2e/report-confirmation.spec.js` | 个人中心 → 查看 → **继续确认** |
| [recommendations](recommendations.md) | 健康提示与商品推荐 | `e2e/recommendations.spec.js` | 同上进入报告详情，内容在其下方 |
| [paste-upload](paste-upload.md) | 粘贴图片上传 | `e2e/paste.spec.js` | 首页 → **体检报告解读**（导航按钮）→ 上传区 |
| [mobile-navigation](mobile-navigation.md) | 移动端底部导航 | `e2e/mobile-nav.spec.js` | 375px 视口 |
| [responsive-layout](responsive-layout.md) | 移动端布局与溢出 | `e2e/mobile-layout.spec.js`, `report-layout.spec.js` | 375px / 桌面视口 |

## Not on this map

- **上传 → VLM 解析 → 证据匹配** — needs an LLM key and the `genesis-evidence`
  service. E2E substitutes seed data for their products. Drive it only against a live
  deployment, and say so.
- **会话建立 / 登录** — there is no login page; the seed plants a subject session
  cookie. See `loginWithSeed` in `e2e/fixtures.js`.
- **商城侧商品可见性** — 商品读取发生在服务端，前端只与自身同源通信。The
  recommendations feature verifies the *client* contract, not mall state.
