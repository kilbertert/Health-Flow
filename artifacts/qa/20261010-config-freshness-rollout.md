# 配置新鲜度机制上线与验收记录（2026-10-10）

## 部署的是什么

| 项 | 值 |
| --- | --- |
| 仓库 / 修订 | `health-flow` `1e1531a`（`main`，PR #208，squash 自 `fix/stale-config-restarts-readers`） |
| 变更 | worker unit 加 `PartOf=`；新增 `health-flow-env.path` → `health-flow-env-restart.service`；`/ready` 新增 `config_freshness`；部署脚本重启步骤的说明注释 |
| 代码上线 | CD run `38033821146`，`state=` 无变更、`selfcheck=passed` |
| 目标主机 | `yidong-36`（service host），入口 `http://127.0.0.1:10007/` |
| unit 安装时间 | 2026-10-10 15:19（主机本地时间） |
| 验收时间 | 2026-10-10 15:19–15:22 |
| 起因 | #201：报告上传全量失败，患者只看到「请重新上传」，而 `/ready` 一直报 `report_provider: configured` |

## 两部分，顺序不能反

1. **代码先上线。** 合并触发 CD，部署脚本按既有路径装 wheel、换前端、重启 `health-flow`。
   这一步之后 `/ready` 有 `config_freshness` 字段，但报 `unknown` —— 因为 unit 里还没有
   `HEALTHFLOW_ENV_FILE`。
2. **unit 后安装。** 部署脚本刻意不碰 unit，`cd.yml` 也刻意不在 `ops/service-host/systemd/**`
   上触发（`tests/test_cd_workflow.py` 断言了这一点）。四份 unit 由 `dev-host write
   --artifact-sha256` 逐份写入（写闸门按产物身份放行），再 `daemon-reload`、`enable --now
   health-flow-env.path`。**只 enable watcher**：worker 按文档保持 disabled。

`unknown` 不降级 `status` 正是为这个顺序服务：否则第一步那次部署会因自检失败自我回滚。

## 已核实（实测，非推断）

### 1. 四份 unit 落盘后逐字节等于本机产物

```
health-flow-report-worker.service  dc54390e8967697e5624e0932fd8e5abc06b4344dbab10cf360785a6964a044c
health-flow-env.path               7aca13705c02e6977c8b1b2e5374c7ed6a8c41974fbae454c0411082139ff642
health-flow-env-restart.service    7d258086305752b70e6a255d2796b5d391cbb01b412da68aed969086d4cddc2e
health-flow.service                93c52a6215feb9b2caf519fa6f3a154648dc13bb9a52db326d84aa483385db9e
```

（写入时私有 umask 让它落成 `0600`，已 `chmod 0644` 并复验 sha 未变。）

**portal unit 有意不是仓库里那一份。** 主机上跑的是 `--host 0.0.0.0`，仓库声明
`--host 127.0.0.1`。关闭这个暴露是独立决定（策略要求由**观测到的流量**决定），
`deploy-36.sh` 只报告不修，本次沿用：写入的是主机现文件 + 新增的 `Environment=` 行，
`ExecStart` 原样保留。两份的差异只有这一行。

### 2. 正控：手工编辑 env 会重启两个读者

改文件之前两边都是 `15:19:31`。向 env 追加一行注释：

```
Oct 10 15:19:41 systemd[1]: Starting health-flow-env-restart.service ...
Oct 10 15:19:41 systemd[1]: Finished health-flow-env-restart.service ...

health-flow               15:19:41   ← 重启
health-flow-report-worker 15:19:41   ← 一起重启（同一个事务）
```

这是本次事故的确切触发条件——**没有任何部署**，只有一次手工编辑。

### 3. 负控：过期配置被看见，并且降级

把 env 文件的 mtime 推到 5 分钟之后（模拟「进程比文件旧」），等 watcher 触发重启后：

```json
{"status":"degraded","config_freshness":"stale",
 "config_file_changed_at":"2026-10-10T07:24:56.309455+00:00",
 "process_started_at":"2026-10-10T07:19:56.546098+00:00"}
```

**为什么必须先把 mtime 推走**：让文件比进程新，watcher 会立刻重启它，`stale` 随即被清掉——
两个机制互相抵消。把 mtime 推到未来、等重启落地，就是构造「读者跑在过期文件之后」的状态。
在系统时钟与重启都在的机器上，这是我能构造的、诚实的 `stale` 复现。

把 mtime 改回当下，机制自愈：读者重启为 `15:20:09`，`/ready` 回到 `ready` / `current`。

### 4. 属性 4：刻意停掉的 worker 不会被复活

```
systemctl stop health-flow-report-worker
systemctl restart health-flow.service          → worker 仍 inactive
（再）手工编辑 env → watcher try-restart        → worker 仍 inactive
```

`PartOf=` 只传播**重启**；`try-restart` 只重启**正在跑**的读者。文档化的
「worker 保持 disabled、抽取暂停」验收状态在一次重启、一次 env 编辑之后都还在。

### 5. 端到端：原本失败的那条通路现在成功

以服务身份、用 unit 那份 env，对 51 号报告的存量图片（2026-10-10 事故里报 401 的那一份）
跑真实抽取：

```json
{"success": true, "metric_count": 9, "error": null,
 "provider": "openai-compatible-chat", "model": "deepseek/deepseek-v4.1-flash",
 "first_metrics": [{"name":"Total Chol 总胆固醇","value":"5.5","unit":"mmol/L","flag":"H"},
                   {"name":"LDL-C 低脂蛋白(坏)胆固醇","value":"3.63","unit":"mmol/L","flag":"H"}]}
```

**同一条命令，只把键换成一个死键**（负控）：

```json
{"success": false, "metric_count": 0,
 "error": "Error code: 401 - {'error': {'message': \"Invalid 'Authorization' header or token.\", ...}}"}
```

这与事故当天的报错文本逐字相同。所以这条通路的成败确实由凭据决定，而机制让凭据保持最新。

### 6. worker 进程现在握的就是磁盘上那把键

```
worker /proc/<MainPID>/environ 的 OPENAI_API_KEY sha256[:12] = 2ba3893d1cb1
磁盘 var/health-flow.env 的同一把键               sha256[:12] = 2ba3893d1cb1
MATCH
```

事故当天这两者是 `b609c77a1149` 对 `2ba3893d1cb1`（MISMATCH，进程报 401）。按哈希比对，
不打印键值。

## 最终状态

```
health-flow-env.path                enabled   active (waiting)
health-flow-report-worker.service   disabled  active (running)   ← 按文档保持 disabled
health-flow.service                 enabled   active (running)

/ready: status=ready  config_freshness=current
```

## 没有做（明确记录）

- **未重跑报告 49–53。** 库里有 34 条 completed、12 条 failed 的 job，没有排队中的。
  重跑这五份是独立的事故善后，不是这次机制上线的验收项；要重跑时让患者重新上传即可
  （现在通路是好的）。当时的 `processing_error` 与审计事件都还在，可追溯。
- **未改 `_TRANSIENT_ERROR_MARKERS`，未改患者侧文案。** 401 不是瞬时的；「请重新上传」
  在本类故障下确实误导，但那是独立的 UX 决定。
- **未动 `127.0.0.1` / `0.0.0.0` 的绑定漂移。** 见上。
- **未在 36 上留下任何临时脚本或控制用的注释。** 控制用的两行注释已从 env 文件移除，
  随后触发的那次重启把两个读者带到了当前状态。
