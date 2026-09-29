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

- 环境：HealthFlow 测试环境 + 金丝雀租户（东宸药业 `1578664130444005376`）+ 商城只读端点。
- 前置：`MALL_WEBAPI_*` 四项经服务环境注入；报告已 `assessed` 且含一个已确认健康风险。
- 数据：该租户真实在售商品（实测 20 条）。
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
数据可证**：商城对该租户返回的 `stock` 全为 `null`，因此「有库存时显示数量」这一半未执行。
实现侧已按未标注渲染并留有单测（`tests/test_mall_goods.py`），但**不得据此报告为通过**。
复验条件：商城侧为该租户的商品标注库存后重跑。

## QA-EV-009 按标签取货 —— **未通过**

- 环境：同 `QA-EV-005`。
- 前置：健康风险 `condition_code` 已有 `(标签名, 标签值)` 映射；商城侧商品已挂该标签。
- 数据：无（见下）。
- 动作：打开报告详情页，检查是否只出现被该标签命中的商品。
- 预期：只展示标签命中的在售商品，未被命中的不出现。
- 清理：无需清理。

**结果：未通过（两个前置都不成立）。** 今天没有任何可以执行这条用例的数据通路：

1. **映射不存在**：`docs/condition-to-mall-tag.md` 的 12 行中只有 2 行有取值，且它们
   标注为**未验证**；`label_pairs_for()` 因此如实返回空元组。
2. **商城端点没有标签入参**：已交付的 `POST /webapi/goods/read` 入参只有
   `shopId`/`current`/`size`（`WebApiReadGoodsRequest`），过滤条件是
   `tenantId + verifyStatus + shelf + delFlag`，**没有标签维度**。即使映射填好，
   本仓拿到的也只是「该租户的全部可售商品」，无法判断哪件被打了哪个标签。

因此这条用例的复验需要**两个**前置同时到位：映射取值 + 商城端点加标签入参（后者
另行立项）。**不得**为了让它变绿而伪造本地的标签-商品对应关系，也不得放宽过滤条件
（例如「无标签就返回全部」——那会把该租户的全部商品推给任意健康风险，正是这道闸门
要防的事）。

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
