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
