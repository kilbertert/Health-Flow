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

**全部 16 个文件逐条比对**，本机 `dist/` 与主机 `/opt/health-flow/frontend` **完全一致**：

```
5ef6a4797dea9ae72f9b0eb030acd79f8f80c25169ffb2c96cf0215cf6379346  assets/antd-B1Ls2J-n.js
3283d82045ecde1d41fea2aab24b04b63a5f963085df8785016c33ac6eb83635  assets/index-C1Jj_aLt.js
450afbbb767f2864c4000618e1e280ac1bf0202f240181c17c8cab1778b9a362  assets/index-TRP4qhw0.css
ef4052ae9434f7e24d0022ad1ddc3190194bfade5bbbeb1e92dce0dd80ad6203  assets/react-BDF8K8oT.js
351403b2978b8494eddfef83844732286c21693b72917251a75e5f1ff64a6b07  hst-club-logo.png
4a0e25e8a81c01844b8e5f11f265f261429dd3d9e74c79e67e6e9de695b419af  index.html
911dc691ed0ffdc6ebb6e230dfffbd313370d328a013440a8440aeb273677d00  products/active-folate.png
f63f9c7d0b0eb5aaf1638fd81632120d823afd131417600d57fc914bd49fc964  products/calcium-citrate.png
b0780669af447b4a60d0634de6b9835db7d5719a68fa275422093d1095dce47c  products/cardiotonic-element.png
027aa29056c14a1ea17751d9e0e5f4edd24f37d93345234bf1d94e50c7cb4877  products/joyees-fruit-vegetable-fiber.png
5862326087c5d8972ced7619683ffce2f63d2e71967a162191b34a301a1a0cd2  products/milk-thistle-alpha-lipoic.png
0676f4edcac250b00da9ebd98491f0fd3cbbd19aec4e2261488b7eee3a1b80b2  products/quercetin.png
876cb685093c6d2d3af9266923f3fd46bfecb887cbbc0ffab1e6d07bf5474ed6  products/super-bc.png
3f86551399480594d644908a294709e44be69c921e0f4a3d079c2e24cbbe480a  products/vitamin-d3.png
2057bcf025a58305a4bdee2845769c3756b4c74fc1d1cfbeaa8d6d9f64a39bc8  products/whole-bone-nutrition-meal.png
e0f9a133822f34bdc03e0350133674f4ea9dba88ed2c7c4ab813b7f6bc893160  products/zhizhen-plant-sterol.png
```

**这一条是实测的对照，不是抽查**：重新构建后，把本机 `dist/` 与主机
`/opt/health-flow/frontend` 各自的**全量** `sha256sum` 清单排序后 `diff`，结果为空
（16/16 逐字节相同）。命令：

```bash
# 本机（注意：先把带 dist/ 前缀的路径交给 sha256sum，**之后**才去掉前缀显示；
# 顺序反过来会让 sha256sum 找不到文件，得到的是空清单而不是 16 行）
cd frontend && rm -rf dist && npm run build
find dist -type f | sort | xargs sha256sum | sed 's|  dist/|  |' > /tmp/local.txt

# 主机
dev-host exec 36 --allow-service-exec -- "sh -c 'cd /opt/health-flow/frontend && \
  find . -type f | sort | xargs sha256sum | sed \"s|  \\./|  |\"'" > /tmp/host.txt

diff <(sort /tmp/local.txt) <(sort /tmp/host.txt)   # 期望无输出
wc -l /tmp/local.txt /tmp/host.txt                  # 期望各 16
```

`wc -l` 一并留着：**「diff 无输出」在命令写错时同样成立**（两边都空也是空 diff）。
先确认两边各 16 行，diff 才有意义——这正是本节命令第一版写错的地方。

### 3. 线上入口实际提供的就是这一版

```
$ curl -s http://36.156.159.175:10007/assets/index-C1Jj_aLt.js | sha256sum
3283d82045ecde1d41fea2aab24b04b63a5f963085df8785016c33ac6eb83635
```

`index.html` 只引用 `index-C1Jj_aLt.js` / `antd-B1Ls2J-n.js` / `index-TRP4qhw0.css`，
与构建输出一致。**部署前**线上是 `index-C0QGrrzm.js`（54179 字节，无推荐挂载），
替换后为 `index-C1Jj_aLt.js`（54408 字节）——差异即本次改动。

### 4. 构建可复现；**归档哈希只在同一棵树上可复算**

连续两次 `rm -rf dist && npm run build`，全量文件 sha256 逐条相同——**文件内容**可复现。

**归档哈希不是这样。** `tar` 会把文件 mtime 写进归档，而重新构建会刷新 mtime，
所以「重新构建再打包」得到的是**另一个归档哈希**。这是实测的，不是推断：

