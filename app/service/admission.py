"""解读准入的**判定**（GLOSSARY.md 的「解读准入」）。

词表在 `app/service/admission_vocabulary.py` —— 那里只有声明、不 import 任何项目
模块，所以出域契约（`app/schema/evidence.py`）能直接消费它而不成环。本模块在词表之
上放**判定**：一条指标行的准入结论、以及它与「异常判定」共用的那份值/范围判定。

三处仍**刻意不同**、且由测试钉住的地方（不是遗漏）：

- `pending`（尚未核对）与 `excluded`（患者明确排除）是**准入结论**，不是「无法判定」。
  一份待确认的报告里，患者正是要在确认页上看到模型标出的异常候选，所以「异常判定」
  对 `pending` 行照常作答 —— 只有 `excluded` 退出异常口径（`_decidable`）。
- 缺单位 / 缺原文证据 / 缺页码会让一行**没进解读**，却不妨碍它的异常判定显示 H。
  这个分叉是「报告单说 H、解读里没有它」的唯一来源，本模块如实保留它，不替它遮掩。
- 准入结论回答「为什么没进解读」；异常判定的守卫回答「值能不能判」。两者在值/参考范围
  这一类原因上必须同名（本模块保证），在证据完备性这一类上本就无关。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.service.admission_vocabulary import (
    ADMITTED_STATUSES,
    NOT_EVALUATED_REASONS,
    SKIPPED_REASONS,
    UNMATCHED_REASONS,
    vocabulary,
)
from app.service.metric_effective_value import effective_value

__all__ = [
    "AdmissionTally",
    "admission_reason",
    "infer_abnormal_flag",
    "parse_reference_range",
    "reference_reason",
    "single_number",
    "tally",
    "value_level_reason",
    "value_reason",
    "vocabulary",
]

_number_re = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])")
_range_re = re.compile(r"(?P<low>-?\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(?P<high>-?\d+(?:\.\d+)?)")
_upper_re = re.compile(r"(?:<|<=|≤)\s*(?P<high>-?\d+(?:\.\d+)?)")
_lower_re = re.compile(r"(?:>|>=|≥)\s*(?P<low>-?\d+(?:\.\d+)?)")
_SIGN_MARKERS = ("<", ">", "≤", "≥")


def single_number(value: str | None) -> float | None:
    """文本里的唯一一个数；不是恰好一个就返回 ``None``。

    这是本模块与「恰好一个数」有关的唯一实现。此前 `_single_number` 是
    `evidence_bridge` 的私有函数，而前端为了同一个判断复刻过一遍（`valueIsUsable`
    的注释写着「必须与后端 `_single_number` 一致」）。
    """
    matches = _number_re.findall(value or "")
    return float(matches[0]) if len(matches) == 1 else None


def value_reason(value_text: str | None) -> str | None:
    """值本身能不能用：``missing_value`` / ``invalid_value`` / ``None``。

    「值还没解析出来」与「值不是一个数」是**两句不同的话**：前者患者去等解析，后者
    患者去修正。把它们合成一句会把人引向错误的动作 —— 而参考范围缺失（见
    `reference_reason`）又是第三件事，值本身完全正常。
    """
    text = (value_text or "").strip()
    if not text:
        return "missing_value"
    # 带符号的值（`<20`、`≥3.9`）先于单值判定：`single_number('<20')` 会解析出 20，
    # 但那是「小于 20」的上界，不是一个测量值。这一条与异常判定的拒绝一致。
    if any(marker in text for marker in _SIGN_MARKERS):
        return "invalid_value"
    if single_number(text) is None:
        return "invalid_value"
    return None


def reference_reason(reference: str | None) -> str | None:
    """参考范围能不能用：``missing_reference_range`` 或 ``None``。

    定性指标（尿蛋白「阴性」）没有可解析的参考范围是常态 —— 这不是值的毛病。
    """
    low, high = parse_reference_range(reference)
    return "missing_reference_range" if low is None and high is None else None


def value_level_reason(value_text: str | None, reference: str | None) -> str | None:
    """「值能不能判」这一类原因的**唯一**判定：值原因优先，其次参考范围。

    准入结论与异常判定的守卫都消费本函数，所以同一行在这两个问题上不可能再得到两个
    名字（此前空值一行分别得到 `invalid_value` 与 `missing_value`）。

    **两侧调的是同一个函数，不是两份「碰巧一致」的抄写** —— 这是本票的验收线之一，而
    它是靠这条调用关系成立的，不是靠断言。测试能钉住的是「结论逐字相同」；「同一处
    实现」只有这一条路径本身在守。
    """
    return value_reason(value_text) or reference_reason(reference)


def admission_reason(
    metric: Any,
    *,
    code: str | None,
) -> str | None:
    """一条指标行的准入结论：``None`` 表示**进入解读**，否则是唯一的原因。

    这是本概念的单一判定入口。调用方负责把 ``code`` 解析好（`resolve_metric_code`）
    再传进来 —— 编码解析有自己的家，本函数不重复它。

    判定顺序即优先级，且**值是第一位**的：值都没解析出来的行，先去补单位没有意义；
    而且这样「值这一类原因」在两侧才必然同名。
    """
    status = getattr(metric, "confirmation_status", None) or "pending"
    if status not in ADMITTED_STATUSES:
        # 尚未核对与患者已排除是两种显式结论。今天它们落在三桶之外、由前端从原始
        # `confirmation_status` 猜 —— 从这张票起由服务端给。
        return "excluded" if status == "excluded" else "pending"

    effective = effective_value(metric)
    value_text = effective.value
    reason = value_reason(value_text)
    if reason is not None:
        return reason
    if not effective.unit:
        return "missing_unit"
    evidence = effective.evidence_text
    if not evidence:
        return "missing_source_evidence"
    if getattr(metric, "page_number", None) is None:
        return "missing_source_page"
    value = single_number(value_text)
    assert value is not None  # value_reason 已经保证
    if not _evidence_contains_value(evidence, value):
        return "missing_source_evidence"
    low, high = parse_reference_range(effective.reference_range)
    if low is None and high is None:
        return "missing_reference_range"
    if low is not None and not _evidence_contains_value(evidence, low):
        return "missing_source_evidence"
    if high is not None and not _evidence_contains_value(evidence, high):
        return "missing_source_evidence"
    flag = infer_abnormal_flag(str(value), effective.reference_range)
    # 判成 N 就是「在参考范围内」—— 准入结论如实说 `within_reference_range`。
    # 它是「正常」，不是「未能解读」：把它并进「没能判定」那一类是误报（#161）。
    if flag == "N":
        return "within_reference_range"
    if not code:
        return "unknown_metric_code"
    return None


@dataclass(frozen=True)
class AdmissionTally:
    """一整份报告的行数台账：每一行**恰好**归一类，且三类相加等于已解析行数。"""

    included: int
    skipped: int
    unmatched: int
    not_evaluated: int

    @property
    def total(self) -> int:
        return self.included + self.skipped + self.unmatched + self.not_evaluated


def tally(reasons: list[str | None]) -> AdmissionTally:
    """把逐行的准入结论汇成台账。``None`` 计入 ``included``。"""
    counters = {"included": 0, "skipped": 0, "unmatched": 0, "not_evaluated": 0}
    for reason in reasons:
        if reason is None:
            counters["included"] += 1
        elif reason in SKIPPED_REASONS:
            counters["skipped"] += 1
        elif reason in UNMATCHED_REASONS:
            counters["unmatched"] += 1
        elif reason in NOT_EVALUATED_REASONS:
            counters["not_evaluated"] += 1
        else:  # pragma: no cover - 词表漂移会被 test_admission 的守卫逮住
            raise ValueError(f"准入结论不在词表里：{reason!r}")
    return AdmissionTally(**counters)


def parse_reference_range(value: str | None) -> tuple[float | None, float | None]:
    """参考范围文本 → ``(low, high)``；解析不出返回 ``(None, None)``。

    **逐字搬自 `evidence_bridge`，语义一字未改**：不剥括号、不归一化单位。括号里包着
    单位的写法（`3.9-6.1 mmol/L`）由正则本身处理 —— 擅自在这里加一步预处理会改变一
    批既有报告的判定，那不在本票的射程内。
    """
    text = (value or "").strip()
    match = _range_re.search(text)
    if match:
        low, high = float(match["low"]), float(match["high"])
        return (low, high) if low <= high else (None, None)
    match = _upper_re.search(text)
    if match:
        return None, float(match["high"])
    match = _lower_re.search(text)
    if match:
        return float(match["low"]), None
    return None, None


def infer_abnormal_flag(value: str | None, reference: str | None) -> str | None:
    """纯函数形式的异常判定：``"H" | "L" | "N"``，输入不足返回 ``None``。

    定义见 GLOSSARY.md 的「异常判定」。它**只**看值与参考范围 —— 不看单位、不看原文
    证据、不看页码，那是刻意的：异常判定回答「这个值相对这个范围偏高吗」，与「这条
    指标能不能跨过证据边界」是两个问题。
    """
    text = (value or "").strip()
    if not text or any(marker in text for marker in _SIGN_MARKERS):
        return None
    number = single_number(text)
    if number is None:
        return None
    low, high = parse_reference_range(reference)
    if low is None and high is None:
        return None
    if low is not None and number < low:
        return "L"
    if high is not None and number > high:
        return "H"
    return "N"


def _evidence_contains_value(evidence: str, value: float) -> bool:
    for match in _number_re.findall(unicodedata.normalize("NFKC", evidence)):
        if math.isclose(float(match), value, rel_tol=1e-9, abs_tol=1e-12):
            return True
    return False

