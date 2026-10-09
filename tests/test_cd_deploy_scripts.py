"""`deploy/deploy-36.sh` 的判定分支：在本地把**远端块**跑起来，不碰 36。

做法：`--dry-run` 会把将要执行的远端命令原样打印出来。本文件把它取出来，配一个
临时目录当 `APP_ROOT`、一组假的 `systemctl`/`chown`/`runuser`、一个本地 http.server
当入口，然后**真的执行那段 shell**。于是幂等、自检、失败恢复这些分支都能在不开 ssh
的情况下被驱动 —— 先例是 AI-Ops 的 `deploy/test-rollback-classifier.sh` +
`tests/test_cd_deploy_scripts.py`。

一个关键点决定了本文件的结构：**产物是真的**。`--dry-run` 会真的构建 wheel 与前端，
并把构建结果（入口 bundle 的路径与 sha256）编译进那段远端命令。所以驱动它时不能用
伪造的产物 —— 自检里「入口提供的就是这一版」那条会永远对不上。本文件因此从
`--dry-run` 的输出里取出真产物与真哈希来搭现场，代价是每个用例要付一次构建。

钉住的是**外部可观测行为**（退出码、打印出的状态行、文件系统状态），不是脚本的内部
写法：脚本形状会反复改，这些判据不该跟着改。

最要紧的一条是**负控**：产物身份不符必须被拒。那是策略门 —— `dev-host` 只放行
「产物身份与载荷一致」的写入，而这条断言如果写成永远为真，生产就会在没有任何人察觉的
情况下接受任何字节。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "deploy-36.sh"
BASH = "/bin/bash"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


TARGET_SHA = _git("rev-parse", "HEAD")
SHORT_SHA = _git("rev-parse", "--short=12", "HEAD")

pytestmark = pytest.mark.skipif(
    shutil.which("npm") is None or shutil.which("uv") is None,
    reason="驱动部署脚本需要 npm 与 uv（--dry-run 会真的构建产物）",
)


def _production_topology_reachable() -> bool:
    """这台机器能不能解析 `dev-host` 的 36 号目标。

    解析不了就跳过用到它的用例（**不是**整体跳过）：脚本的第一件事是环境前置检查，它要 `dev-host show 36` —— 那需要
    主机清单（库外）、ssh 配置与私钥。它们只存在于开发机上，**刻意不**在 GitHub-hosted
    runner 上（也就不该因为「CI 没有生产环境的钥匙」而变红）。这是一次**明确的跳过**，
    不是通过 —— 所以 `test_transfer_refuses_a_payload...` 自己再判一次，并说明原因。
    """
    dev_host = shutil.which("dev-host")
    if dev_host is None:
        return False
    show = subprocess.run([dev_host, "show", "36"], capture_output=True, text=True, timeout=60)
    return show.returncode == 0 and "service-host" in show.stdout


_TOPOLOGY = _production_topology_reachable()

# The skip is applied per-test rather than module-wide on purpose: a guard that can never
# run is not a guard. The argument-validation and exit-code-discipline tests need no host
# at all, so they keep running in CI — only the ones that actually drive the remote block
# or reach 36 are skipped away.
needs_topology = pytest.mark.skipif(
    not _TOPOLOGY,
    reason="需要本机的 dev-host 主机清单与到 36 的访问（开发机才有，CI 刻意没有）",
)

_FAKE_SYSTEMCTL = """#!/bin/sh
# 只实现远端块用到的三个子命令；每个调用都记进日志，好断言「重启有没有发生」。
echo "$*" >> "$FAKE_SYSTEMCTL_LOG"
case "$1" in
  is-active) echo "${FAKE_SERVICE_ACTIVE:-active}" ;;
  restart)   [ "${FAKE_RESTART_FAIL:-0}" = "1" ] && exit 1; exit 0 ;;
  cat)       echo "[Service]"
             echo "ExecStart=/opt/health-flow/.venv/bin/uvicorn app.main:app"\\
                  "--host ${FAKE_UNIT_HOST:-0.0.0.0} --port 10007" ;;
esac
exit 0
"""

_FAKE_CHOWN = "#!/bin/sh\nexit 0\n"

_FAKE_RUNUSER = """#!/bin/sh
# 剥掉 `-u <user> --` 前缀后原样执行，让 pip 那一步落到假的 pip 上。
while [ $# -gt 0 ]; do
  case "$1" in
    -u) shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
