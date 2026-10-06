# HealthFlow feature map

One file per user-facing feature. Each answers, from the patient's point of view:
what it is, how to reach it, how to drive it, and what observable end state proves it works.

The map is the maintained verification source. **A proof that drives one convenient
entry point is incomplete when this index lists others.**

## Canonical navigation

`loginWithSeed()` lands on **首页 only**. Almost nothing on this map is reachable from
there — every example below must traverse first. These are the real paths, taken from
the specs themselves, not inferred:

```js
// 账户会话
await loginWithSeed(page, seeded);                    // -> 首页「呵护您的健康」

// 报告历史 / 报告详情 / 指标确认 都挂在个人中心下
await page.getByRole('button', { name: '个人中心', exact: true }).click();
await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
await page.getByRole('button', { name: '查看' }).click();      // 历史列表项 -> 报告详情
await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();

// 从报告详情进入指标确认
await page.getByRole('button', { name: '继续确认' }).click();
await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();  // 解析结果

// 上传页（粘贴的落点）——导航按钮名是「体检报告解读」
await page.getByRole('button', { name: '体检报告解读' }).click();
await expect(page.getByText('点击或拖拽多张报告文件到此区域')).toBeVisible();
```

**「体检报告解读」既是一个导航按钮（→ 上传页），也是确认流程的标题。** 两者靠
上下文区分：上传页有「点击或拖拽多张报告文件到此区域」，确认流程有「解析结果」。

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
