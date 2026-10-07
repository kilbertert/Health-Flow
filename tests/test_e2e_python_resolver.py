"""E2E Python 解释器解析(frontend/e2e/python.mjs)。

本文件驱动的是**真实的解析模块**,不是它的复刻:node 导入模块后调用导出的
``resolvePython``。断言的对象因此与跑 e2e 时用的是同一份代码。

回归的是 #114:回退到 ``uv run`` 会让 uv 在 checkout 里**新建一个空的 .venv**
(只有 pip/setuptools,没有 uvicorn)。下一次运行解析到那个目录,失败从「uv 可自愈」
变成「稳定复现」。所以解析器只在**已经能导入 uvicorn** 的解释器上落地,否则
明确报错并退出,绝不创建任何目录。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOLVER = REPO_ROOT / "frontend" / "e2e" / "python.mjs"

# 通过 node 调用解析器:导入模块、调用导出、把结果以 JSON 打到 stdout。
# 失败时异常冒泡,node 以非零退出码结束并把消息写到 stderr。
_NODE_PROBE = """
const { pathToFileURL } = await import('node:url');
const { resolvePython } = await import(pathToFileURL(process.env.HEALTHFLOW_TEST_RESOLVER).href);
process.stdout.write(JSON.stringify(resolvePython(process.env.HEALTHFLOW_TEST_CHECKOUT)));
"""

NODE = shutil.which("node")


def _python_with_uvicorn() -> str | None:
    """一个能导入 uvicorn 的解释器。跑本测试的 venv 通常就是。"""
    if importlib.util.find_spec("uvicorn") is not None:
        return sys.executable
    return None


PYTHON_WITH_UVICORN = _python_with_uvicorn()

pytestmark = pytest.mark.skipif(
    NODE is None or PYTHON_WITH_UVICORN is None,
    reason="解析器是 node 模块,且需要一个能导入 uvicorn 的解释器;缺少其一即无法驱动真实实现",
)


@pytest.fixture
def checkout(tmp_path):
    """一个 uv 项目根(worktree 的形态:pyproject + lock,但没有 .venv)。"""
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "probe"\n', encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return root


@pytest.fixture
def shared_venv():
    """一个真正就绪的 .venv(跑本测试的那个)——worktree 复用的共享环境。"""
    return Path(sys.prefix)


def resolve(checkout: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """在 checkout 上驱动真实的 resolvePython。"""
    child_env = {
        **os.environ,
        "HEALTHFLOW_TEST_RESOLVER": str(RESOLVER),
        "HEALTHFLOW_TEST_CHECKOUT": str(checkout),
    }
    # 解析顺序里 HEALTHFLOW_E2E_PYTHON 优先级最高;除非用例显式传入,否则清掉,
    # 免得开发机上的设置把结果带偏。
    child_env.pop("HEALTHFLOW_E2E_PYTHON", None)
    child_env.update(env or {})
    return subprocess.run(
        [NODE, "--input-type=module", "--eval", _NODE_PROBE],
        cwd=checkout,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_venv_fails_without_creating_one(checkout):
    """#114:没有解释器时明确报错,且不留下任何目录。

    曾经的失败形态是「先造一个空 .venv 再以 No module named uvicorn 失败」——
    报错指向 uvicorn,而真因是环境;且那个目录会让下一次运行也失败。
    """
    result = resolve(checkout)

    assert result.returncode != 0, f"应当失败,却返回了:{result.stdout}"
    assert not (checkout / ".venv").exists(), "解析器不得在 checkout 里创建 .venv"

    message = result.stderr
    assert "uvicorn" in message
    assert "uv sync --extra dev" in message
    assert "HEALTHFLOW_E2E_PYTHON" in message
    assert str(checkout) in message, "报错要指出是哪个 checkout 缺环境"


def test_empty_venv_fails_instead_of_being_used_as_the_project_env(checkout):
    """上一次运行踩下的空 .venv 要被点名,而不是当成项目环境用。

    这是 #114 里「稳定复现」的那一环:目录一旦存在,下一次运行就会选中它。
    """
    venv = checkout / ".venv"
    subprocess.run(
        [PYTHON_WITH_UVICORN, "-m", "venv", "--without-pip", str(venv)],
        check=True,
        capture_output=True,
    )
    assert venv.joinpath("bin", "python").exists(), "夹具前提:空 venv 也有 bin/python"

    result = resolve(checkout)

    assert result.returncode != 0, f"空 .venv 不该被当成项目环境:{result.stdout}"
    assert "uvicorn" in result.stderr
    assert "uv sync --extra dev" in result.stderr
    assert "HEALTHFLOW_E2E_PYTHON" in result.stderr


def test_uvicorn_capable_venv_is_used_directly(checkout, shared_venv):
    """已有可导入 uvicorn 的 .venv 就直接用它——canonical 行为不变。

    顺带覆盖文档里的 worktree 复用写法:``.venv`` 是指向共享环境的符号链接。
    解析器看的是解释器本身,所以这种「目录软链」与真实目录同样成立。
    """
    os.symlink(shared_venv, checkout / ".venv")

    result = resolve(checkout)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "command": str(checkout / ".venv" / "bin" / "python"),
        "prefixArgs": [],
    }


def test_healthflow_e2e_python_wins_over_the_checkout_venv(checkout, shared_venv):
    """HEALTHFLOW_E2E_PYTHON 仍然优先于 checkout 里的 .venv。"""
    os.symlink(shared_venv, checkout / ".venv")
    explicit = "/opt/elsewhere/bin/python"

    result = resolve(checkout, env={"HEALTHFLOW_E2E_PYTHON": explicit})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"command": explicit, "prefixArgs": []}


def test_healthflow_e2e_python_is_taken_verbatim_even_without_a_venv(checkout):
    """显式指定是人的输入,解析器不替它做判断——没有 .venv 也照用。"""
    explicit = "/opt/elsewhere/bin/python"

    result = resolve(checkout, env={"HEALTHFLOW_E2E_PYTHON": explicit})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"command": explicit, "prefixArgs": []}
