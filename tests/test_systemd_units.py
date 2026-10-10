"""unit 文件的形状守卫：配置新鲜度的两条机制必须真的写在 unit 上。

静态的 —— 只读文本，不碰主机、不跑 systemd（`systemd-analyze verify` 在某些环境下会
因为读到别的机器上的路径而报无关噪声）。与 `tests/test_cd_deploy_scripts.py` 里那三条
静态守卫同一形状、同一理由：这一类错误在本地「看起来完全正常」，只在真的执行时现形，
而 `bash -n` / 编译器一个字都不报。

钉住的三条，每一条都对应一个**已经实测过**的失效形态（#201）：

1. **两份 worker unit 都带 `PartOf=health-flow.service`。** 部署重启的是父服务；父被重启
   而兄弟没有，兄弟就继续持有过期配置与过期代码。**两处都要断言**：两个文件都会被真的
   安装（一份给开发机的 `--user`，一份给服务主机），只改一处等于只修了一半。
2. **`.path` 监视的路径与 unit 实际加载的 env 文件是同一个。** 路径一旦漂移，机制会静默
   失效 —— 服务照常起、照常报 configured，而重启再也不会发生。
3. **`.path` 触发的 oneshot 同时重启两个读者。** 只重启一半正是本缺陷的形状。
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_HOST_UNITS = REPO_ROOT / "ops" / "service-host" / "systemd"
DEV_HOST_UNITS = REPO_ROOT / "ops" / "systemd"

# 服务主机上的 env 文件路径。开发机那份写在 unit 里，从文件读出来比对，不在这里复制一份。
SERVICE_HOST_ENV_FILE = "/opt/health-flow/var/health-flow.env"


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少 unit 文件：{path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _directive(text: str, key: str) -> list[str]:
    """取某个指令的所有取值，**剥掉注释行**。

    必须剥：解释「为什么这样写」的注释里正是出现 `PartOf=`、`PathChanged=` 这些字样的地方。
    不剥注释会让一条把语义写反的注释把断言骗过去 —— 这正是 `tests/test_cd_workflow.py`
    的 `_stripped()` 存在的同一个理由。
    """
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith(f"{key}="):
            values.extend(part.strip() for part in stripped[len(key) + 1 :].split() if part.strip())
    return values


def _directive_lines(text: str, key: str) -> list[str]:
    """取某个指令**整行**的取值（不按空白拆）。用于 `ExecStart=` 这种一行就是一条命令的指令。"""
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith(f"{key}="):
            value = stripped[len(key) + 1 :].strip()
            if value:
                values.append(value)
    return values


def _environment_file(text: str) -> str:
    values = _directive(text, "EnvironmentFile")
    assert len(values) == 1, f"期望恰好一个 EnvironmentFile，得到 {values}"
    return values[0]


def _healthflow_env_file_value(text: str) -> list[str]:
    """`Environment=HEALTHFLOW_ENV_FILE=...` 的取值。

    `Environment=` 可以一次给多个赋值，所以先取整行、再在等号上切开找这个键的名。
    """
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped.startswith("Environment="):
            continue
        for assignment in stripped[len("Environment=") :].split():
            name, separator, value = assignment.partition("=")
            if separator and name == "HEALTHFLOW_ENV_FILE":
                found.append(value)
    return found


def test_both_worker_units_restart_with_their_parent() -> None:
    """属性 2：部署重启父级时，读取同一份 env 的兄弟必须一起重启。

    实测（systemd 249）：只重启父级 -> 活跃的子级一起重启；子级「disabled 但 active」
    照样传播；被操作者主动 stop 的子级保持停止。所以 `PartOf=` 不会破坏文档里那句
    「worker 保持 disabled」的验收状态。
    """
    for directory in (SERVICE_HOST_UNITS, DEV_HOST_UNITS):
        text = _read(directory / "health-flow-report-worker.service")
        assert _directive(text, "PartOf") == ["health-flow.service"], (
            f"{directory.relative_to(REPO_ROOT)} 的 worker 没有 PartOf=health-flow.service："
            " 部署重启父服务后会留下一个继续持有过期配置与过期代码的兄弟进程（#201）"
        )


def test_every_reader_is_told_which_env_file_it_loaded() -> None:
    """`/ready` 的过期检测靠 `HEALTHFLOW_ENV_FILE`；unit 不告诉它，检测就退化成 unknown。

    对服务主机那一对断言（那是 `/ready` 被机器读取的地方：`deploy-36.sh` 的自检断言
    `status == "ready"`）。取值必须与同一个 unit 的 `EnvironmentFile=` 一致 —— 不一致
    就会去比对另一个文件，而结果照样自称 current。
    """
    for name in ("health-flow.service", "health-flow-report-worker.service"):
        text = _read(SERVICE_HOST_UNITS / name)
        declared = _environment_file(text)
        told = _healthflow_env_file_value(text)
        assert told == [declared], (
            f"{name} 的 HEALTHFLOW_ENV_FILE={told} 与它加载的 EnvironmentFile={declared} 不一致；"
            " /ready 会去比对另一个文件并自称 current"
        )


def test_env_change_restarts_both_readers() -> None:
    """属性 1 的另一半：手工编辑 env（无部署）也必须让两个读者重读。

    `PartOf=` 补不上这个缺口 —— 它只在父被重启时传播，而手工改 env 不重启任何东西。
    """
    path_unit = _read(SERVICE_HOST_UNITS / "health-flow-env.path")
    watched = _directive(path_unit, "PathChanged")
    assert watched == [SERVICE_HOST_ENV_FILE], (
        f".path 监视的是 {watched}，而读者加载的是 {SERVICE_HOST_ENV_FILE}；"
        " 路径漂移会让这条机制静默失效 —— 服务照常起，重启再也不会发生"
    )

    # `.path` 只接受一个 Unit=，所以它必须指向一个显式重启两者的 oneshot，而不是某一个读者。
    triggered = _directive(path_unit, "Unit")
    assert triggered == ["health-flow-env-restart.service"], (
        f".path 触发的 unit 是 {triggered}；只重启一半正是本缺陷的形状"
    )

    restart_group = _read(SERVICE_HOST_UNITS / "health-flow-env-restart.service")
    exec_lines = _directive_lines(restart_group, "ExecStart")
    assert len(exec_lines) == 1, f"期望一条 ExecStart，得到 {exec_lines}"
    command = exec_lines[0]
    assert command.split()[0].endswith("systemctl"), f"ExecStart 不是 systemctl 调用：{command}"
    # `try-restart` 而不是 `restart`：restart 会把操作者刻意停掉的 worker 重新拉起来，
    # 破坏文档化的「抽取暂停」验收状态。`start` 则对 active 的 unit 是空操作（静默不生效）。
    assert "try-restart" in command.split(), f"ExecStart 必须用 try-restart：{command}"
    for reader in ("health-flow.service", "health-flow-report-worker.service"):
        assert reader in command.split(), f"ExecStart 漏了读者 {reader}：{command}"


def test_the_deploy_restart_target_is_the_worker_units_parent() -> None:
    """`PartOf=` 的另一半在部署脚本里 —— `SERVICE=<name>` 必须就是那个父级。

    这是本文件里**最要紧**的一条，因为它的失效完全无声：`deploy-36.sh` 只重启
    `SERVICE=health-flow`，worker 靠 `PartOf=health-flow.service` 跟着重启。任何人改了
    `SERVICE` 的默认值、或给 worker 写上另一个父级名，传播就断了 —— 没有测试会红，部署
    照样绿，而 worker 继续跑旧代码。这正是第三个线上实例的形状（worker 起于 09-30，
    而代码在 10-10 被换过）。

    静态比对：两侧都是文本，谁改了都立刻响。
    """
    script = (REPO_ROOT / "deploy" / "deploy-36.sh").read_text(encoding="utf-8")
    # 只取**字面量**默认值。脚本下面还有一行 `SERVICE=${DEPLOY_SERVICE:-$SERVICE}`（测试接缝），
    # 它含 `$`，不是默认值本身 —— 两者都算进来会让这条断言对着一个变量名做判断。
    defaults = [value for value in re.findall(r"^SERVICE=([^\n#]+)$", script, re.MULTILINE) if "$" not in value]
    assert len(defaults) == 1, f"期望恰好一处 SERVICE 字面量默认值，得到 {defaults}"
    deployed_service = defaults[0].strip()
    assert deployed_service, "SERVICE 默认值是空的"

    for directory in (SERVICE_HOST_UNITS, DEV_HOST_UNITS):
        worker = directory / "health-flow-report-worker.service"
        parent = directory / f"{deployed_service}.service"
        assert parent.is_file(), (
            f"deploy-36.sh 重启的是 {deployed_service}，但 {directory.relative_to(REPO_ROOT)} 里"
            f"没有 {parent.name} —— PartOf= 会指向一个不存在的父级"
        )
        assert _directive(_read(worker), "PartOf") == [f"{deployed_service}.service"], (
            f"{worker.relative_to(REPO_ROOT)} 的 PartOf= 与 deploy-36.sh 的 SERVICE="
            f"{deployed_service} 不一致：重启不会再传播到 worker，而部署仍然报绿"
        )


def test_the_paths_unit_is_the_only_new_one() -> None:
    """`ops/systemd/` 那一对**刻意**没有 `.path`。

    开发机上不跑这两个服务，环境变量的变更由 shell 重新读取，没有「长驻进程持有过期配置」
    这回事。为对称而加一个 .path 会是一个没有读者的机制 —— 部署到开发机的路径也不存在。
    这条断言把「刻意不加」变成可检查的，免得后来者以为是漏了。
    """
    assert not list(DEV_HOST_UNITS.glob("*.path")), (
        "ops/systemd/ 不该有 .path unit：开发机不跑这两个服务，没有需要重启的读者"
    )


def test_the_units_parse_as_ini() -> None:
    """最弱的一道，但便宜：`[Unit]`/`[Service]`/`[Path]` 段名拼错时立刻响。"""
    for directory in (SERVICE_HOST_UNITS, DEV_HOST_UNITS):
        for unit in sorted(directory.glob("*.service")) + sorted(directory.glob("*.path")):
            # interpolation=None：unit 文件里的 `%` 在 configparser 默认的插值语法下有含义，
            # 一个 `%i` 会让它抛异常 —— 那会是这条守卫自身的假阳性，不是 unit 的问题。
            parser = configparser.ConfigParser(strict=False, allow_no_value=True, interpolation=None)
            parser.read_string(_read(unit))
            assert parser.sections(), f"{unit.name} 没有可解析的段"
