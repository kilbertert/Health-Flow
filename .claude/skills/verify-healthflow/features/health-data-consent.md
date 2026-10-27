# 健康数据同意

## Sub-features

- `GET /api/auth/consent` 返回两项（`report_interpretation`、`product_recommendation`）的
  当前状态、**当前要求的文本版本**、以及每项 `valid`
- 没有记录的一项是 `decision: null, valid: false` —— 「从未表过态」是一个读得到的状态，
  不是缺失的键
- `POST /api/auth/consent` 记录一次表态（`granted` / `revoked`），**只追加**；撤回是又一条
  记录，此前的行一条都不改写
- 文本版本改变后，此前的同意读取为 `valid: false`（`decision` 仍是 `granted`）
- 没有有效的「报告解读」同意时，`POST /api/health/report/upload` 返回 **403**，
  `X-Report-Access-Reason: report_interpretation_consent_required`，且**在读取文件之前**
- 记录同意之后，同一主体的上传照常（202）；撤回后闸门重新关上
- **撤回「报告解读」删除该主体的全部报告**（#272）：`GET /api/auth/reports` 随之为空，
  旧报告详情返回 404 + `report_not_found`；撤回记录带 `deleted_report_count`（起始份数 +
  本次上传的份数）。撤回「商品推荐」只让推荐返回「未同意」，报告一份都不动
- 来源由会话通道判定：Bearer → `mall_h5`，Cookie → `legacy_web`，种子夹具 → `seed`

## How to get to it (user POV)

本仓**没有同意界面**——同意页是商城 H5 模块的一部分（PRD #266），界面那一半不在本仓。
患者看到的是两件事：首次进入模块时被征求同意，以及**未同意时上传被拒**。

本切片之前，旧前端（同源 Cookie 通道）没有同意页，所以它现在上传会得到 403。这是 PRD
明确接受的已知后果：旧通道没有真实患者，且即将退役。

## Driving it with Playwright

`e2e/consent.spec.js`。它开一张**商城源**上的真页面（API 端口 +1，由 `CORS_ORIGINS` 放行），
再从那个页面里用 Bearer 调接口 —— 同源请求不经过 CORS，用它证明跨源等于没证明（与
[cross-origin-bearer](cross-origin-bearer.md) 同一个理由）。

```bash
cd frontend
npm run build && ./node_modules/.bin/playwright test e2e/consent.spec.js
```

```js
// 种子默认代主体授予「报告解读」（来源 seed）。要一个从未表过态的主体：
const seeded = await seed({ reports: [], consent: [] });
await page.goto(`${mallOrigin}/`);

// 读取状态（跨源，Bearer）
const status = await page.evaluate(async ({ apiBase, token }) => {
  const res = await fetch(`${apiBase}/api/auth/consent`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.json();
}, { apiBase: apiOrigin, token: seeded.subject.session_token });
// status.items.find(i => i.item === 'report_interpretation').valid === false

// 上传被拒时，**在页面里** res.headers.get 才读得到那个头
//   → 403，accessReason === 'report_interpretation_consent_required'
//
// POST /api/auth/consent {item, decision} 之后同一主体上传得到 202；
// 再 POST {decision: 'revoked'}，上传重新 403。
```

另外一条**界面路径**（旧前端，Cookie 会话）：`loginWithSeed` → 首页「体检报告解读」→
上传一个 PNG → 点「上传并解析」→ 页面告警里出现「尚未同意「报告解读」授权」。它证明闸门
不挑通道：Cookie 与 Bearer 走同一条判定。

撤回的**界面后果**（#272）：种两份报告 → 个人中心 → 「报告历史」里两条「查看」→ 用同一
主体的令牌 `POST /api/auth/consent {decision: 'revoked'}` → `page.reload()` → 个人中心 →
「报告历史」显示「还没有报告记录」，一条「查看」都没有。撤回页本身在商城 H5 模块里，不在
本仓；能在这里证明的是**它删对了**。

## Gotchas

- **本仓没有同意页。** 不要在这里找一个勾选框；要驱动患者侧交互，去商城 H5 模块。
- **种子必须授予同意。** `seed()` 默认已授予「报告解读」（`scripts/e2e_seed.py`）；
  传 `consent: []` 才是「从未表过态」。不传而忘了这一点，用例会在上传处得到 403，
  看起来像上传坏了。
- **`X-Report-Access-Reason` 只能在页面上下文里 `res.headers.get` 断言**；用
  `page.waitForResponse(...).headers()` 会绕过浏览器的暴露头过滤，「没暴露」也能读到。
- **文本版本来自配置**（`CONSENT_REPORT_INTERPRETATION_VERSION` / `..._PRODUCT_...`）。
  改它会让所有既有同意静默失效 —— 方向是安全的（要求重新同意），但会让 E2E 的种子
  在两次运行之间表现不同；不要在同一份数据库上跨版本断言。
- 「报告解读」与「商品推荐」**彼此独立**：授予其一不会让另一项有效，推荐闸门在下一张单。

## Evidence kept

`artifacts/qa/20261027-consent-revocation.md` —— #272 的实测记录：撤回前后报告历史的两张截图
（`revocation-01-before.png` / `revocation-02-after.png`，gitignored 的 `var/verify-evidence/`）
与那次撤回记录的实际响应。

`artifacts/qa/20261027-health-consent-gate.md` — 2026-10-27 的实测记录：界面路径的三张截图
（首页 → 选文件 → 403 告警）落在 `var/verify-evidence/consent-0*.png`，跨源四条用例与服务端
日志 `POST /api/health/report/upload 403 Forbidden` 写在同一份记录里。
