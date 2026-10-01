# 证据链路 QA 计划

关联：genesis-evidence #173 / ADR 0006。商品相关用例随商品能力退役一并删除——
它们断言的行为（`product_status`、`recommendations[]`、产品图渲染）已不存在。
证据侧用例在 genesis-evidence 的 `qa-plan.md`（`QA-EVID-001` ~ `004`）执行；
本仓负责契约透传与渲染，用例如下。

## QA-EV-001 证据契约透传

- 环境：HealthFlow Python 测试环境。
- 前置：Genesis Evidence v3 响应含一个 finding 与 `evidence_items`，**不含商品字段**。
- 数据：血脂异常 finding，含卡片正文、Claim 回链与原文证据片段。
- 动作：使用 `EvidenceMatchResponse` 校验响应，并保存到报告评估结果。
- 预期：校验通过且报告进入 `assessed`；持久化后的 JSON 键名遍历不出现 `recommendations`、`recommendation_message`、`product_status`。
- 清理：删除临时 SQLite 数据库。

## QA-EV-002 含商品字段的旧响应被拒

- 环境：同 `QA-EV-001`。
- 前置：构造一份带 `recommendations`、`recommendation_message`、`product_status` 的响应体。
- 数据：在合法响应上追加三个商品字段。
- 动作：用 `EvidenceMatchResponse.model_validate` 校验该响应。
- 预期：抛出 `ValidationError` 且指出多余字段；接口层把它转为 `EvidenceBridgeError`，报告评估返回 503 而不是静默吞掉。
- 清理：同 `QA-EV-001`。

## QA-EV-003 真实报告阴性对照

- 环境：隔离的 HealthFlow、Genesis Evidence API、SQLite 与本机 loopback 动态端口。
- 前置：报告解析模型与 Evidence API 就绪。
- 数据：`中英文双语完整版个人体检报告.pdf`。
- 动作：上传 PDF，等待真实模型解析，检查全部结构化指标与异常判定。
- 预期：68 条指标解析完成且无解析警告；报告内可识别数值均未越过参考范围，因此不产生虚假健康风险。
- 清理：停止临时服务，删除隔离数据库、报告文件与未脱敏日志。

## QA-EV-004 受控异常报告正向全链

- 环境：与 `QA-EV-003` 相同，并使用真实报告解析模型。
- 前置：`COND_DYSLIPIDEMIA` 存在已发布知识卡；确认请求包含完整原文证据和参考上限。
- 数据：明确标注为验收夹具的单页 LDL-C 报告，`4.20 mmol/L`，参考上限 `3.40 mmol/L`。
- 动作：上传报告，等待解析，确认 LDL-C 异常项，请求评估，检查健康风险提示。
- 预期：解析标记为 `H`；报告进入 `assessed`；匹配 `COND_DYSLIPIDEMIA`；返回卡片正文与证据回链；`unmatched=[]` 且 `skipped=[]`；响应不含任何商品字段。
- 清理：停止临时服务，删除隔离数据库、报告夹具与未脱敏日志。

## QA-EV-005 检测页商品由服务端代理读取（#174）

- 环境：HealthFlow 测试环境 + **部署配置指定的租户**（`MALL_WEBAPI_TENANT_ID`）+ 商城只读端点。
  租户标识**不写死在这里**：它是部署配置，换租户不该要求改文档。本仓代码与测试里
  同样不出现任何真实租户字面量——测试用构造串，真实 id 进测试等于把业务数据写进代码。
  **目标租户已于 2026-09-29 订正**（此前的验收集指向了一个非目标租户）；本仓不记录租户
  id，只记录「由 `MALL_WEBAPI_TENANT_ID` 决定」。
- 前置：`MALL_WEBAPI_*` 四项经服务环境注入；报告已 `assessed` 且含一个已确认健康风险。
- 数据：该租户真实在售商品（首次执行时该租户 20 条）。
- 动作：请求 `GET /api/health/report/{id}/recommendations`，并在浏览器打开报告详情页，
  同时记录页面发出的全部请求 URL。
- 预期：服务端向 `POST /mallapi/webapi/goods/read` 发出带签名请求（`tenant-id` 与
  `mall-app-id` 头齐备）；页面请求中**不含任何商城域名**。
- 清理：无需清理（只读）。

## QA-EV-006 空态四种原因可区分

- 环境：同 `QA-EV-005`，商城可用性可控。
- 前置：分别构造四种情形。
- 数据：无 `findings` 的报告 / 无标签映射 / 有标签映射但无货 / 商城不可达。
- 动作：逐一请求推荐端点。
- 预期：`reason` 依次为 `no_published_card`（且不调用商城）、`no_label_data`、
  `no_label_data`、`mall_unavailable`；命中时为 `null` 且有 `items`。四种不得合并成
  一种说法。
- 清理：无需清理。

## QA-EV-007 商城不可达时页面降级不报错

