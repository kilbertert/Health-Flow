---
name: verify-healthflow
description: "Drive the HealthFlow patient report portal in a real browser and capture evidence that a change works. Use when you need to prove UI behavior on health-flow (report history, report detail, indicator confirmation, recommendations, paste upload, mobile nav) rather than assume it works from unit tests."
---

# Verify HealthFlow

Drive the **real** HealthFlow app — FastAPI serving the real built React frontend —
in a real browser, exercise a user-facing feature the way a patient would, and
capture evidence. This skill wraps the repo's **existing** Playwright harness
(`frontend/e2e/`) rather than introducing a second test runtime.

Grounded against commit `892cc2b` on 2026-10-06: full suite = **42 tests, ~46s, all passing**.

## Launch

The harness starts and stops the server **by itself** — do not start uvicorn manually.

```bash
cd frontend
npm run test:e2e                      # whole suite: vite build + server + 42 specs + teardown
```

For a single feature, the build is still required:

```bash
cd frontend
npm run build && ./node_modules/.bin/playwright test e2e/<spec>.spec.js
```

Readiness is Playwright's own: `webServer` polls `http://127.0.0.1:${HEALTHFLOW_E2E_PORT}/health`
before the first spec, and `reuseExistingServer: false` means a stale listener on that
port **fails the run** rather than silently reusing it. Teardown is `e2e/teardown.mjs`,
which deletes the throwaway sandbox directory.

What the run actually does:

1. `vite build` → real production asset in `frontend/dist/`
2. starts FastAPI with `SERVE_FRONTEND=true`, serving that build
3. `DATABASE_URL` points at a one-off SQLite file under the system temp dir
4. runs Playwright at a **375×667 mobile viewport** by default

### Prerequisites — check these first, they fail loudly

```bash
cd frontend
ls node_modules/.bin/playwright              # missing -> npm install
ls dist/index.html                           # missing -> npm run build  (server refuses to start without it)
ls ../.venv/bin/uvicorn                      # NOT just ../.venv — exist-without-uvicorn is the trap
node -e 'require("@playwright/test")'        # resolves -> package installed (see note below)
ls ~/.cache/ms-playwright                    # hint only — NOT a reliable browser check
```

Do **not** treat `~/.cache/ms-playwright` as proof Chromium is present: it passes with an
empty or stale cache, and `PLAYWRIGHT_BROWSERS_PATH` moves the real location elsewhere.
Only a launch proves it — `npm run e2e:install` if Playwright reports a missing browser.

**A fresh worktree has none of these.** `node_modules/`, `dist/`, and `.venv/` are all
build artifacts or gitignored. Set up once per worktree:

```bash
cd <worktree>/frontend && npm install && npm run build
cd <worktree> && uv sync --extra dev    # creates .venv WITH uvicorn
```

Four failure modes worth knowing by sight, all verified in a fresh worktree:

- **`No module named uvicorn` naming a `.venv` path** — `.venv/` exists but is **empty**.
  `uv` creates `uvicorn`-less environments on the fly when a lockfile-only sync runs, so
  "the venv exists" proves nothing. Check for `../.venv/bin/uvicorn` specifically, and fix
  with `uv sync --extra dev` from the repo root (the `--extra dev` is what carries uvicorn).

- **`ERR_MODULE_NOT_FOUND` from a path under `~/.npm/_npx/`** — you invoked
  `npx playwright`, which resolved a *global cached* copy instead of this repo's.
  Use the local binary (`./node_modules/.bin/playwright`) or an npm script. `npx`
  is not safe here.
- **`未找到前端构建产物 .../dist/index.html`** — `dist/` is a build artifact and is
  absent in a fresh worktree or clean clone. Run `npm run build` first; a
  single-spec run does **not** skip the build.
- **`node_modules/.bin/playwright: No such file or directory`** — a fresh worktree has
  no installed dependencies. `npm install` in `frontend/`.

## Doctor

Read-only, answers "is this run worth driving?". Run it before the first drive, after
any failed drive, and whenever output looks wrong.

```bash
cd frontend
PORT="${HEALTHFLOW_E2E_PORT:-8137}"     # the override is real — do not hardcode 8137
curl -sf "http://127.0.0.1:${PORT}/health"   # only when a run is live; 200 = server up
```

