# 报告计数增加 in_range：浏览器验证记录（2026-10-27）

## 验证的是什么

PRD #266 子单 **#270** —— 报告计数块增加 `in_range`（判定在参考范围内、且患者没有排除的行）。

本片在本仓**没有新的患者可见界面**：患者摘要上的「需关注 / 待核对 / 范围内」三个数画在
商城 H5 模块里（PRD 的实现分工），health-flow 只负责把数**算对并出域**。所以界面这一趟
要证的是「既有页面照常渲染 + 服务端在两个契约里给同一个数」，而数的判据本身由 HTTP 层
测试钉住（`tests/test_auth.py`、`tests/test_metric_counts.py`）。

## 怎么跑的

```bash
cd frontend
npm run build
./node_modules/.bin/playwright test                            # 87 passed (53.6s)
uv run pytest -n 4                                             # 743 passed, 19 skipped
uv run ruff check                                              # All checks passed
node .sandcastle/adr.check.mjs --base origin/main              # ok, 26 records
node .sandcastle/policy-check.mjs commit                       # passed
```

浏览器：Playwright 自带 Chromium（`/opt/ms-playwright`），375×667，一次性 SQLite 沙箱
（`/tmp/healthflow-e2e-*`，跑完由 `teardown.mjs` 删除）。

## 界面路径（截图在 `var/verify-evidence/`，gitignored）

种子建一份 `assessed` 报告，`loginWithSeed` 种下 Cookie 会话，走 首页 → 个人中心 → 查看。

| 步骤 | 证据 | 看到什么 |
| --- | --- | --- |
| 首页 | `270-01-home.png` | 「呵护您的健康」 |
| 个人中心 → 报告历史 | `270-02-history.png` | 报告行照常渲染（「5 项指标 · 2 项异常」，状态「已完成」） |
| 报告详情 | `270-03-report-detail.png` | 「指标总览」与告警块照常渲染，未因契约多一个字段而报错 |

同一趟里从**真实应用**的响应读到的数（`270-counts.json`）：

```json
// GET /api/auth/reports
{ "metric_count": 5, "abnormal_count": 3, "needs_review_count": 3, "in_range": 2 }

// GET /api/health/report/1 → counts
{ "parsed": 5, "abnormal": 3, "needs_review": 3, "undecided": 0, "in_range": 2 }
```

两个契约的 `in_range` 相同（2），且 `abnormal + in_range + undecided = 3 + 2 + 0 = 5 = parsed`
—— 这份种子报告没有已排除行，所以三个互斥出口恰好铺满已解析行数。

## 服务端行为（HTTP 层测试）

`tests/test_auth.py::test_both_count_blocks_carry_in_range_and_agree` 用一份同时含
`H`、`L`、`N`、定性阴性、判不出来、以及两条**已排除**行（一条 H、一条 N）的样例报告断言：

- 两个契约的 `in_range` 相等，且等于 2；
- `abnormal + in_range + undecided == parsed - 已排除行数`；
- 那条「判定为 N、但被患者排除」的行是负控：减法会把它算成「范围内」，而它不在数里。

`tests/test_metric_counts.py` 钉住判据本身（`H` 不算、`N` 算、判不出来不算、排除不算）。

## 覆盖不到

- 患者摘要上「需关注 / 待核对 / 范围内」第三个数的**排版与文案**：在商城 H5 模块里，
  不在本仓。
- 上传 → VLM 解析 → 证据匹配：需要 LLM 密钥与 `genesis-evidence`，E2E 用种子替代其产物。
