# 10. 必需检查改到 self-hosted：一次被计费事故逼出来的主机边界扩张

日期：2026-10-13
状态：已采纳
影响版本：本仓库（不在 afk-bootstrap 模板内，是仓库设置层面的变更）

## 背景

2026-10-12，GitHub 账号因付款方式被拒而进入计费锁定。`ubuntu-latest` 上的 job
**起不来**，job 注解原文：

```
The job was not started because your account is locked due to a billing issue.
```

本仓库的规则集（`protected default branch`）要求 **`Workflow policy`** 与 **`Tests`**
两个检查有结论，而这两个 job 都在 `ubuntu-latest` 上。因此：

- 本仓库（以及另外三个同构仓库）的**所有 PR 都合不了**；
- `--admin` 也绕不过——`gh pr merge --admin` 的报错是
  `2 of 2 required status checks are failing`，管理员权限能绕评审，绕不过"必需检查必须成功"。

这不是本仓库的缺陷，是账号状态。但它是**阻塞**：迁移到模板 1.7.18 的 PR 就卡在这里。

## 决定

**把两个必需检查的 `runs-on` 从 `ubuntu-latest` 改为 `[self-hosted, health-flow]`**，
并**先把 fork PR 审批收紧**。

配套的安全边界变更（必须先做，不在这个 diff 里）：仓库是 **public**，而 fork PR 审批原为
`first_time_contributors` —— **回访的** fork 贡献者开 PR 会自动执行。把这批 workflow 挂到
self-hosted 上，等于给陌生人一条在宿主机上以 `claude` 身份执行任意代码的路径。
已在仓库设置改成 **`all_external_contributors`**。

## 理由

1. **AI-Ops 已经这么跑了，而且实测是绿的。** 它的 `Workflow policy` 与 `verify` 一直
   在 `[self-hosted, AI-Ops]` 上；2026-10-12 09:47 的一次 run 两个检查都 success。
   self-hosted 不受计费锁影响——这一点是**实测**的，不是推断：锁定期间在 Auto_Test 上
   触发过一次 self-hosted job，`Set up job` 成功、步骤实际执行。

2. **宿主工具链齐备。** node v24 / npm 11 / uv 0.11 / Python 3.13 都在，本仓库的
   `afk-policy` 各步与 `ci` 的 `uv sync` + `ruff` + `npm ci` + `npm run build` +
   `pytest` 本机逐条跑过：全绿。

3. **"改回 hosted" 不是暂缓，是迟早要还。** 计费恢复后可以把 `runs-on` 改回去，
   但那要**再动一次仓库设置、再走一次评审**，收益是"回到原状"。而 self-hosted 这条
   路径在 AI-Ops 上已经稳定运行，成本已经付过。

## 被否掉的方案

- **什么都不做，等计费恢复。** 代价是四个仓库的 PR 全部停摆，而解锁时间不在我们手上
  （付款方式被拒，不是欠费）。**这是唯一被明确排除的选项**：用一条不确定的等待去换
  一条已验证的路径。

- **`--admin` 绕过。** 实测无效：管理员权限不能绕过"必需检查必须成功"。

- **把检查从规则集里摘掉。** 最省事，直接删掉 merge gate。否掉：这等于为了让一次迁移
  通过而**永久降低**这个仓库的合并门槛，而问题会随计费恢复自行消失。

- **加 Windows self-hosted runner 来满足 `Windows Verify`（Auto_Test）。** 宿主没有
  Windows 环境，为一个计费事故引入一台新机器不划算；Auto_Test 的处置单独决定。

## 后果

- **宿主机成为 CI 执行面。** 这是真实的边界扩张：本仓库的 required check 现在跑在
  `claude` 账号下，而不是一次性容器里。缓解手段是 fork 审批（`all_external_contributors`）
  ——它把"哪些 workflow 可以执行"变成一次**人工批准**，而不是一条自动路径。
  这一点必须在仓库设置里保持，否则这个记录描述的保护就不存在了。

- **`AFK_PROFILE` / Playwright 一类的实现差异**按 `docs/GOVERNANCE-ADAPTER.md` 的口径
  属于需要 owner 批准的例外；本记录即该批准。安全边界与 merge gate 不在此列，本次也
  没有放宽它们——规则集一个字没改。

- **计费恢复后可以回退**，且回退是单向的、低风险的（把 `runs-on` 改回
  `ubuntu-latest` 即可）。本记录不主张永久保持 self-hosted，只主张**此刻不再等**。

## 验证

本 PR 自身就是判据：`pull_request` 事件下 workflow 取自 **merge commit**，所以改动
在这个 PR 上直接生效。

| 断言 | 结果 |
| --- | --- |
| `Workflow policy` 在 self-hosted 上**起得来** | 起得来（不再有计费注解） |
| `Tests` 在 self-hosted 上起得来 | 起得来 |
| `node .sandcastle/policy-check.mjs workflows` | 通过 |
| `prd-duplicates.check.ts` | 通过 |
| `repo-map.check.mjs` | 最新 |
| `AFK_ADR_BASE=<base> adr.check.mjs --base <base>` | 通过 |
| `profile-network.check.ts` / `mcp-config.check.ts` / `sandbox-prepare.check.ts` | 通过 |

**第一次跑到这里时它是红的**，而原因正是这个仓库自己的决策记录门禁：

```
adr: 80c5615c "ci: 必需检查改到 self-hosted runner" — a record looks owed:
this commit touches 2 path(s) that decide how the system is put together
(.github/workflows/afk-policy.yml, .github/workflows/ci.yml), and docs/adr/ is unchanged.
```

这条记录就是那次失败的产物。1.7.18 的门禁在它被部署的当天、在第一个提交上，
拦住了它的作者。
