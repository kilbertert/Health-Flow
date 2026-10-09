"""`cd.yml` 的形状守卫：部署只在合并后发生，且三处已知的坑不会溜回来。

静态的 —— 它读 workflow 文本，不跑它。与 `tests/test_ci_workflow.py` 同一形状、同一
理由（先例：`.sandcastle/policy-check.mjs` 静态守 AFK 边界）。断言的每一条都对应一个
**已经实测过**的失效形态，任何一条成立时这一层就会安静地失效。

被钉住的六条：

1. **不许有 `--force` push**、不许有「指定 commit」的输入 —— 后者会让能触发部署的人
   把从未通过必需检查的提交送上生产，等于把 Ruleset 绕开。
2. **`paths` 不含 `**`**：纯文档合并不该拉起一次生产重启。
3. **`environment` 存在**（部署账本 + 只允许 main 的分支限制），且**没有任何**
   试图在 job 内自动批准该 environment 的步骤 —— 那一步在有 reviewer 时永远不会执行、
   没有 reviewer 时什么都不做，是穿着安全网外衣的死代码。
4. **`concurrency` 在 job 级**：workflow 级 + `cancel-in-progress: false` 会死锁
   （run 一创建就占住槽位），workflow 级 + `true` 会打断正在改生产的部署。
5. **`runs-on` 含本仓专属 label**，不是裸 `self-hosted`：裸 label 的语义是「任何一台
   自托管 runner」，任何新登记的 runner 都能领到这个 job 并拿到部署凭据。
6. **摘要步骤 `if: always()`**：失败时也要出声（本仓 #133 的教训 —— 摘要只在 success
   分支里写，于是 12 次 failure 里 11 次没留下任何解释）。
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "cd.yml"


def _stripped() -> str:
    """workflow 文本，去掉注释。

    必须去掉：解释「为什么不是这样」的注释里正是出现 `**`、`self-hosted`、
    `workflow_dispatch` 这些字样的地方。不剥注释会让一条把语义写反的注释把断言骗过。
    """
    return "\n".join(
        line.split("#", 1)[0].rstrip() for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
    )


def test_cd_workflow_exists() -> None:
    assert WORKFLOW.is_file(), "缺少 .github/workflows/cd.yml —— 部署不在交付链上"


def test_no_force_push_and_no_commit_selector() -> None:
    body = _stripped()
    assert not re.search(r"git\s+push[^\n]*--force", body), "CD 不得 force-push"
    # `workflow_dispatch` 允许重跑当前 main；但**不许有任何 input** —— 一个能选任意
    # commit 的输入会让从未通过必需检查的提交部署到生产。
    assert not re.search(r"^\s+inputs:", body, re.MULTILINE), (
        "workflow_dispatch 不得带输入：一个『选 commit』的入口就是绕过 main 保护"
    )


def test_paths_filter_excludes_everything() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    body = _stripped()
    assert re.search(r"^    paths:", body, re.MULTILINE), "触发集合必须是白名单"
    paths = body.split("    paths:", 1)[1].split("\n    #", 1)[0]
    # 「全都是」的两种写法都要挡住。`**` 的裸条目会被 YAML 当作别名，所以真正可能写出来
    # 的是带引号的 `"**"`；不剥注释地扫原文，顺带把 YAML 里 `- **` 这种写法也覆盖上。
    assert not re.search(r'^\s+- ("\*\*"|\*\*)$', raw, re.MULTILINE), (
        "不要用 `**`：纯文档合并不该重启生产"
    )
    # 部署时真的会执行的文件必须在触发集合里，否则「改了它却不部署」而且没有信号。
    for required in ("deploy/deploy-36.sh", "app/**", "pyproject.toml", "uv.lock"):
        assert f'"{required}"' in paths, f"触发集合漏了 {required}"
    # 反向：unit 文件不在集合里，因为部署脚本不装 unit（它只报告绑定漂移）。
    # 列上它会产生一次「重装了同一份代码、unit 却没变」的部署 —— 正是本节的坑。
    assert "systemd" not in paths, (
        "unit 文件不该在触发集合里：deploy-36.sh 不安装 unit，列上它只会产出一个假信号"
    )


def test_environment_exists_without_a_self_approval_step() -> None:
    body = _stripped()
    assert re.search(r"^    environment: production-36$", body, re.MULTILINE), (
        "缺少 environment：它承载部署账本与 main-only 的分支限制"
    )
    # GitHub 的 environment 审批发生在 job 的步骤**开始之前**。在 job 里写一个批准步骤，
    # 有 reviewer 时它根本不会被执行，没有 reviewer 时它无事可做 —— 死代码，不是安全网。
    assert "pending_deployments" not in body, (
        "job 内不得试图自动批准自己的部署：那一步永远不会按预期执行"
    )
    assert not re.search(r"gh\s+api[^\n]*reviews[^\n]*APPROVE", body), (
        "job 内不得出现批准动作"
    )


def test_concurrency_is_at_job_level() -> None:
    body = _stripped()
    # job 级：缩进四格，落在 jobs.<id> 下。
    assert re.search(r"^    concurrency:$", body, re.MULTILINE), (
        "concurrency 必须在 job 级：workflow 级 + false 会死锁，workflow 级 + true 会"
        "打断正在改生产的部署"
    )
    assert re.search(r"^      cancel-in-progress: false$", body, re.MULTILINE), (
        "不能取消正在执行的部署：它会在改到一半时被杀，自检与回滚都跑不到"
    )
    assert not re.search(r"^concurrency:$", body, re.MULTILINE), "不得同时存在 workflow 级 concurrency"


def test_runs_on_a_repository_specific_label() -> None:
    body = _stripped()
    match = re.search(r"^    runs-on: (.+)$", body, re.MULTILINE)
    assert match, "找不到 runs-on"
    labels = match.group(1)
    assert "health-flow" in labels, (
        "必须带本仓专属 label：裸 `self-hosted` 的语义是「任何一台自托管 runner」，"
        "任何新登记的 runner 都能领到这个 job 并拿到部署凭据"
    )
    assert labels.strip() != "self-hosted", "裸 self-hosted 不能执行部署"


def test_summary_runs_even_on_failure() -> None:
    body = _stripped()
    assert re.search(r"^      - name: Summarize$", body, re.MULTILINE), "缺少摘要步骤"
    summary = body.split("- name: Summarize", 1)[1]
    assert re.search(r"^        if: always\(\)$", summary, re.MULTILINE), (
        "摘要必须 if: always()：失败时不出声正是 #133 的病灶"
    )
    assert "backup=" in summary or "回滚点" in summary, "失败时要指出回滚点在哪"


def test_no_checks_are_duplicated_here() -> None:
    """CD 不重复跑 CI 的检查。

    它跑在 `main` 上，而 `main` 只能通过带必需检查的 PR 进入。在这里再跑一次 pytest
    会让「部署失败」与「代码不过」混成同一个信号，并且把部署时间翻倍。
    """
    body = _stripped()
    assert "pytest" not in body, "CD 不跑 pytest：那是 CI 的职责，main 已经过门"
    assert "ruff" not in body, "CD 不跑 ruff：同上"
