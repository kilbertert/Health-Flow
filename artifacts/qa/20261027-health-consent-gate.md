# 健康数据同意：上传闸门的浏览器验证记录（2026-10-27）

## 验证的是什么

PRD #266 子单 **#269** —— 「健康数据同意」的记录、状态读取与上传闸门。

本仓在这一片里**没有同意界面**（同意页在商城 H5 模块里），所以「用户点得到什么」只有
一件事：**没有有效「报告解读」同意时，上传被拒，患者看到服务端给的那句话**。其余行为
（只追加、文本版本作废、两项独立）是 API 语义，由 HTTP 层测试断言。

## 怎么跑的

```bash
cd frontend
npm run build
./node_modules/.bin/playwright test e2e/consent.spec.js        # 4 passed (8.3s)
./node_modules/.bin/playwright test                            # 87 passed (51.4s)
uv run pytest -n 4                                             # 741 passed, 19 skipped
uv run ruff check                                              # All checks passed
node .sandcastle/adr.check.mjs --base main                     # ok, 26 records
```

浏览器：Playwright 自带 Chromium（`/opt/ms-playwright`），375×667，一次性 SQLite 沙箱
（`/tmp/healthflow-e2e-*`，跑完由 `teardown.mjs` 删除）。

## 界面路径（截图在 `var/verify-evidence/`，gitignored）

种子用 `consent: []` 建一个**从未表过态**的主体，`loginWithSeed` 种下 Cookie 会话。

| 步骤 | 证据 | 看到什么 |
| --- | --- | --- |
| 首页 | `consent-01-home.png` | 「呵护您的健康」 |
| 首页 →「体检报告解读」→ 选一个 PNG | `consent-02-file-selected.png` | 上传区列出「报告.png」 |
| 点「上传并解析」 | `consent-03-upload-denied-403.png` | 红色告警：**尚未同意「报告解读」授权，请先在商城模块完成同意后再上传报告**；页面上没有「已解析 N 个文件」 |

同一趟的服务端日志：

```
POST /api/health/report/upload HTTP/1.1 403 Forbidden
```

## 跨源路径（`e2e/consent.spec.js`）

商城 H5 在另一个源上调用本仓，所以这四条用例开一张**真的**商城源页面（`mallOrigin`，
API 端口 +1，由 `CORS_ORIGINS` 放行）再从那个页面里 fetch —— 同源请求不经过 CORS，
用它证明跨源等于没证明。

1. **状态读取**：种子授予的「报告解读」是 `decision=granted, valid=true, source=seed`，
   `text_version` 等于服务端给出的 `required_text_version`；「商品推荐」是
   `decision=null, valid=false`（两项独立，默认不勾）。
2. **闸门**：无同意时 `POST /api/health/report/upload` → **403**，页面上下文里
   `res.headers.get('X-Report-Access-Reason') === 'report_interpretation_consent_required'`
   （跨源能读到它，靠的是 `Expose-Headers`，ADR 0026），`detail` 仍是那句人话。
3. **授予 → 202 → 撤回 → 403**：`POST /api/auth/consent` 写入后上传得到 202
   （报告进入 `processing`、抽取任务 `queued`）；撤回后同一条上传重新得到 403。
   写入记录带 `source=mall_h5` —— 来源由服务端按会话通道判定。
4. **闸门不挑通道**：旧前端（Cookie）在同一道闸门前得到同样的 403 与同一句话。

## 本轮顺带修掉的一处脚手架缺陷

`cors-bearer.spec.js` 原来在自己的 `beforeAll` 里 `listen` 商城源。跨源 spec 变成两条之后，
两个 worker 并行跑、各自绑同一个端口 —— 全量运行里 `consent.spec.js` 挂成
`EADDRINUSE: 127.0.0.1:8138`，**红的原因与跨源无关**（83 条里单条失败）。
现在监听由 `e2e/mall-origin.mjs`（Playwright `globalSetup`）起一次、活到本次运行结束，
配置外的那个源仍归 `cors-bearer.spec.js` 自己（没有第二条 spec 会绑它）。

## 覆盖不到

- 同意页本身、勾选框、二次确认：在商城 H5 模块里，不在本仓。
- 「商品推荐」同意在推荐接口上的执行：下一张单。
- 撤回解读同意后的数据删除：下一张单。
- 上传 → VLM 解析 → 证据匹配：需要 LLM 密钥与 `genesis-evidence`，E2E 用种子替代其产物。
  本片只走到 202 为止。
