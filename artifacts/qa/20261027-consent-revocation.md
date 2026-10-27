# 撤回同意：撤回报告解读即删除该主体全部报告（2026-10-27）

## 验证的是什么

PRD #266 子单 **#272**。撤回「报告解读」之后，该主体的报告**真的不在了** —— 不是「标记
为删除」，不是「前端隐藏」：

- 报告行连同级联的指标、页图行、审计事件、抽取任务一起消失，磁盘上的原始文件也删除；
- 报告列表随即为空，旧报告详情返回 `404` + `X-Report-Access-Reason: report_not_found`；
- 上传重新被 403 拒绝（`report_interpretation_consent_required`），重新同意后恢复；
- 撤回「商品推荐」只停止推荐，报告一份都不动；
- 撤回记录里写着这次删掉的份数，此前的同意记录一个字段都没有被改写。

「保留审计事件」是 PRD 原文，按票面的订正改为随报告删除（`ReportAuditEvent.report_id` 是
对报告的非空外键，且能追溯到主体）；留证由撤回记录承担。见 `docs/adr/0029`。

## 怎么跑的

```bash
cd frontend
npm run build
./node_modules/.bin/playwright test e2e/consent.spec.js   # 6 passed (10.9s)
uv run pytest tests/test_consent_revocation.py -q          # 5 passed
uv run pytest -n 4                                        # 见下方「全量」
uv run ruff check                                         # All checks passed
node .sandcastle/adr.check.mjs --base origin/main          # ok, 28 records
```

浏览器：Playwright 自带 Chromium（375×667），一次性 SQLite 沙箱，跑完由 `teardown.mjs` 删除。

## 界面证据（截图在 `var/verify-evidence/`，gitignored）

种两份报告（`assessed` + `pending_confirmation`），`loginWithSeed` 种下 Cookie 会话。

| 步骤 | 证据 | 看到什么 |
| --- | --- | --- |
| 个人中心 → 报告历史 | `revocation-01-before.png` | 两条记录，各有一个「查看」 |
| 用同一主体令牌撤回「报告解读」 | — | 响应里的那一项：`decision=revoked, valid=false, deleted_report_count=2` |
| 刷新 → 个人中心 → 报告历史 | `revocation-02-after.png` | 「还没有报告记录」，一个「查看」都没有 |

那次撤回收到的实际响应（`itemOf(body, 'report_interpretation')`）：

```json
{"item":"report_interpretation","decision":"revoked","text_version":"draft-2026-10-27",
 "recorded_at":"2026-10-27T14:02:19.317023","source":"mall_h5",
 "required_text_version":"draft-2026-10-27","valid":false,"deleted_report_count":2}
```

## 跨源路径（`e2e/consent.spec.js`）

商城 H5 在另一个源上调用本仓，所以这几条从**真的商城源页面**里发请求（同源请求不经过
CORS，用它证明跨源等于没证明）。

1. **授予 → 上传 202 → 撤回 → 报告列表为空 → 上传重新 403**：撤回记录里
   `deleted_report_count` 等于起始份数 + 本次上传的 1 份；`GET /api/auth/reports` 变成 `[]`。
2. **撤回后旧报告打不开**：种两份，撤回后 `deleted_report_count=2`，每份的报告详情都是
   `404` 且具名原因是 `report_not_found` —— 不是 403（那意味着报告还在、只是没授权）。
3. **旧前端的报告历史肉眼可见地空了**：撤回前两条「查看」，刷新后「还没有报告记录」。

## 服务端用例（`tests/test_consent_revocation.py`，全部打 HTTP 层）

1. 两份报告（其中一份带抽取任务与 2 条审计事件）+ 另一个主体的报告：撤回后本主体的报告 /
   指标 / 页图行 / 审计事件 / 抽取任务全部为 0，磁盘文件与目录都不存在；另一个主体的行与
   文件原样；授予那条同意记录的六个字段未被改写，撤回记录写 2。
2. 一份报告都没有的主体撤回：`deleted_report_count` 是 `0`（真实的「一份也没删」），不是空。
3. 撤回后上传 403 + 具名原因；重新同意后又可以上传（202）。
4. 撤回「商品推荐」：推荐返回 `product_recommendation_consent_required` 且 `items`/`groups`
   为空，报告与文件一份不少，「报告解读」仍然有效；这一项的 `deleted_report_count` 为空。
5. **中途失败**：删除已经发出、提交之前抛错 —— 报告、审计事件与磁盘文件全部还在，撤回记录
   没有落下，状态读取仍是「已同意」。这是「先删库、后删文件」换来的性质；反过来会留下
   指向不存在文件的报告行。

## 迁移

`health_data_consents.deleted_report_count` 是 #269 之后新增的列，而部署宿主的这张表由
`create_all` 建、`create_all` 不改既有表。`MySQLClient.create_tables` 的列迁移补上它
（幂等、历史行原样保留），`tests/test_mysql_client.py` 用旧 schema 建的库断言这一点 ——
没有它，撤回会在写入那一列时失败，而那时报告已经被删了。

## 覆盖不到

- 「隐私与授权」页面与二次确认交互：在商城 H5 模块里，不在本仓。本仓这一半是「收到撤回就
  执行，不再二次询问」（票面明确）。
- 商城账号本身的注销：属于商城。
- 上传 → VLM 解析 → 证据匹配：需要 LLM 密钥与 `genesis-evidence`，E2E 用种子替代其产物。