- 环境：同 `QA-EV-005`，把商城基址指向不可达地址。
- 前置：报告已 `assessed` 且含一个健康风险。
- 数据：无。
- 动作：在浏览器打开报告详情页。
- 预期：显示「暂无推荐」并说明商城暂时不可用；页面无错误弹窗；不出现任何商品卡片；
  指标与证据区域不受影响。
- 清理：恢复商城基址。

## QA-EV-008 库存未标注不渲染为零 —— **未通过**

- 环境：同 `QA-EV-005`。
- 前置：商城对该租户返回的商品 `stock` 字段为 `null`。
- 数据：实测（2026-09-29）该租户 20 条商品**全部** `stock: null`。
- 动作：打开报告详情页并检查商品卡片。
- 预期：卡片显示「库存未标注」，不显示「缺货」或「库存 0」。
- 清理：无需清理。

**结果：未通过（阻断项在数据侧，不在实现侧）。** 用例的「有真实库存值」一侧今天**没有
数据可证**：商城对目标租户返回的 `stock` **全为 `null`**（2026-09-29 实测：4 件商品，
0 件有库存值），因此「有库存时显示数量」这一半未执行。实现侧已按未标注渲染并留有单测
（`tests/test_mall_goods.py`），但**不得据此报告为通过**。
复验条件：商城侧为该租户的商品标注库存后重跑。

> 该结论在**目标租户订正后重新实测过**：换租户不能改变它——新租户的商品同样没有库存值。

## QA-EV-009 按标签取货 —— **可执行，待执行**

- 环境：同 `QA-EV-005`。
- 前置：健康风险 `condition_code` 已有 `(标签名, 标签值)` 映射；商城侧商品已挂该标签。
- 数据：见下。
- 动作：打开报告详情页，检查是否只出现被该标签命中的商品。
- 预期：只展示标签命中的在售商品，未被命中的不出现。
- 清理：无需清理。

**状态：三个旧阻断项已解除，本用例今天可执行；尚未端到端执行，故不记为通过。**

**旧阻断项的现状（2026-10-01 实测）**：

1. ~~商城侧尚未建立任何标签~~ —— **已解除**。目标租户已建标签，商品已挂上。
2. ~~映射取值仍不完整~~ —— **已解除**：12 条映射均已填值。真值文档
   （`docs/condition-to-mall-tag.md`）在 **genesis-evidence** 仓库，**不在本仓**；
   本仓的取值副本在 `app/service/condition_tags.py` 的 `CONDITION_TAGS`，
   由 `tests/test_condition_tags.py` 逐条比对两仓、拦住漂移。在**本仓**核对取值看后者即可。
3. ~~商城端点没有标签入参~~ —— **对这条用例不适用**。该条说的是 `POST /webapi/goods/read`
   （#186 仍未解决），但**本仓取货走的不是它**，而是 `MALL_WEBAPI_LABELS_PATH`
   （默认 `/mallapi/goodsspu/getGoodsByLabels`）。**实测该路径在目标租户可用**：
   在生产 base URL（`h5.trendcloud.cc`）上以 `血脂异常风险评估` 查询，返回 **1 件**——
   「心腦通」`2016406459289477122`；而该租户在售商品共 5 件。即**过滤确实生效**，
   返回的是命中集合而非全量。

> ⚠️ **第 3 条订正的原因值得记住**：先前把它当成阻断项，是因为假设取货走的是
> `/webapi/goods/read`。**实际配置走的是标签端点**，所以「端点没有标签入参」看似成立
> 却不影响这条用例。**读代码得出的结论要拿实际配置对一遍**——配置里的一行
> （`MALL_WEBAPI_LABELS_PATH`）改变了这条用例的成立条件。

**复验时必须做的一步**：用标签接口把标签**读回来核对**取值字符串——因为**名字写错在
商城侧是静默跳过**（不报错、返回空列表），「写错」与「没货」在调用方眼里完全一样。

**不得**为了让它变绿而伪造本地的标签-商品对应关系，也不得放宽过滤条件（例如
「无标签就返回全部」——那会把该租户的全部商品推给任意健康风险，正是这道闸门要防的事）。

## QA-EV-010 深链落到**商品详情页**，且参数名页面认得

- 环境：HealthFlow 测试环境；目标租户由 `MALL_WEBAPI_TENANT_ID` 决定。
- 前置：报告已 `assessed`；`MALL_STOREFRONT_BASE_URL` / `MALL_STOREFRONT_CART_PATH` /
  `MALL_DEEP_LINK_SECRET` 三项已配置。
- 数据：目标租户的在售商品。
- 动作：点「加入购物车」，检查跳转 URL 与落地页。
- 预期：URL 同时带契约载荷 `spu_id` 与页面参数 `id`（两者同值）；落地页**打得开该商品**；
  签名在商城侧可通过。
- 清理：无需清理。

**结果：部分未通过（落地一侧待 H5 侧完成）。** 本仓能证明的是 URL 构造与签名；**「点一下就完成
加购」不成立**——H5 商品详情页的 `onLoad` 只认 `options.id`，它不会读 `spu_id`、也不会静默加购。
所以当前形态是「**跳到商品详情页，用户再点一次加购**」，并且为此**两个参数都带**：
`spu_id` 是契约载荷（商城侧验签的对象），`id` 是页面识别所需——只带前者会落到一个
**打不开商品的空页面**。