- **Server not up** → you are not inside a run. Playwright owns the lifecycle; just run it.
- **`/api/health/metric-catalog` returns 503** → **expected, not a failure.** The app
  degrades without the model/evidence services, which E2E deliberately does not run.
- **Port busy** → a previous run was killed uncleanly. Find and stop *that* process
  (never by name matching, see Cleanup), or re-run with a fresh `HEALTHFLOW_E2E_PORT`.

**自定义端口是可用的**（已实测）。`playwright.config.js` 把最终端口写进
`HEALTHFLOW_E2E_PORT` 并传给 server、seed 与 worker 三处，所以
`HEALTHFLOW_E2E_PORT=8237 npm run test:e2e` 全链路成立 —— 已跑通。

一个容易误判的点：`fixtures.js` 里 `addCookies` 的 `url:` 写的是固定的
`http://127.0.0.1:8137`，看起来像是"自定义端口就会掉会话"。**实测不是**：
把该 URL 指到一个根本没有监听的端口，用例照样通过。

原因是 **cookie 本身不受端口约束**（RFC 6265：cookie 的作用域是 domain + path，
没有 port 分量）。`addCookies` 的 `url` 只用来推导 domain 与 path，端口部分不参与
匹配 —— 所以这里写 8137 还是 8237 没有区别。真正的 required 是 **domain 一致**
（都是 `127.0.0.1`）。

**所以：不要为了这个看起来的硬编码去改 `fixtures.js`。** 实测不支持那个改动，
改了只是无谓地碰产品外的既有文件。

## Drive

Import `test`/`expect` from `./fixtures.js` — never from `@playwright/test` — so you get
the `seed` fixture bound to this run's database.

**`loginWithSeed()` lands on 首页 and nothing else.** Almost every feature is on a deeper
page, so an example that stops there times out. Traverse per the canonical paths in
`features/README.md`:

```js
import { test, expect, loginWithSeed } from './fixtures.js';

test('示例：已完成报告的指标总览', async ({ page, seed }) => {
  // Seed ONE report: the history list renders one 查看 button per report, and
  // getByRole resolves to all of them — an unscoped click fails strict mode.
  const seeded = await seed({ reports: ['assessed'] });
  await loginWithSeed(page, seeded);            // plants the session cookie, opens '/'

  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();      // -> 报告详情
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await expect(page.getByText('指标总览', { exact: true })).toBeVisible();

  // seeded.reports[i] = { id, status, report_type, access_token }
});
```

- There is **no login page** — a session is a cookie the seed plants. `loginWithSeed`
  does it and asserts the home heading.
- `seed()` creates a **fresh account per call** (unique email), so cases never interact.
- Viewport is mobile-first (375px). For desktop layouts put
  `test.use({ viewport: { width: 1280, height: 800 } })` at **file or `describe` scope** —
  calling it inside a test body aborts with "did not expect test.use() to be called here".

**Prefer role/name selectors** — the real ones already in use:

| Target | Selector | Where |
|---|---|---|
| home heading | `getByRole('heading', { name: '呵护您的健康' })` | 首页 |
| profile | `getByRole('heading', { name: '个人中心' })` | 个人中心 |
| report history | `getByRole('heading', { name: '报告历史' })` | 个人中心内 |
| history row | `.history-section .ant-list-item` | 个人中心内 |
| report detail | `getByRole('heading', { name: '报告详情' })` | 报告详情 |
| upload page | `getByText('点击或拖拽多张报告文件到此区域')` | 上传页 |
| nav → upload | `getByRole('button', { name: '体检报告解读' })` | 首页导航 |
| parsing result | `getByText(/解析结果/)` | 确认流程 |
| mobile bottom nav | `getByRole('navigation', { name: '移动端主导航' })` | 375px |
| service nav | `getByRole('navigation', { name: '健康服务' })` | 桌面 |
| continue confirm | `getByRole('button', { name: '继续确认' })` | 报告详情内 |
| original-text popup | `getByRole('button', { name: /^查看.+原文$/ })` | 报告详情内 |

**「体检报告解读」是双关**：首页上它是一个导航按钮（→ 上传页），确认流程里它是一级标题。
按 name 找 heading 会撞车——用所在页面或伴随文本区分。

