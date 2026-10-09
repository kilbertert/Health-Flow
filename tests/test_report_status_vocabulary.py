"""「有没有评估过」只有一个答案：报告状态（#187）。

准入台账刻意**不带**任何旁证：既没有时间戳，也没有「是否评估过」的布尔。那份信息由
报告状态给出，而状态在 GLOSSARY 里已经是单一事实来源（`app/service/report_status.py`
的词汇表 + 迁移函数）。这里再盖一个章，等于给同一个问题留第二个答案 —— 两者一旦不一致，
读的人无法判断该信哪个。

本文件是那份约束的守卫。它做三件事：

1. 把散落在 `app/` 里的**状态字面量**钉在状态模块声明的那一套上；
2. 禁止 `app/api` 为回答准入问题而**读审计流的内容**；
3. 钉住台账字段里没有旁证。

它刻意**不**去统一 `{"confirmed", "assessed"}` 这类**业务组合**：那些回答的是另一个问题
（「这份报告的解读算不算完成到可以取货/可以评估」），把它们并进词汇表会把两个概念混成
一个 —— 那正是本 PRD 主题的反面用法。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import get_args

from app.service.report_status import PATIENT_STATUSES, ReportStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_MODULE = REPO_ROOT / "app" / "service" / "report_status.py"
API_MODULE = REPO_ROOT / "app" / "api" / "report.py"
VOCABULARY = set(get_args(ReportStatus))


def test_the_vocabulary_is_the_five_known_statuses():
    assert {"processing", "pending_confirmation", "confirmed", "assessed", "failed"} == VOCABULARY
    # 有序的那一份与类型是同一套（两个视图，不是两份词表）。
    assert tuple(PATIENT_STATUSES) == tuple(get_args(ReportStatus))


def test_the_three_places_that_answer_was_assessed_agree():
    """三处「有没有评估」必须给同一个答案。

    只允许这三处：契约里的词表（`Literal[...]`）、状态模块、准入投影的那一个判据。
    """
    from app.service import admission_projection

    assert admission_projection.has_conclusion("assessed") is True
    assert admission_projection.has_conclusion("confirmed") is False
    assert admission_projection.has_conclusion("pending_confirmation") is False
    # 未知状态（历史脏数据）按「还没有结论」处理 —— 与「未知的类按 server-managed 处理」
    # 同一种保守取向：不因为读不懂就宣称有结论。
    assert admission_projection.has_conclusion(None) is False
    assert admission_projection.has_conclusion("something-from-the-future") is False


def test_the_ledger_is_absent_when_the_report_has_not_been_assessed():
    """台账的空/非空必须由报告状态决定 —— 这是那个「答案」被真正用上的地方。

    没有这一条时，守卫只钉住了判据本身，钉不住**接线**：一次「给未评估的报告也造一份
    全 0 台账」的改动会让读的人把「还没有结论」读成「没有一行被拦下」，而上面那几条
    断言全部照绿。
    """
    from app.service.admission_projection import admission_shapes

    class _Row:
        id = 1
        metric_code = None
        metric_name = "空腹血糖"
        confirmation_status = "confirmed"
        confirmation_value = None

    rows = [_Row()]
    assessed_pairs, assessed_ledger = admission_shapes(rows, assessed=True)
    unassessed_pairs, unassessed_ledger = admission_shapes(rows, assessed=False)

    assert assessed_ledger is not None
    assert unassessed_ledger is None, "未评估时不得有台账（那是同一个问题的第二个答案）"
    assert [reason for _, reason in unassessed_pairs] == [None], "未评估时也不得逐行给结论"
    assert len(assessed_pairs) == len(unassessed_pairs) == 1


# ── 状态字面量：全文件扫描 ──────────────────────────────────────────────────


def test_no_status_literal_outside_the_vocabulary_appears_anywhere_in_the_app():
    """**全文件**扫状态字面量，而不是只看比较表达式。

    我第一版只查 `ast.Compare` 的常量操作数 —— 实测那会漏掉**全部**真实形态：

    | 形态 | 第一版 |
    | --- | --- |
    | `report.status in {"confirmed", "assessd"}` | 漏（集合字面量，不是 Constant 操作数） |
    | `BAD = "assessd"` 后 `report.status == BAD` | 漏（比较的是名字） |
    | `{"assessed": ...}`（字典值） | 漏（根本不在比较节点里） |
    | `match report.status: case "assessd":` | 漏（`match_case` 不是 Compare） |

    而本仓**实际用的**正是集合字面量与模块常量两种写法（`COMPLETED_FOR_LINK`）——
    换句话说，第一版只会在仓库从不使用的写法上变红。

    改成扫整个文件里的字符串常量：任何与某个已知状态「相似」却不属于词表的字符串都算。
    这样上表四种形态一网打尽（f-string 里的常量也覆盖，`JoinedStr` 的片段是 `Constant`）。
    代价是可能误报，而**当前仓库测下来一个误报都没有** —— 我量过才敢这么宽。

    为什么可以这么宽：这类字面量在这个代码库里**本就不该存在**。状态的写入必须过
    `report_status.transition`，读取一律是与词表成员比较 —— 一个「几乎拼对」的字符串常量
    没有正当用途。射程之外的是 `{"confirmed", "assessed"}` 这类**已知成员构成的业务组合**：
    它们是合法成员，本守卫按定义不碰。
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        if path.name == "report_status.py":
            continue  # 词表的家
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if _looks_like_a_status(node.value) and node.value not in VOCABULARY:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.value!r}")
    assert not offenders, (
        "这些字符串与某个已知状态相似却不在词表里（拼错、或用了已退役的名字）：" + "; ".join(offenders)
    )