要让「一次点击即完成加购」成立，需要 H5 侧新增「带某标记即自动加购」的行为。那是
**公司前端仓库的改动**（不在本仓、不在本仓的 review 流程内），且形态取决于改哪条租户线，
另行立项。**不得**因此把本用例记为通过。

## 已知限制（不是缺陷，但必须写下来）

**深链的签名不担保「该商品与本报告相关」。**

`create_cart_link` 只鉴权报告，**不校验 `spu_id` 是否落在该报告的推荐集内**。所以持有报告
的人可以拿任意 `spu_id` 换到一张由本仓签发的链接。

**今天这是可接受的**，因为签名代表的不是「推荐」：

1. 用户拿不到额外权限——他本来就能在商城自己把这个商品加进购物车。
2. 签名的语义只是「这份载荷由本仓签发且未被改动」；它防的是**传输途中被改**
   （改商品、改数量、改过期），不是「这个商品与该报告相关」。
3. ~~若现在加闸门，功能会立即死亡：`label_pairs_for` 返回空 → 过滤后为空 → **一张链接
   都签不出来**。~~ **这条已被实测推翻（2026-10-01）**：标签端点在本租户实际可用
   （`getGoodsByLabels`），报告 #40（血脂异常）经该路径取回的正是「心腦通」。
   因此「加闸门会让功能死亡」**不成立**——闸门在今天的链路上是**可实现的**。

**触发条件（必须记住的一条）**：**一旦归因启用**（订单开始带「来自健康建议」），这个
缺口就从「无害」变成「用户可伪造归因」。**那时必须先加闸门（`spu_id` 必须在该报告的
推荐集内）再开归因**，顺序不能反。

> **2026-10-01 更新：归因已决定不做**（理由与重启条件见 genesis-evidence 的
> `docs/deep-link-contract.md` 阻塞项二）。因此上面这个**触发器暂不生效**；
> 换 spu 的缺口今天仍是**可接受**的。**顺序规则保留**，将来重启归因时照用。
> 注：`create_cart_link` 今天**没有**闸门，本仓未改动它。

契约侧已同步声明：商城侧**不得**把签名当作推荐背书或授权凭据。

## 结果记录

执行后在 `artifacts/qa/` 下记录提交、环境、时间戳、每个用例结果与保留的去标识化证据。

## AFK-B10 可信工作流静态门

- 环境：HealthFlow AFK 任务分支。
- 前置：模板 1.1.1 已部署。
- 数据：六条 AFK 变更 workflow。
- 动作：运行 `node .sandcastle/policy-check.mjs workflows`、actionlint、ShellCheck，并与 afk-bootstrap 的受管文件逐字节比较。
- 预期：同仓库 owner gate、可信 controller、候选只读 token、干净 delivery checkout 和 AGENT_PAT fail-closed 全部通过，受管文件无漂移。
- 清理：无。

## AFK-B11 Bundle 状态机回归

- 环境：afk-bootstrap 临时 Git 仓库测试。
- 前置：模板测试 checkout 可用。
- 数据：落后的本地 main、前进的 origin main、合并结果和远端竞态。
- 动作：运行 afk-bootstrap 的 `test/trusted-pr-delivery.sh`。
- 预期：基线被重置、bundle 原样保留提交、远端竞态被拒绝。
- 清理：测试 trap 删除临时仓库。

## AFK-B12 Live canary

- 环境：HealthFlow self-hosted runner。
- 前置：加固 workflow 已合并，runner 与只读 token、AGENT_PAT 在线。
- 数据：仓库所有者创建的一次性 PR。
- 动作：添加 `agent:review` 并检查 workflow、review、标签和交付分支。
- 预期：使用当前 main，通过 controller/candidate/delivery 隔离完成审核且没有 blocked 标签。
- 清理：关闭一次性 PR，删除临时分支和标签。

AFK-B10：已通过，时间 `2026-08-30T03:31:15+08:00`，提交
`de499d8d10b80fba7318a99c18b08fa68820b0b1`，Linux x86_64，Python
3.13.13、Node v24.15.0、actionlint 1.7.12、ShellCheck 0.11.0。证据：`uv
run pytest`（143 passed, 1 skipped）、`uv run ruff check .`、policy checker、
actionlint、ShellCheck、`git diff --check` 全部通过；受管文件与模板逐字节一致。

AFK-B11：已通过，复用模板提交 `84e9537c661f676f68951eb3e7480472b91ff728` 的
`bash test/trusted-pr-delivery.sh`，覆盖 stale main、bundle 提交保留和远端竞态拒绝。

AFK-B12：待模板合并后在 HealthFlow 在线 self-hosted runner 执行 owner-authored
`agent:review` canary，保留 workflow URL、review、标签和清理证据后再标记通过。
workflow YAML 不适用复杂度或 mutation 工具；安全状态机由模板动态测试覆盖，本仓负责静态门和部署一致性。