There are **no `data-testid` attributes** in this codebase. Accessible roles and visible
Chinese copy are the stable handles; do not invent ids.

## Evidence

Capture the **action and the resulting state**, not just the final screen.

```js
await page.screenshot({ path: 'var/verify-evidence/<feature>-<step>.png', fullPage: true });
```

Put deliberate evidence under `var/verify-evidence/` (gitignored, survives teardown).
Playwright's own failure artifacts (`screenshot: 'only-on-failure'`,
`trace: 'retain-on-failure'`) land in `frontend/test-results/`.

Proof standards for this app:

- Drive the **real user path** through the built frontend. Never assert against
  `/api/*` directly and call it UI verification.
- When a flow has a **hash route** (report detail), assert a **deep-link refresh**
  works, not just in-app navigation.
- For degradation behavior, assert the **reason** shown to the user differs per cause
  (no published knowledge card vs. mall unreachable) — the app distinguishes them on purpose.
- A `503` from `/api/health/metric-catalog` is degraded-mode, not a defect.

## Cleanup

The harness already does this: each run gets a fresh temp sandbox (DB + report files)
and `e2e/teardown.mjs` removes it. Nothing touches `data/healthflow.db`.

- Set `HEALTHFLOW_E2E_KEEP_SANDBOX=1` to keep the sandbox for triage.
- If you spawn a server yourself, **kill what you started** — never by process name.
- **Cleanup removes sandboxes and processes, never the evidence.** After teardown,
  confirm the files under `var/verify-evidence/` still exist. A cleanup that eats the
  proof has failed this step.
- Run cleanup after **every** failed iteration so broken attempts do not strand a
  listener on the E2E port and wedge the next run.

### Known harness gap: a failed startup strands its sandbox

`globalTeardown` only runs once a test run actually starts. If `webServer` fails to
launch — missing `dist/`, missing `uvicorn`, port already bound — Playwright aborts
**before** teardown, and the sandbox that `server.mjs` already created under
`/tmp/healthflow-e2e-<pid>-<ts>/` is left behind. Verified: two failed startups leaked
two sandboxes; the successful run cleaned up after itself.

The `webServer` timeout path is the exception — Playwright kills the child process
itself there, so no listener survives. Only the directory leaks.

Sweep after any failed launch, before the next attempt:

```bash
ls -d /tmp/healthflow-e2e-*           # identify first — do not blind-delete
rm -rf /tmp/healthflow-e2e-<pid>-<ts> # only ours: /tmp is shared across all projects
```

Leaked directories are small (a tarball keypair, and `report-files/` once seeding ran)
and contain no project data. They are residue, not a hazard — but they accumulate, and
`HEALTHFLOW_E2E_KEEP_SANDBOX=1` deliberately produces the same shape, so a deliberate
keep and a leak look identical. Do not assume every `/tmp/healthflow-e2e-*` is garbage.

## Helpers

All helpers already exist; this skill adds none.

| Helper | Invocation | Purpose |
|---|---|---|
| `e2e/server.mjs` | started by `webServer` in `playwright.config.js` | FastAPI on the test DB, serving `frontend/dist` |
| `e2e/python.mjs` | imported by the above | resolves `.venv/bin/python`, falls back to `uv run --no-sync` |
| `e2e/seed.mjs` + `scripts/e2e_seed.py` | via the `seed` fixture | creates account + `assessed` / `pending_confirmation` reports |
| `e2e/fixtures.js` | `import { test, expect, loginWithSeed }` | `seed` fixture + session bootstrap |
| `e2e/teardown.mjs` | `globalTeardown` | removes the sandbox |

Environment switches: `HEALTHFLOW_E2E_PORT` (default `8137`),
`HEALTHFLOW_E2E_PYTHON`, `HEALTHFLOW_E2E_KEEP_SANDBOX`.

## Known limits

- **Upload → VLM parse → evidence matching is not covered.** It needs an LLM key and the
  `genesis-evidence` service; E2E substitutes seed data for their products. Drive this
  chain only against a live deployment, and say so when you do.
- The harness port `8137` is a transient per-run default, not an entry in
  `~/Projects/DEVELOPMENT-PORT-REGISTRY.md`. It binds loopback only.
- Feature coverage lives in `features/` — see `features/README.md`.