exec "$@"
"""

# 一台「入口」：`/ready` 返回可配置的 JSON，其余路径按目录提供文件。这样自检的两条
# 判据都是真的在打 HTTP —— 不是把结果喂进函数里。
_SERVER_PY = '''import sys, http.server, socketserver

root, port, ready = sys.argv[1], int(sys.argv[2]), sys.argv[3]

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=root, **kw)
    def do_GET(self):
        if self.path == "/ready":
            body = ready.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()
    def log_message(self, *a):
        pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", port), H) as httpd:
    httpd.serve_forever()
'''

READY_OK = '{"status":"ready","report_provider":"configured","account_auth":"required"}'
READY_DEGRADED = '{"status":"degraded","report_provider":"unconfigured","account_auth":"required"}'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_script(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(SCRIPT), *args], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=900,
    )


def _parse_dry_run(stdout: str) -> dict[str, str]:
    """把 `--dry-run` 的输出拆成：远端命令 + 它声明的产物身份 + 入口 bundle 的 sha。

    只取这三样。**入口 bundle 的路径不从这里取** —— 生成块时 `${HEALTH%/ready}` 已被
    外面那层 shell 展开，块里留下的是绝对 URL，形状随调用者变化；而它本来也不需要：
    自检是拿**服务端实际返回的字节**去比这个 sha，路径由块自己拼。
    """
    marker = "---- 远端命令（原样）----\n"
    block = stdout.split(marker, 1)[1].split("---- 结束 ----", 1)[0]
    declared = re.search(r"--artifact-sha256 ([0-9a-f]{64})", stdout)
    artifact = re.search(r"本地产物：(.+)", stdout)
    live_asset_sha = re.search(r'"([0-9a-f]{64})" \] \|\| return 1', block)
    assert declared and artifact and live_asset_sha, stdout
    return {
        "block": block,
        "declared_sha": declared.group(1),
        "artifact": artifact.group(1).strip(),
        "live_asset_sha": live_asset_sha.group(1),
    }


class _FakeHost:
    """一台「够用的」主机：临时目录 + 假工具 + 一个本地入口。"""

    def __init__(self, tmp: Path, app_root: Path) -> None:
        tmp.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp
        self.root = app_root
        self.backup_dir = tmp / "backups"
        self.staging = tmp / "staging" / "deploy-36"
        self.bin = tmp / "bin"
        self.systemctl_log = tmp / "systemctl.log"
        self.port = _free_port()
        self.server: subprocess.Popen | None = None
        self._install_fakes()

    def _install_fakes(self) -> None:
        self.bin.mkdir(parents=True, exist_ok=True)
        for name, body in (
            ("systemctl", _FAKE_SYSTEMCTL),
            ("chown", _FAKE_CHOWN),
            ("runuser", _FAKE_RUNUSER),
        ):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # 主机 venv 里的 pip。假 pip 只记录被调用、返回成功 —— 装包本身不是这里的
        # 判据；被断言的是「改完之后服务起得来、入口提供的是新构建」。
        pip = self.root / ".venv" / "bin" / "pip"
        pip.parent.mkdir(parents=True, exist_ok=True)
        pip.write_text('#!/bin/sh\necho "pip $*" >> "$FAKE_PIP_LOG"\nexit ${FAKE_PIP_RC:-0}\n')
        pip.chmod(0o755)

    def env(self, **overrides: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "DEPLOY_APP_ROOT": str(self.root),
                "DEPLOY_BACKUP_DIR": str(self.backup_dir),
                "DEPLOY_REMOTE_TARBALL_BASE": str(self.staging),
                "DEPLOY_HEALTH_URL": f"http://127.0.0.1:{self.port}/ready",
                "DEPLOY_SERVICE": "fake-svc",
                "FAKE_SYSTEMCTL_LOG": str(self.systemctl_log),
                "FAKE_PIP_LOG": str(self.tmp / "pip.log"),
                # 假工具在真 PATH 之前；python3/curl/npm/uv 仍解析到真的那些。
                "PATH": f"{self.bin}:{os.environ['PATH']}",
            }
        )
        env.update(overrides)
        return env

    # ---- 搭现场：产物与哈希都来自真实的 `--dry-run` ----

    def stage(self, **overrides: str) -> dict[str, str]:
        """跑一次 `--dry-run`，把**真产物**放到远端块会读的路径上。"""
        result = _run_script(self.env(**overrides), "--commit", TARGET_SHA, "--dry-run")
        assert result.returncode == 0, f"--dry-run 失败：\n{result.stdout}\n{result.stderr}"
        parsed = _parse_dry_run(result.stdout)
        artifact = Path(parsed["artifact"])
        assert artifact.is_file(), artifact
        actual = subprocess.run(
            ["sha256sum", str(artifact)], capture_output=True, text=True, check=True
        ).stdout.split()[0]
        assert actual == parsed["declared_sha"], "dry-run 声明的身份与实际载荷不符"
        self.staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact, Path(f"{self.staging}-{SHORT_SHA}.tar.gz"))
        return parsed

    def seed_deployed(self, revision: str, *, frontend_files: dict[str, str] | None = None) -> None:
        frontend = self.root / "frontend"
        frontend.mkdir(parents=True, exist_ok=True)
        for name, body in (frontend_files or {"assets/index-OLD.js": "old build"}).items():
            path = frontend / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        (self.root / "deployed-revision").write_text(revision + "\n")

    def serve(self, *, ready: str = READY_OK) -> None:
        script = self.tmp / "server.py"
        script.write_text(_SERVER_PY)
        self.server = subprocess.Popen(
            ["python3", str(script), str(self.root / "frontend"), str(self.port), ready],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            try:
                subprocess.run(
                    ["curl", "-sf", "-o", "/dev/null", f"http://127.0.0.1:{self.port}/ready"],
                    check=True, capture_output=True, timeout=3,
                )
                return
            except Exception:
                time.sleep(0.1)
        raise AssertionError("本地入口起不来")

    def stop(self) -> None:
        if self.server is not None:
            self.server.terminate()
            self.server.wait(timeout=10)

    def run_remote(self, block: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, "-c", block], env=self.env(**overrides),
            capture_output=True, text=True, timeout=300,
        )


@pytest.fixture
def scratch(tmp_path_factory):
    """一个测试独占的暂存根。

    `--dry-run` 刻意**保留**它的暂存目录（调用方要能对那个包算 sha256），而暂存目录里
    有一次完整的前端构建（node_modules 数百 MB）。实测：不接管 `TMPDIR` 时反复调用会把
    `/tmp` 填满 —— 随后是 `git worktree add ... No space left on device`，一个看起来像
    构建坏了、其实是磁盘满了的失败。
    """
    return tmp_path_factory.mktemp("deploy-scratch")


@pytest.fixture(autouse=True)
def _own_tmpdir(scratch, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(scratch))


@pytest.fixture
def host(scratch):
    h = _FakeHost(scratch / "tmp", scratch / "opt")
    try:
        yield h
    finally:
        h.stop()


# ── 用法与参数校验（不碰主机、不需要构建） ──────────────────────────────────


def test_refuses_both_commit_and_rollback() -> None:
    result = _run_script(
        dict(os.environ), "--commit", TARGET_SHA, "--rollback-to", TARGET_SHA
    )
    assert result.returncode == 64
    assert "只能给一个" in result.stderr


def test_requires_a_target() -> None:
    result = _run_script(dict(os.environ))
    assert result.returncode == 64
    assert "--commit" in result.stderr


@needs_topology
def test_bad_revision_is_a_deployment_failure_not_an_environment_one() -> None:
    """坏 sha 走的是「部署失败」（1），不是「环境不对」（65）。

    这一条需要 host：环境前置检查排在解析 commit **之前**（那是刻意的——`dev-host`
    跑不通时没必要先去解析什么），所以没有 dev-host 的机器上它会先撞到 65。那正好是
    下面那条用的场景，两条一起构成这组退出码的完整边界。
    """
    result = _run_script(dict(os.environ), "--commit", "0" * 40, "--dry-run")
    assert result.returncode == 1
    assert "仓库里没有 commit" in result.stderr


def test_environment_fault_is_not_reported_as_a_deployment_failure() -> None:
    """`dev-host` 跑不通 → 退出码 65，不是 1。

    这两种失败把人引向完全不同的方向（环境 vs 部署），混成一个码会在每次事故的最初
    一小时里白花时间 —— 而 `dev-host` 依赖 `tomllib`（Python 3.11+），runner 上的裸
    `python3` 是 3.10，这正是它会发生的方式。
    """
    env = dict(os.environ)
    # 把 dev-host 藏起来，但别把 coreutils 也藏掉 —— 那样连 dirname/mktemp 都找不到，
    # 测的就不是脚本的前置检查了。dev-host 住在 ~/.local/bin，所以一个只含系统路径的
    # PATH 精确地把它摘掉，其余一切照旧。
    env["PATH"] = "/usr/bin:/bin"
    result = _run_script(env, "--commit", TARGET_SHA, "--dry-run")
    assert result.returncode == 65
    assert "dev-host" in result.stderr


# ── 幂等 ────────────────────────────────────────────────────────────────────


@needs_topology
def test_no_change_when_the_marker_already_names_the_target(host: _FakeHost) -> None:
    """标记 == 目标 → 成功退出、**不重启**、不动任何东西。

    不重启是重点：一个「看似幂等」的分支如果仍然重启生产，那么每次重跑都会造成一次
    没有必要的停机，而那是本分支存在的全部理由。
    """
    host.seed_deployed(TARGET_SHA, frontend_files={"assets/index-OLD.js": "still here"})
    parsed = host.stage()
    result = host.run_remote(parsed["block"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "state=no-change" in result.stdout
    assert f"revision={TARGET_SHA}" in result.stdout
    assert not host.systemctl_log.exists() or "restart" not in host.systemctl_log.read_text()
    assert (host.root / "frontend" / "assets" / "index-OLD.js").read_text() == "still here"
    assert not (host.root / "frontend.old").exists()


@needs_topology
def test_marker_comparison_uses_the_full_sha(host: _FakeHost) -> None:
    """标记必须与目标**等宽**比较。

    AI-Ops 踩过反面：`/health` 报 12 位而目标是 40 位，字符串不等把每次幂等重跑都判成
    陈旧，于是生产被反复重部署。这里用一个短 sha 当标记 —— 它必须被判为「不同」，
    从而走真正的部署路径，而不是被前缀匹配蒙对。
    """
    host.seed_deployed(TARGET_SHA[:12])
    parsed = host.stage()
    host.serve()
    result = host.run_remote(parsed["block"])
    assert "state=no-change" not in result.stdout, "12 位标记被当成了同一个修订"


# ── 真正的部署：自检与入口一致性 ────────────────────────────────────────────


@needs_topology
def test_deploys_and_selfchecks_against_the_live_entry_point(host: _FakeHost) -> None:
    """健康且线上确实在提供这一版 → 自检通过。

    自检里最实的一条是**从入口取回入口 bundle 的 sha256**：其它几条都能由一个「文件在
    盘上」满足，只有这一条要求请求真的被服务到了新构建。
    """
    host.seed_deployed("a" * 40)
    parsed = host.stage()
    host.serve()

    result = host.run_remote(parsed["block"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "selfcheck=passed" in result.stdout
    assert f"live-asset-sha={parsed['live_asset_sha']}" in result.stdout
    assert (host.root / "deployed-revision").read_text().strip() == TARGET_SHA
    # 前端是**整目录替换**：部署后盘上恰好是一次构建，不留旧文件。
    assert not (host.root / "frontend" / "assets" / "index-OLD.js").exists()
    assert not (host.root / "frontend.old").exists()


@needs_topology
def test_selfcheck_failure_restores_the_previous_frontend(host: _FakeHost) -> None:
    """自检不过 → 用**写入之前**建的回滚点恢复。

    这是回滚路径唯一能被自动证明的地方。这里让自检失败的方式是 `/ready` 报
    `degraded`（而不是伪造内容）：内容那条判据由上一个用例覆盖，把失败来源分开才能
    说清是「哪条判据拦住的」。

    断言恢复的是**内容**，不是「备份文件存在」：恢复后盘上必须重新出现旧构建。
    """
    host.seed_deployed("a" * 40, frontend_files={"assets/index-OLD.js": "old build"})
    parsed = host.stage()
    host.serve(ready=READY_DEGRADED)

    result = host.run_remote(parsed["block"])

    assert result.returncode == 1
    assert "selfcheck=failed" in result.stdout
    assert "restored=frontend-from-" in result.stdout
    assert (host.root / "frontend" / "assets" / "index-OLD.js").read_text() == "old build"
    # 恢复也应该把修订标记还原成上一个值。
    assert (host.root / "deployed-revision").read_text().strip() == "a" * 40


@needs_topology
def test_stop_is_reported_as_drift_not_silently_repaired(host: _FakeHost) -> None:
    """unit 的绑定地址与仓库声明不一致时：报出来，不改它。

    线上 unit 是 `--host 0.0.0.0`、仓里写 `127.0.0.1`。策略要求改变暴露必须先从
    **观测到的流量**决定，而 CD 静默把它改成 loopback 会直接切断入口 —— 那不是
    「谁来部署」该做的决定。所以断言的是「报告了」。
    """
    host.seed_deployed("a" * 40)
    parsed = host.stage()
    host.serve()

    result = host.run_remote(parsed["block"], FAKE_UNIT_HOST="0.0.0.0")

    assert "drift=unit-binds-0.0.0.0" in result.stdout
    # 仓库里的 unit 声明与线上不同，而这并不妨碍本次部署成功 —— 漂移是报告，不是门槛。
    assert "selfcheck=passed" in result.stdout


# ── 产物身份（策略门） ──────────────────────────────────────────────────────


@needs_topology
def test_transfer_refuses_a_payload_that_does_not_match_the_declared_identity() -> None:
    """`dev-host` 侧的负控：身份不符必须被拒。

    这段逻辑在 `dev-host` 里（早于本票存在），但它是本脚本赖以成立的门，所以这里对它
    下一道断言：**它真的会拒**。用错误的身份尝试一次写入 —— 必须得到拒绝（退出码 77），
    而不是「恰好成功」。
    """
    dev_host = shutil.which("dev-host")
    if dev_host is None:
        pytest.skip("需要 dev-host 才能验证传输门")
    show = subprocess.run([dev_host, "show", "36"], capture_output=True, text=True)
    if show.returncode != 0 or "service-host" not in show.stdout:
        pytest.skip("36 不是可达的服务类主机")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("payload\n")
        payload = handle.name
    try:
        result = subprocess.run(
            [dev_host, "cp", "36", payload,
             "--dest", "/tmp/health-flow-artifact-identity-probe.txt",
             "--artifact-sha256", "0" * 64],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        os.unlink(payload)
    assert result.returncode == 77, (
        f"产物身份不符却没有被拒（rc={result.returncode}）：{result.stdout}{result.stderr}"
    )
    assert "产物身份不符" in (result.stdout + result.stderr)


@needs_topology
@needs_topology
def test_a_run_leaves_no_worktree_registration_behind() -> None:
    """跑完不能留下 worktree 登记。

    worktree 是**仓库里的登记**，不只是目录。跑完不注销，登记就比目录活得久，之后每次
    `git worktree list` 都带着一条悬挂项 —— 那是审计违规，而且会把真正的违规埋掉。
    实测：本脚本的第一版在自己的测试里漏了 55 条。

    断言的是「登记数在一次运行前后相同」，不是「目录被删了」：`--dry-run` 刻意保留暂存
    目录，登记的注销与它无关。
    """
    def registrations() -> int:
        return len(
            subprocess.run(
                ["git", "worktree", "list"], cwd=REPO_ROOT,
                capture_output=True, text=True, check=True,
            ).stdout.strip().splitlines()
        )

    before = registrations()
    result = _run_script(dict(os.environ), "--commit", TARGET_SHA, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert registrations() == before, "跑了 dry-run 之后多出了 worktree 登记"


@needs_topology
def test_rollback_to_is_the_same_path_as_commit(host: _FakeHost) -> None:
    """应急回滚与自动部署共用同一个脚本、同一条路径。

    这正是不抽两个入口的理由：AI-Ops 的 tar/rsync 配对 bug 就是两条路各自漂移出来的。
    断言的是**同一条路径**：`--rollback-to` 生成的远端块与 `--commit` 的逐字节相同
    （目标是同一个 sha）。
    """
    host.seed_deployed("a" * 40)
    via_commit = host.stage()["block"]
    via_rollback = _run_script(host.env(), "--rollback-to", TARGET_SHA, "--dry-run")
    assert via_rollback.returncode == 0, via_rollback.stderr
    assert _parse_dry_run(via_rollback.stdout)["block"] == via_commit