def _looks_like_a_status(value: str) -> bool:
    """像状态名的字符串：小写 + 下划线，且与某个已知状态**相似**（编辑距离 1 以内）。

    「相似」而不是「等长」：`assessd` / `assesed` 这类常见拼错都在一字之差，而
    `application/pdf`、`fasting_glucose` 这类同形但无关的字符串不会因为差一个字母就被
    误判成状态 —— 我用一张表把这三种情形都验过。
    """
    if not re.fullmatch(r"[a-z_]{4,}", value):
        return False
    return any(_within_one_edit(value, known) for known in VOCABULARY)


def _within_one_edit(left: str, right: str) -> bool:
    """替换 / 增 / 删一个字即为真；相等或差两个及以上为假。"""
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) == 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    return any(long[:index] + long[index + 1 :] == short for index in range(len(long)))


def test_the_similarity_check_does_not_fire_on_ordinary_strings():
    """相似判定本身的行为：确切的五个状态、无关字符串、以及常见拼错。

    这条是上面那个「全文件扫描」的基础设施测试 —— 它坏掉时，那条守卫会**静默地**变成
    永不报错（或永远报错），而两种情形都不会有人注意到。
    """
    for known in VOCABULARY:
        assert not _looks_like_a_status(known), known
    for ordinary in ("application/pdf", "fasting_glucose", "assessed_at", "Assessed", "", "a", "x" * 40):
        assert not _looks_like_a_status(ordinary), ordinary
    for typo in ("assessd", "assesed", "confirme", "asessed", "faild"):
        assert _looks_like_a_status(typo), typo


# ── 审计流：不得为回答业务问题而读它 ────────────────────────────────────────


