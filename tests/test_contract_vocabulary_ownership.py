"""契约暴露的封闭取值集**只能有一位主人**（#245/#246/#247 的类级不变式）。

三个 architecture-review issue 是**同一个类**的三个实例，而那个类当时是**无界**的 ——
没有任何机械判据能发现新成员，只能靠每天一次的 agent 去读代码。本文件是那个判据。

## 判的是什么

一个**封闭取值集**（`Literal[...]` 的一串字符串）如果被**一个以上的文件**声明，那它就有
两个主人 —— 而两份声明一定会漂移，因为没有任何东西会因为它们不一致而失败。这正是：

- **#247**：审计事件的 action 词表只活在 docstring 里，契约把它出域成
  `list[dict[str, Any]]`、列是 `String(64)`；
- **#246**：取货为空的原因在服务端 `Literal`、ADR 的一段散文、以及前端自造的第四个值里
  各有一份；
- **#245**：报告指标计数根本没有契约字段，于是三个调用点各自算一遍。

## 为什么按「跨文件」划界，而不是按「契约里出现的每个 Literal」

这条界线是**量出来的**，不是想出来的：

- `app/schema/evidence.py` 里有 6 组重复取值（`routine/soon/urgent/emergency`、
  `context_only`、`not_available`、`high/moderate/low/very_low[/mixed]`、
  `体检报告解读与健康风险提示`），但它们**全在同一个文件里**，而且**服务端从不分支**于其中
  任何取值（`grep` 在 `app/service` 与 `app/api` 下对它们零命中）。它们是**上游证据服务的
  线路格式**在逐个模型边界上的复述 —— 给它们指定一个「家」，等于宣称服务端拥有一项它并不
  拥有的决定。所以按**跨文件**划界，那 6 组不进来。
- 真正跨边界重复、且服务端**确实分支**于其取值的是另外两组：报告状态（`schema/report.py`
  ↔ `service/report_status.py`）与报告主体一致性（`schema/report.py` ↔
  `service/report_subject.py`）。这两组是这条不变式要抓的类。

换句话说：**判据是「同一个封闭取值集被两个文件各声明一次」**，而不是「契约里有字面量」。
后者会把上游线路格式判红，而那种红只会教人给不属于自己的东西编一个家。

## 与既有守卫的关系

本仓已经有两条**同一个概念**的守卫（`test_report_status_vocabulary.py` 的状态字面量扫描、
`test_admission.py::test_no_second_vocabulary_is_declared_anywhere` 的原因名扫描）。它们是
**一次一个概念**手写的：覆盖面等于「有人想起来写了一份」。本文件是那条判据的**升级**：
按结构判，不按名字判，所以下一个同类概念长出来时它自己会红。
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP = REPO_ROOT / "app"

#: 允许的例外：**必须有**理由，且理由是「这不是本仓的决定」。
#:
#: 空集是当前状态 —— 这条不变式在现有代码上就是绿的。
EXEMPT: dict[tuple[str, ...], str] = {}


def _closed_sets(path: Path) -> list[tuple[int, tuple[str, ...]]]:
    """文件里每一处**全字面量**的 `Literal[...]`，返回 (行号, 取值元组)。

    派生写法（`Literal[*X]`）不算声明 —— 它正是本守卫要求的形状。
    """
    found: list[tuple[int, tuple[str, ...]]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)):
            continue
        if node.value.id != "Literal":
            continue
        slice_node = node.slice
        items = slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
        values: list[str] = []
        for item in items:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                values.append(item.value)
            else:
                return found  # 有非字面量 → 这是派生写法，不是声明
        if values:
            found.append((node.lineno, tuple(sorted(values))))
    return found


def test_no_closed_value_set_is_declared_in_two_files():
    """同一个封闭取值集不得被两个文件各声明一次。"""
    by_set: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for path in sorted(APP.rglob("*.py")):
        for lineno, values in _closed_sets(path):
            by_set[values].append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    offenders = {
        values: where
        for values, where in by_set.items()
        if len({location.split(":")[0] for location in where}) > 1 and values not in EXEMPT
    }
    assert not offenders, (
        "这些封闭取值集被多个文件各声明了一次 —— 两份声明一定会漂移，"
        "因为没有任何东西会因它们不一致而失败。把其中一处改成 `Literal[*那份声明]`：\n"
        + "\n".join(f"  {list(values)} -> {where}" for values, where in sorted(offenders.items()))
    )


def test_the_two_instances_this_rule_was_written_for_are_derived():
    """写这条守卫时它抓到的两组，必须保持**派生**（负控：改回手写则本文件变红）。

    它们此前是 `schema` 里手写的第二份/第三份声明：

    - 报告状态：`app/schema/report.py` 手写了那五个值，而词表的家在
      `app/service/report_status.py`；
    - 报告主体一致性：`app/schema/report.py` 手写了**两处**，家在
      `app/service/report_subject.py`。
    """
    from typing import get_args

    from app.schema.report import MedicalReportResponse
    from app.service.report_status import PATIENT_STATUSES, ReportStatus

    assert set(get_args(MedicalReportResponse.model_fields["status"].annotation)) == set(PATIENT_STATUSES)
    assert tuple(PATIENT_STATUSES) == tuple(get_args(ReportStatus))
    # 状态由**服务端**拥有且服务端确实分支于它：这条派生不是形式主义。
    from app.service.report_status import _ALLOWED  # noqa: PLC0415

    assert set(_ALLOWED) == set(PATIENT_STATUSES)


def _vocabulary_homes() -> list[Path]:
    """词表之家：`app/service/` 下**名字里带 `vocabulary`** 的模块，外加 `report_subject.py`
    与 `report_status.py`（它们是本不变式最初抓到的两组取值的家，名字里没有那个词）。

    **按结构发现，不按名字清单**：写死一份名单会让这条守卫在新增词表模块时静默放过它 ——
    而「静默放过」正是本文件存在的理由。
    """
    names = {"report_subject.py", "report_status.py"}
    return [
        path
        for path in sorted((APP / "service").glob("*.py"))
        if "vocabulary" in path.name or path.name in names
    ]


@pytest.mark.parametrize("home", _vocabulary_homes(), ids=lambda path: path.name)
def test_a_vocabulary_home_declares_its_set_once(home: Path):
    """每个词表之家**只**声明一次自己那组取值（不能自己跟自己漂移）。"""
    declarations: dict[tuple[str, ...], int] = defaultdict(int)
    for _, values in _closed_sets(home):
        declarations[values] += 1
    repeated = {values: count for values, count in declarations.items() if count > 1}
    assert not repeated, f"{home.name} 里同一组取值声明了多次：{repeated}"
