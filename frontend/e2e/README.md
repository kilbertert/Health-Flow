# E2E 测试脚手架(Playwright)

浏览器级端到端测试基建:一条命令用**真实构建产物 + 一次性测试数据库**驱动整个应用。
后续所有票的 E2E 断言都加在这套脚手架上,不再另建测试运行时。

## 一条命令跑通

前置(一次性,仓库根目录执行):

```bash
uv sync --extra dev          # 后端环境(含 uvicorn,uv.lock 锁定)
cd frontend && npm install   # 前端依赖(含 Playwright)
npm run e2e:install          # 首次执行:下载 Chromium 浏览器内核
```

运行(在 `frontend/` 下):

```bash
npm run test:e2e
```

该命令会依次:

1. `vite build` 产出真实前端构建产物(`frontend/dist`);
2. 以 `SERVE_FRONTEND=true` 启动 HealthFlow FastAPI(uvicorn),由后端直接服务构建产物,
   数据库指向系统临时目录里的一次性 SQLite 文件(绝不触碰仓库 `data/healthflow.db`);
3. 运行 Playwright 用例(默认 375×667 移动端视口)。

不依赖 MySQL / Milvus / Neo4j / LLM / 证据服务;报告解析等外部服务链路
(上传 -> VLM 解析 -> 证据匹配)不在 E2E 覆盖范围,由种子数据替代其产物。

## 种子数据

`e2e/fixtures.js` 导出的 `test` 在原生 Playwright 之上扩展了 `seed` fixture,
绑定本次运行的测试数据库:

```js
import { test, expect } from './fixtures.js';

test('示例用例', async ({ page, seed }) => {
  const { account, reports } = await seed({
    reports: ['assessed', 'pending_confirmation'],
  });
  // account: { id, email, password, display_name } —— 可直接在登录表单输入
  // reports: [{ id, status, report_type, access_token }]
  await page.goto('/');
});
```

种子由 `scripts/e2e_seed.py` 生成(可独立执行,输出 JSON),复用应用自身的
模型、口令散列与证据响应契约,不引入并行契约:

- **登录账户**:邮箱 + 密码,口令散列与注册接口一致;
- **已完成报告**(`assessed`):携带已确认指标(含页码/bbox/证据原文)与
  契约合法的 `evidence_result`;
- **待确认报告**(`pending_confirmation`):携带待核对指标,处于确认流程入口状态。

## 数据隔离

- **每次运行**:全新的临时沙箱目录(测试数据库 + 报告文件目录),运行结束自动删除;
  需要保留现场排查时执行 `HEALTHFLOW_E2E_KEEP_SANDBOX=1 npm run test:e2e`。
- **每个用例**:`seed()` 每次调用都创建全新账户(唯一邮箱),报告按账户隔离,
  用例之间不共享任何数据;并发 worker 亦互不影响。

## 环境开关

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `HEALTHFLOW_E2E_PORT` | E2E 服务器端口 | `8137` |
| `HEALTHFLOW_E2E_PYTHON` | 显式指定项目 Python 解释器 | 自动探测 `.venv/bin/python`,回退 `uv run --no-sync` |
| `HEALTHFLOW_E2E_KEEP_SANDBOX` | 运行结束后保留沙箱目录 | 未设置即删除 |

## 目录结构

| 文件 | 职责 |
| --- | --- |
| `../playwright.config.js` | 运行配置:一次性沙箱、375px 基线视口、webServer |
| `server.mjs` | 以测试数据库启动 FastAPI(服务真实构建产物) |
| `python.mjs` | 项目 Python 解释器解析 |
| `seed.mjs` + `../../../scripts/e2e_seed.py` | 种子数据工具(账户/已完成报告/待确认报告) |
| `fixtures.js` | 用例脚手架(`seed` fixture,所有用例从这里导入 `test`) |
| `smoke.spec.js` | 375px 冒烟测试(登录 -> 首页渲染) |
| `teardown.mjs` | 运行收尾(删除一次性沙箱) |

pytest(`uv run pytest`)与前端构建(`npm run build`)不受本脚手架影响;
脚手架自身的种子工具由 `tests/test_e2e_seed.py` 覆盖。
