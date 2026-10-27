# 商品推荐同意闸门与按健康问题分组：浏览器验证记录（2026-10-27）

## 验证的是什么

PRD #266 子单 **#271** —— 「商品推荐」同意闸门，以及推荐响应的 `groups`。

患者可见的部分只有一句空态文案：**没有同意推荐时，推荐区说「尚未开启商品推荐授权，
开启后即可看到推荐商品。」**，而不是兜底的「该报告暂无可推荐的商品」或「商城暂时不可用」。
闸门「不调用商城」与分组本身没有界面可点（分组是给商城 H5 模块消费的契约），由 HTTP 层
测试断言。

## 怎么跑的

```bash
cd frontend
npm run build
./node_modules/.bin/playwright test                            # 89 passed (52.1s)
uv run pytest -n 4                                             # 757 passed, 19 skipped
uv run ruff check                                              # All checks passed
node .sandcastle/adr.check.mjs --all                           # ok, 27 records
node .sandcastle/policy-check.mjs commit                       # passed
```

浏览器：Playwright 自带 Chromium（`/opt/ms-playwright`），375×667，一次性 SQLite 沙箱
（`/tmp/healthflow-e2e-*`，跑完由 `teardown.mjs` 删除）。

## 界面路径（截图在 `var/verify-evidence/`，gitignored）

种子建一份 `assessed` 报告，`loginWithSeed` 种下 Cookie 会话，走 首页 → 报告详情。

| 步骤 | 证据 | 看到什么 |
| --- | --- | --- |
| 首页 | `271-01-home.png` | 「呵护您的健康」 |
| 报告详情（种子**没有**给「商品推荐」同意） | `271-02-gate-empty-state.png` | 推荐区：「暂无推荐 / **尚未开启商品推荐授权，开启后即可看到推荐商品。**」 |
| 报告详情（种子给了「商品推荐」同意） | `271-03-with-consent.png` | 推荐区照常渲染，走到真实端点 |

从**真实应用**读到的响应：

```
GET /api/health/report/1/recommendations      (无推荐同意)
→ 200 {"items": [], "groups": [], "reason": "product_recommendation_consent_required"}

GET /api/health/report/N/recommendations      (有推荐同意，种子报告没有 findings)
→ 200 {"items": [], "reason": "no_published_card", "groups": []}
```

同一趟的服务端日志只有一次 `recommendations` 请求，且是 200 —— 闸门不是一个错误页。

## 服务端行为（HTTP 层测试）

`tests/test_recommendation_gate.py`（新增，14 条）与 `tests/test_mall_goods.py`（沿用既有
商城替身）：

- **闸门**：没有同意 / 撤回 / 写在旧文本版本上 / 只有「报告解读」同意 → 四种情形都返回
  `product_recommendation_consent_required`，且 `fetch_goods` **一次都没有被调用**
  （负控：只断言空响应的话，「先问商城、再丢结果」的实现会同样通过）。
- **闸门先于报告条件**：没有风险、也没有同意时，得到的是「未同意」，不是
  `no_published_card`。
- **同一次判定**：闸门的结论与 `GET /api/auth/consent` 的 `valid` 逐值一致。
- **分组**：两个健康方向、两组商品 → `groups` 各带自己的商品，且每件都出现在扁平
  `items` 里；一组没货只让那一组为空（`no_label_data`），另一组照常；商城整体不可达时
  每组都带 `mall_unavailable`；没有映射的方向不发取货请求、组说 `no_label_data`。
- **旧契约不变**：`items` / `reason` 仍在，取值与改动前一致（`groups` 是可选的附加字段）。

## 覆盖不到

- 分组在**商城 H5 模块**里的展示（「针对 X 偏高」的标题、按组排列）：在商城代码库，
  不在本仓。本仓只保证 `groups` 的形状与归属正确。
- 商城侧「为什么推荐」块：同样在商城模块。
- **真实商城数据下的分组**：商城今天不返回商品的标签字段，所以实跑时每组都空、整份响应
  落到 `no_label_data`；分组逻辑由测试里的带标签商品驱动。这与 ADR 0005 的
  「标签数据到位后需复验」是同一条未通过的 QA 用例，本片不让它变绿。
- 上传 → VLM 解析 → 证据匹配：需要 LLM 密钥与 `genesis-evidence`，E2E 用种子替代其产物。
