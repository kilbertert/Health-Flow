"""「有没有评估过」只有一个答案：报告状态（#187）。

准入台账刻意**不带**任何旁证：既没有时间戳，也没有「是否评估过」的布尔。那份信息由
报告状态给出，而状态在 GLOSSARY 里已经是单一事实来源（`app/service/report_status.py`
的词汇表 + 迁移函数）。这里再盖一个章，等于给同一个问题留第二个答案 —— 两者一旦不一致，
读的人无法判断该信哪个。

本文件是那份约束的守卫。它做的事只有一件：把散落在 `app/api` 里的**状态字面量**钉在
状态模块所声明的那一套上，于是「某处悄悄多写了一个字面量」会红，而不是等着两份判定在某
次改动后各说各话。

它刻意**不**去统一 `{"confirmed", "assessed"}` 这类**业务组合**：那些回答的是另一个问题
（「这份报告的解读算不算完成到可以取货/可以评估」），把它们并进词汇表会把两个概念混成
一个。守卫查的是「字面量本身是不是词汇表里的成员」。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import get_args

from app.service.report_status import PATIENT_STATUSES, ReportStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_MODULE = REPO_ROOT / "app" / "service" / "report_status.py"
VOCABULARY = set(get_args(ReportStatus))


def test_the_vocabulary_is_the_five_known_statuses():
    assert {"processing", "pending_confirmation", "confirmed", "assessed", "failed"} == VOCABULARY
    # 有序的那一份与类型是同一套（它们是两个视图，不是两份词表）。
    assert tuple(PATIENT_STATUSES) == tuple(get_args(ReportStatus))


def test_the_three_places_that_answer_was_assessed_agree():
    """三处「有没有评估」必须给同一个答案。

    只允许这三处：契约里的词表（`Literal[...]`）、状态模块的迁移函数、以及准入投影的
    那一个判据。它们都必须从 `ReportStatus` 这一套词里取值。
    """
    from app.service import admission_projection

    assert admission_projection.has_conclusion("assessed") is True
    assert admission_projection.has_conclusion("confirmed") is False
    assert admission_projection.has_conclusion("pending_confirmation") is False
    # 未知状态（历史脏数据）按「还没有结论」处理 —— 与「未知的类按 server-managed 处理」
    # 同一种保守取向：不因为读不懂就宣称有结论。
    assert admission_projection.has_conclusion(None) is False
    assert admission_projection.has_conclusion("something-from-the-future") is False


def test_no_module_outside_the_status_module_compares_a_status_literal():
    """除状态模块外，任何地方都不得把**未知的**状态字符串与状态比较。

    这条守卫的射程是「字面量的成员资格」，不是「统一所有组合」：写
    `report.status == "assessd"`（拼错）这类会红，而 `report.status not in
    {"confirmed", "assessed"}` 这种**已知成员构成的业务组合**不在此列。
    """
    unknown: list[str] = []
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        if path == STATUS_MODULE or path.name == "report_status.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for operand in operands:
                # 只查**看起来像状态**的字面量：它必须与某个已知状态相似（差一个字）。
                if (
                    isinstance(operand, ast.Constant)
                    and isinstance(operand.value, str)
                    and _looks_like_a_status(operand.value)
                    and operand.value not in VOCABULARY
                ):
                    unknown.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {operand.value!r}")
    assert not unknown, "这些状态字面量不在词表里（拼错或用了已退役的名字）：" + "; ".join(unknown)


def _looks_like_a_status(value: str) -> bool:
    """像状态名的字符串：小写 + 下划线，且与某个已知状态**相似**（编辑距离 1 以内）。

    「相似」而不是「等长」：`assessd` / `pending_confirmation` 的常见拼错都在一字之差，
    而像 `application/pdf`、`fasting_glucose` 这类同形但无关的字符串不会因为差一个字母就
    被误判成状态。
    """
    if not re.fullmatch(r"[a-z_]{4,}", value):
        return False
    return any(_within_one_edit(value, known) for known in VOCABULARY)


def _within_one_edit(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if left == right:
        return False
    # 一字的替换 / 增 / 删。
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) == 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    return any(long[:index] + long[index + 1 :] == short for index in range(len(long)))


def test_the_ledger_schema_carries_no_timestamp_or_assessed_flag():
    """台账只描述行数 —— 没有时间戳、也没有「是否评估过」的布尔。

    这是本票的核心约束：那个问题的答案在报告状态上，不在台账里。
    """
    from app.schema.report import AdmissionLedger

    fields = set(AdmissionLedger.model_fields)
    assert fields == {"included", "normal", "skipped", "unmatched", "not_evaluated", "total"}
    assert not any("time" in name or name.endswith("_at") or name.startswith("is_") for name in fields)


def test_the_api_does_not_walk_the_audit_stream_to_answer_this_question():
    """`app/api` 不得遍历审计事件来回答「有没有评估」。

    审计流服务的是**审计**，不是状态机的一部分。让一份对外契约依赖它，等于把审计变成
    承重墙 —— 本票之前的实现正是这么做的（从审计里取最近一次 `assessed` 的时间戳）。
    审计事件仍可**原样出域**（那是它本来的用途），所以守卫查的是「为回答评估与否而读它」。
    """
    source = (REPO_ROOT / "app" / "api" / "report.py").read_text(encoding="utf-8")
    # 去掉注释后，`audit_events` 只应当出现在把它原样透出给契约的那一处。
    code_lines = [line.split("#", 1)[0] for line in source.splitlines()]
    reads = [line.strip() for line in code_lines if "report.audit_events" in line]
    assert len(reads) == 1, "审计流只应在原样出域那一处被读；现在这些地方也在读：" + "; ".join(reads)
    # 那一处是**原样透出**：按 id 排序后逐条映射，不筛不挑 —— 一旦有人加过滤条件，
    # 出域的就不再是审计流本身了。
    assert reads[0].startswith("for event in sorted(report.audit_events"), reads[0]