def test_the_api_does_not_read_the_audit_stream_to_answer_a_business_question():
    """`app/api` 不得**读审计流的内容**来回答「有没有评估」。

    审计流服务的是**审计**，不是状态机的一部分。让一份对外契约依赖它，等于把审计变成
    承重墙 —— #184 之前的实现正是这么做的（从审计里取最近一次 `assessed` 的时间戳）。

    审计事件仍可**原样出域**（那是它本来的用途）。所以判据不是「名字出现在哪一行」——
    按字面量计数或按行首匹配都能被 `getattr(report, "audit_events")` 或一个辅助函数绕过
    （我实测过那两种写法，它们都从第一版守卫底下漏过去了）。

    改成一个能分辨「读」与「透出」的判据：**找出所有对审计流的取值点**（`.audit_events`
    属性、以及任何等于 `"audit_events"` 的字符串常量 —— `getattr` 那条路走的就是后者），
    要求其中**恰好一个**，且它必须出现在那一个原样透出的 `for ... in sorted(...)` 里。
    任何别的取值点都意味着有人在另读一遍。
    """
    source = API_MODULE.read_text(encoding="utf-8")
    reads = _audit_stream_reads(ast.parse(source))
    assert len(reads) == 1, (
        "审计流只应在原样透出那一处被读取；现在有 "
        f"{len(reads)} 处：{[(line, kind) for line, kind in reads]}"
    )
    line, kind = reads[0]
    assert kind == "pass-through", (
        f"审计流在 line {line} 被读取，但那不是在原样透出的 `for ... in sorted(...)` 里"
        "（原样透出只把整个对象映射进契约，不筛不挑）"
    )
    # 复核那一处确实是原样透出：**没有过滤条件**。
    #
    # 推导式的 `ifs` 就是过滤器 —— `for event in sorted(report.audit_events) if event.action
    # != "secret"` 实测能绕过只看「取值点在哪」的那版判据（它仍是一个原样透出形状的
    # 推导式）。出域一旦有了筛选条件，就不再是审计流本身了。
    assert not _pass_through_has_a_filter(ast.parse(source)), (
        "原样透出那一处带了过滤条件 —— 出域的就不再是审计流本身"
    )


def _audit_stream_reads(tree: ast.AST) -> list[tuple[int, str]]:
    """审计流的每一个**取值点**，以及它是「原样透出」还是「另读一遍」。

    取值点 = `.audit_events` 属性访问 + 字符串常量 `"audit_events"`（`getattr` 的形式）。

    「原样透出」的判据是：那个取值点必须是某个 `sorted(...)` 调用的实参，而那个调用是
    某个**推导式**的迭代表达式（`for event in sorted(report.audit_events, key=...)` 是
    推导式的生成器，不是 `ast.For` —— 我第一版只找 `ast.For`，于是把合法的透出那一处
    也判成了「另读一遍」）。只找推导式带来的另一个好处是语义更准：逐条映射进契约正是
    靠推导式做的。
    """
    reads: list[tuple[int, str]] = []
    pass_through: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            continue
        for generator in node.generators:
            call = generator.iter
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "sorted"
                and call.args
                and _is_audit_stream(call.args[0])
            ):
                pass_through.add(id(call.args[0]))
    for node in ast.walk(tree):
        if _is_audit_stream(node):
            reads.append((node.lineno, "pass-through" if id(node) in pass_through else "separate-read"))
    return reads


def _pass_through_has_a_filter(tree: ast.AST) -> bool:
    """原样透出的那个推导式里有没有 `if`（过滤器）。"""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            continue
        for generator in node.generators:
            call = generator.iter
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "sorted"
                and call.args
                and _is_audit_stream(call.args[0])
                and generator.ifs
            ):
                return True
    return False


def _is_audit_stream(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "audit_events":
        return True
    return isinstance(node, ast.Constant) and node.value == "audit_events"


def test_the_ledger_schema_carries_no_timestamp_or_assessed_flag():
    """台账只描述行数 —— 没有时间戳、也没有「是否评估过」的布尔。

    这是本票的核心约束：那个问题的答案在报告状态上，不在台账里。
    """
    from app.schema.report import AdmissionLedger

    fields = set(AdmissionLedger.model_fields)
    assert fields == {"included", "normal", "skipped", "unmatched", "not_evaluated", "total"}
    assert not any("time" in name or name.endswith("_at") or name.startswith("is_") for name in fields)
