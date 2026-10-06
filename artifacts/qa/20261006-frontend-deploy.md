# 前端部署与验收记录：体检解读页的商品推荐（2026-10-06）

## 部署的是什么

| 项 | 值 |
| --- | --- |
| 仓库 / 修订 | `health-flow` `06f158d`（`main`，PR #125） |
| 变更 | `Upload.jsx` 在报告 `assessed` 后同时渲染 `Recommendations`；新增只从首页入口进入的 E2E 守卫 |
| 产物 | `health-flow-frontend-06f158d.tar.gz` |
| **产物 sha256** | `46268939c8eb52bb59447df0baeed4883353b2709d8d841ba548c9a6553459c9` |
| 构建位置 | 开发机 `frontend/`，`npm run build`（Node v24.15.0） |
| 目标主机 | `yidong-36`（service host，`36.156.159.175`），入口 `http://36.156.159.175:10007/` |
| 部署时间 | 2026-10-06 11:36（主机本地时间） |
| 回滚点 | `/var/backups/health-flow/frontend-20261006113640.tar.gz`（部署前的前端快照） |

## 部署方式

`dev-host cp 36 … --artifact-sha256 <hash>`（写闸门按产物身份放行）→ 主机侧解包到
`/opt/health-flow/frontend` → `chown -R health-flow:health-flow`。

**未重启服务、也未重新构建后端。** 这是前端静态产物替换：`SERVE_FRONTEND=true` 下由
运行中的进程按请求从磁盘提供 `frontend/`，`index.html` 引用带内容哈希的文件名，
因此换目录即生效。实测证明见下。

## 已核实（实测，非推断）

### 1. 产物在传输与落盘前后逐字节一致

```
本机: 46268939c8eb52bb59447df0baeed4883353b2709d8d841ba548c9a6553459c9
主机: 46268939c8eb52bb59447df0baeed4883353b2709d8d841ba548c9a6553459c9  /opt/health-flow/artifacts/health-flow-frontend-06f158d.tar.gz
```

### 2. 落盘内容与构建输出一致（不是「文件在」，是「是这一版」）

```
index.html         4a0e25e8a81c01844b8e5f11f265f261429dd3d9e74c79e67e6e9de695b419af
assets/index-C1Jj_aLt.js
                   3283d82045ecde1d41fea2aab24b04b63a5f963085df8785016c33ac6eb83635
```

两个哈希与本机 `dist/` 同名文件**逐字节相同**。

### 3. 线上入口实际提供的就是这一版

```
$ curl -s http://36.156.159.175:10007/assets/index-C1Jj_aLt.js | sha256sum
3283d82045ecde1d41fea2aab24b04b63a5f963085df8785016c33ac6eb83635
```

`index.html` 只引用 `index-C1Jj_aLt.js` / `antd-B1Ls2J-n.js` / `index-TRP4qhw0.css`，
与构建输出一致。**部署前**线上是 `index-C0QGrrzm.js`（54179 字节，无推荐挂载），
替换后为 `index-C1Jj_aLt.js`（54408 字节）——差异即本次改动。

### 4. 构建可复现

连续两次 `rm -rf dist && npm run build`，全量文件 sha256 逐条相同。因此上面的产物
哈希是可复算的，不是一次性巧合。

### 5. 部署前的既有产物已清理

解包会保留旧文件，而 `index.html` 已不再引用它们，导致「旧包仍在盘上」。已删除三个
上一版的 bundle（`antd-BlKgdRk9.js`、`index-C0QGrrzm.js`、`react-B2eJIqMy.js`），
现在 `frontend/assets/` 恰好等于本产物。

### 6. 服务在部署后健康

```
GET /ready → {"status":"ready","database":"ok","evidence_service":"configured",
              "mall_goods":"configured","report_provider":"configured",
              "report_owner":"account","account_auth":"required",
              "report_model":"deepseek/deepseek-v4.1-flash"}
systemctl is-active health-flow → active
```

`mall_goods: configured` 说明商城的四项凭据齐备，取商品这条链路具备运行条件。

## 验收结果

**在产物上执行（本地 E2E，构建产物即被测对象）：**

```
npx playwright test e2e/recommendations-from-entry.spec.js e2e/recommendations.spec.js
6 passed
```

含 `QA-EV-011`（从首页入口走完 → 推荐出现在当前页）与其负控。

**负控（本记录的前置证据）：** 关掉推荐挂载 → **重建 `dist`** → 用例失败在
`expect(card).toBeVisible()`（`element(s) not found`），即用户报告的症状；恢复后通过。

## 未执行 / 阻塞的验收（不得计为通过）

### ❌ 线上真实上传 → 推荐 未执行

`GET /api/health/report/{id}/recommendations` 受会话保护（`account_auth: required`），
而本机**没有创建会话的途径**：账号体系已退役（#172/#117），会话只能由商城票据建立，
票据由 `hstclub.com` 的入口页签发。我无法从本机完成「登录 → 上传 → 确认 → 生成健康提示」
这条线上路径。

**因此线上行为未由我核实。** 请操作者按下面的步骤亲自确认（见「操作者的复验步骤」）。

### ❌ `ops/service-host/upload-probe.sh` 已过期，且会误报失败

该探针的步骤顺序是 #172 之前的（注册账号 → 用会话上传）。实测线上：

```
POST /api/auth/register → 405（该方法在线上不存在）
POST /api/auth/ticket   → 422（存在，但需要商城票据）
```

它会在 `register` 一步得到 405 而判失败，**那是探针过期，不是服务故障**。
未运行它。**修正它属于另一张票**，本次不改（避免把部署记录和探针修复混在一个改动里）。

### ❌ `QA-EV-009`（按标签取货）线上仍未执行

`qa-plan.md` 里该用例的状态仍是「可执行，待执行；尚未端到端执行，故不记为通过」。
本次部署**不改变**这一状态——它需要真实登录态，同上的阻塞。

## 覆盖范围的真相（不要据此扩大宣称）

- **12 个健康方向里只有 `COND_DYSLIPIDEMIA` 一行**在真实报告上端到端跑通过
  （genesis `docs/condition-to-mall-tag.md`）。本次修好的是**入口**，不是「每个方向都有货」。
- 目标租户商品 `stock` 全为 `null`，商品卡片会显示「库存未标注」而非数量。
  `QA-EV-008` 至今**未通过**，阻断在商城数据侧。

## 操作者的复验步骤

前置：商城侧登录，且已有一份 `assessed` 或有血脂相关异常的报告。

1. 从 `hstclub.com` 的「健康服务」入口进入，或直接打开 `http://36.156.159.175:10007/`。
2. 走**首页「体检报告解读」按钮**（不要直接开 `/#/report/<id>`——那会绕过本次修的入口）。
3. 上传报告 → 等解析 → 「确认并生成健康提示」。
4. **预期**：停留在体检解读页，页面下方出现「推荐商品」卡片。
5. **预期**：卡片内有商品（该租户血脂方向有货），库存显示「库存未标注」。
6. **反例（回归）**：若卡片显示「该健康方向暂无可推荐的已上架商品」，说明入口修好了但
   该方向的货/标签没到位——那是商城侧的事，不是本次缺陷复发。
   若**整个卡片都不出现**，那才是本次缺陷复发。

## 回滚

```bash
# 以服务身份，或在主机上以 root：
cd /opt/health-flow/frontend
tar -xzf /var/backups/health-flow/frontend-20261006113640.tar.gz
chown -R health-flow:health-flow /opt/health-flow/frontend
```

回滚后 `index.html` 重新指向 `index-C0QGrrzm.js`。
