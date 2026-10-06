# HealthFlow feature map

One file per user-facing feature. Each answers, from the patient's point of view:
what it is, how to reach it, how to drive it, and what observable end state proves it works.

The map is the maintained verification source. **A proof that drives one convenient
entry point is incomplete when this index lists others.**

| Feature | Surface | Existing spec | Reaches it by |
|---|---|---|---|
| [report-history](report-history.md) | 报告列表 | `e2e/report-history.spec.js` | 首页 → 体检解读 |
| [report-detail](report-detail.md) | 报告详情（含原文溯源） | `e2e/report-detail.spec.js`, `report-layout.spec.js` | 列表项「查看」/ hash 深链 |
| [indicator-confirmation](indicator-confirmation.md) | 指标核对与确认 | `e2e/report-confirmation.spec.js` | 待确认报告 → 「继续确认」 |
| [recommendations](recommendations.md) | 健康提示与商品推荐 | `e2e/recommendations.spec.js` | 报告详情下方 |
| [paste-upload](paste-upload.md) | 粘贴图片上传 | `e2e/paste.spec.js` | 首页上传区 / 移动粘贴区 |
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