```
# 同一棵树、连打两次 → 相同
46268939c8eb52bb59447df0baeed4883353b2709d8d841ba548c9a6553459c9  pack-same-tree.tar.gz
46268939c8eb52bb59447df0baeed4883353b2709d8d841ba548c9a6553459c9  health-flow-frontend-06f158d.tar.gz（已部署）

# 重新构建后再打包 → 不同（文件内容仍逐字节相同，差的是归档里的 mtime）
f30110f98bbc6e41c199382a5d6bb45ac746f5854402d7343a00addb615be75e  pack-after-rebuild.tar.gz
```

工具版本（打包元数据随它们变化，故一并记下）：

```
gzip 1.10
tar (GNU tar) 1.34
```

**所以要分清两件事：**

| 想验的 | 怎么验 | 是否可复算 |
| --- | --- | --- |
| **文件内容**是这一版（有意义的那条） | 比对 `dist/` 内各文件的 sha256 | ✅ 任何机器、任何时间，只要构建工具链一致 |
| **这次传输**没被改动（归档完整性） | 比对归档 sha256 | ⚠️ 只在**同一棵未改动的树**上可复算 |

归档哈希的用途就是后者——它是**那一次传输**的完整性凭据（写闸门 `--artifact-sha256`
正是这样用的），不是长期可复算的版本标识。要复算文件内容：

```bash
cd frontend && rm -rf dist && npm run build
find dist -type f | sort | xargs sha256sum
# 与本记录「落盘与构建输出一致」一节的哈希逐条比对
```

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
```

| 用例 | 文件 | 结果 | 断言的可观测结果 |
| --- | --- | --- | --- |
| 1 | `recommendations-from-entry` | **通过** | 从首页入口走完上传→生成健康提示后，当前页出现推荐卡，含商品名/`¥36.5`/「库存未标注」；浏览器对商城域名请求集合为空 |
| 2 | `recommendations-from-entry` | **通过** | 未生成健康提示时当前页不渲染推荐区 |
| 3 | `recommendations` | **通过** | 命中时渲染商品卡片，且浏览器不请求商城域名 |
| 4 | `recommendations` | **通过** | 真实端点契约下渲染空态（不 mock，回到服务端） |
| 5 | `recommendations` | **通过** | 商城不可达时降级为「暂无推荐」，原因可区分，无错误弹窗 |
| 6 | `recommendations` | **通过** | 无已发布知识卡与无可推荐商品的原因不同 |

**6 passed / 0 failed。**

**证据位置与保留方式：** 用例 1/2 是**可执行**的证据——按上面的命令在
`06f158d` 的检出上重跑即可复现，且已随源码进版本控制
（`frontend/e2e/recommendations-from-entry.spec.js`），比一张截图更耐放。
其余证据（产物哈希、主机落盘哈希、线上 curl 哈希）都在本文件上方逐条列出，
可直接复算。**未保留** Playwright 的 trace/截图产物：它们是本地生成物、
按约定不进版本控制，且断言本身是文本可复现的。

**负控（本记录的前置证据）：** 关掉推荐挂载 → **重建 `dist`** → 用例 1 失败在
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

**必须从商城入口进入，不能直接打开 HTTP 地址。** 二者不等价：会话只能由
`hstclub.com` 的入口页签发一次性票据、再由本服务兑换建立；而兑换时写下的会话
Cookie 带 `Secure`（服务主机配置要求），**`Secure` Cookie 不随明文 HTTP 请求发送**。
因此直接打开 `http://36.156.159.175:10007/` 时页面只会显示无会话提示，
下面的步骤一步都走不了——那不是本次缺陷复发，是这条路径本身不成立。

前置：商城侧登录，且已有一份 `assessed` 或有血脂相关异常的报告。

1. 从 `hstclub.com` 的「健康服务」入口进入（票据由入口页兑换，会话随之建立）。
2. 走**首页「体检报告解读」按钮**（不要直接开 `/#/report/<id>`——那会绕过本次修的入口）。
3. 上传报告 → 等解析 → 「确认并生成健康提示」。
4. **预期**：停留在体检解读页，页面下方出现「推荐商品」卡片。
5. **预期**：卡片内有商品（该租户血脂方向有货），库存显示「库存未标注」。
6. **反例（回归）**：若卡片显示「该健康方向暂无可推荐的已上架商品」，说明入口修好了但
   该方向的货/标签没到位——那是商城侧的事，不是本次缺陷复发。
   若**整个卡片都不出现**，那才是本次缺陷复发。
7. **反例（会话）**：若页面显示无会话提示，先确认是从商城入口进来的；
   从商城入口进不来时，问题在票据兑换/会话，与本次改动无关。

## 回滚

```bash
# 以服务身份，或在主机上以 root：
cd /opt/health-flow/frontend
tar -xzf /var/backups/health-flow/frontend-20261006113640.tar.gz
chown -R health-flow:health-flow /opt/health-flow/frontend
```

回滚后 `index.html` 重新指向 `index-C0QGrrzm.js`。
