"""`ci.yml` 的形状守卫：测试门存在，且两个已知的静默失效不会溜回来。

本文件是**静态**的 —— 它读 workflow 文本，不跑它。这与本仓既有先例同源：
`.sandcastle/policy-check.mjs` 就是这么守 AFK 边界的（剥掉 YAML 注释后断言语义
必需的字面量），而它守的是「边界」，不是「实现」：这里断言的四条，每一条对应
一个**已经实测过**的失效形态，任何一条成立时 CI 都会看起来是绿的。

被钉住的四条：

1. **`uv sync` 必须带 `--extra dev`。** 本仓测试依赖在
   `[project.optional-dependencies] dev`，`--dev` 是空的且仍然退 0；随后
   `uv run pytest` 回退到 PATH 上的 pytest，全suite报
   `ModuleNotFoundError: No module named 'app'`。见干净 clone 实测。
2. **`npm ci` 必须在 pytest 之前。** 三个前端契约测试缺 `frontend/node_modules`
   时 `skipif` 掉，「全绿」会以 `445 passed, 4 skipped` 的样子出现 —— 跳过计数被
   读成通过。
3. **不能声明 `ruff format --check`。** main 上 27 个文件 would be reformatted，
   采用格式化器是独立一次改动；把它写进这里会让门的第一次运行读不出信号。
4. **`name` 与触发集合稳定。** Ruleset 的必需检查 context 是 job 的 `name`；改名
   会让那个检查永远 pending。触发集合漏了 `pull_request` 则 PR 阶段零信号。
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _stripped() -> str:
    """workflow 文本，去掉整行注释与行尾注释。

    注释必须去掉：本文件的第 1 条正是「不许只用 `--dev`」，而解释这件事的注释里
    就写着 `--dev`。不剥注释的话，一条把语义写反的注释会让断言通过。
    """
    lines = []
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        # 只剥注释起始前的内容；yaml 里的 `#` 在引号内是数据，但本文件没有这种用法。
        lines.append(line.split("#", 1)[0].rstrip())
    return "\n".join(lines)


def test_ci_workflow_exists() -> None:
    assert WORKFLOW.is_file(), "缺少 .github/workflows/ci.yml —— 测试门不存在"


def test_sync_installs_the_dev_extra() -> None:
    body = _stripped()
    assert "uv sync --locked --extra dev" in body, (
        "uv sync 必须带 --extra dev：本仓测试依赖在 optional-dependencies.dev，"
        "`--dev` 不装 pytest 却仍退 0"
    )
    bare = re.findall(r"uv sync[^\n]*", body)
    for command in bare:
        assert "--extra dev" in command, f"uv sync 少了 dev extra：{command!r}"


def test_frontend_dependencies_precede_the_tests() -> None:
    body = _stripped()
    assert "npm ci" in body, "缺少 npm ci：三个前端契约测试会静默 skip"
    assert body.index("npm ci") < body.index("pytest"), (
        "npm ci 必须在 pytest 之前，否则前端契约测试在收集时就已经 skip 了"
    )


def test_formatter_is_not_adopted_here() -> None:
    assert "ruff format" not in _stripped(), (
        "本 workflow 只跑 ruff check；采用格式化器是独立一次改动（main 上 27 个文件）"
    )


def test_required_check_context_and_triggers_are_stable() -> None:
    body = _stripped()
    # Ruleset 的 required_status_checks context == job 的 name。改这个名字会让那个
    # 必需检查永远 pending，从而**堵死所有 PR**。
    assert re.search(r"^\s*name: Tests$", body, re.MULTILINE), (
        "job name 变了；Ruleset 的必需检查 context 是它，改名即堵死所有 PR"
    )
    assert re.search(r"^  pull_request:", body, re.MULTILINE), "必须触发 pull_request"
    assert re.search(r"^  push:", body, re.MULTILINE), "必须触发 push: main"
    assert re.search(r"^  contents: read$", body, re.MULTILINE), "权限应只要 contents: read"


def test_runs_on_the_repository_runner_with_the_fork_gate_recorded() -> None:
    """检查跑在本仓专属的 self-hosted runner 上，**且这是有条件的**。

    这一条在 ADR-0010 里改过一次方向，改的理由是实测的：GitHub 账号被计费锁住时
    `ubuntu-latest` 的 job **起不来**（注解原文 "The job was not started because your
    account is locked due to a billing issue"），而 Ruleset 要求 `Tests` 与
    `Workflow policy` 有结论 —— 于是本仓每个 PR 都合不了，`--admin` 也绕不过。

    **持久 runner 不得执行不可信 PR 代码**，所以换 runner 的前提是一道 fork 门。
    那道门是**仓库设置**（`actions/permissions/fork-pr-contributor-approval` =
    `all_external_contributors`），不在这个文件里，因此本静态测试**读不到它** ——
    它能断言的是 runner 标签，以及「label 必须是本仓专属的那一个」。

    裸 `self-hosted` 的语义是「任何一台 self-hosted runner」，那会让这个仓库的
    检查落到别的项目的 runner 上；本仓（与 AI-Ops）的做法都是带上项目 label。
    """
    body = _stripped()
    assert re.search(r"^\s*name: Tests\n\s+runs-on: \[self-hosted, health-flow\]$", body, re.MULTILINE), (
        "verify job 应跑在 [self-hosted, health-flow]；改 runner 前先确认 fork 门"
        "（all_external_contributors）仍生效，并更新 docs/adr/0010"
    )
    assert "ubuntu-latest" not in body, (
        "上游 runner 已被证明会因计费锁整批起不来（ADR-0010）；改回去要先确认账号可付费"
    )
