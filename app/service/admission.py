"""解读准入的**判定**（GLOSSARY.md 的「解读准入」）。

词表在 `app/service/admission_vocabulary.py` —— 那里只有声明、不 import 任何项目
模块，所以出域契约（`app/schema/evidence.py`）能直接消费它而不成环。本模块在词表之
上放**判定**：一条指标行的准入结论、以及它与「异常判定」共用的那份值/范围判定。

三处仍**刻意不同**、且由测试钉住的地方（不是遗漏）：

- **尚未核对不是准入结论**（#203）。一行值/单位/参考范围/原文证据/页码齐全时，判定照常
  走完，落 `awaiting_confirmation`（可判、待核对）而不是提前收口 —— 那个词以前叫
  `pending`，与「指标确认决策」词表里患者那个 `pending` 同名，于是「患者没看过的正常
  行」与「判不了的行」在数据上不可分辨。`excluded` 仍然是一个表态、也是一个结论：
  只有它退出异常口径（`_decidable`）。
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
    NORMAL_REASONS,
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
    "signed_value_reason",
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
# 出现它们时，「两个数」不是「用哪个」的问题：`0 x 10^6/L`（单位被抽进值）与 `10:00`
# （时间）都会抽成恰好两个数字，但患者在那两个数之间没有可做的选择。
_TWO_VALUE_BLOCKERS = (":", "^")


# 值文本里的数值**字面量**边界。与 `_number_re` **分开**是刻意的：`_number_re` 还服务
# 「原文证据里有没有这个数」的比对（那里放宽是安全的），而这一条只判「值能不能用」——
# 它额外把千分位逗号与指数记法排除在外：`1,234` 与 `1e3` 被抽成两个数字会让页面给出两个
# **错的**候选（`1` 与 `234`），患者点哪个都是错的。
#
# 上限写在明处：空格分组的数字（`1 234`）仍会被看成两个值 —— 而空格恰好是「两个值」最
# 常见的形态（`3.39 / 3.63`、`76.1kg 83.6kg`），两者无法从字面区分。
# 数字类**显式**写出来（ASCII + 全角），而不是用 `\d`：JS 的 `\d` 只认 ASCII，而两侧
# 必须是同一份判据。全角数字在报告里真实出现过（`１.２`），一行全角写的双值若在服务端
# 是 `two_values`、在页面上什么都不显示，患者就白等一次。
_value_number_re = re.compile(r"(?<![0-9０-９.,eE])-?[0-9０-９]+(?:\.[0-9０-９]+)?(?![0-9０-９.,eE])")


def _numbers(value_text: str | None) -> list[float]:
    """文本里出现的**全部**数值字面量，按出现顺序。"""
    return [float(match) for match in _value_number_re.findall(value_text or "")]


def single_number(value: str | None) -> float | None:
    """文本里的唯一一个数；不是恰好一个就返回 ``None``。

    这是本模块与「恰好一个数」有关的唯一实现。此前 `_single_number` 是
    `evidence_bridge` 的私有函数，而前端为了同一个判断复刻过一遍（`valueIsUsable`
    的注释写着「必须与后端 `_single_number` 一致」）。
    """
    matches = _value_number_re.findall(value or "")
    return float(matches[0]) if len(matches) == 1 else None


def value_reason(value_text: str | None) -> str | None:
    """值本身能不能用：``missing_value`` / ``two_values`` / ``invalid_value`` / ``None``。

    四句话对应四件不同的事，其中第三句是本票（#204）新拆出来的：

    - ``missing_value`` —— 值还没解析出来，患者去等；
    - ``invalid_value`` —— 值不是一个数（混进了文字、被截断），患者去修正；
    - ``two_values`` —— **两个数都在**（页面印一行、患者手写一行），缺的是「哪个才是
      这一项的当前值」。患者要做的不是重输一遍数字（他本来就写着），而是**选一个**。
      把它压进 ``invalid_value`` 会让页面问一个他已经答过的问题。
    - 参考范围缺失（见 `reference_reason`）又是另一件事：值本身完全正常。
    """
    text = (value_text or "").strip()
    if not text:
        return "missing_value"
    # 带符号的值（`<20`、`≥3.9`）先于数值计数：`single_number('<20')` 会解析出 20，
    # 但那是「小于 20」的上界，不是一个测量值。这一条与异常判定的拒绝一致。
    # 它**先于** `two_values`：`<3 x 10^6/L` 里那个 3 与 10 不是两个测量值，是一个
    # 带单位的界 —— 判成「两个值」会问患者一个不存在的问题。
    if any(marker in text for marker in _SIGN_MARKERS):
        return "invalid_value"
    numbers = _numbers(text)
    # 两个候选要**恰好两个**，而且文本里不能有 `:` / `^` —— 那两个符号说明这些数字不是
    # 一组并排的测量值：`0 x 10^6/L`（单位被抽进了值）与 `10:00`（时间）都不是「用哪个」
    # 的问题。真实报告里被抽成两个值的形态是 `3.39 / 3.63`、`1.53 1.50`、`76.1kg 83.6kg`。
    #
    # 这条判据的上限写在明处：一个**恰好两个数字、又含冒号**的值会被判成 `invalid_value`
    # 而不是两个值（观测到的样本里没有这种形态）。那时患者要重新给一个值，不是选一个。
    if len(numbers) == 2 and not any(marker in text for marker in _TWO_VALUE_BLOCKERS):
        return "two_values"
    if len(numbers) == 0 or len(numbers) >= 2:
        return "invalid_value"
    return None


def reference_reason(reference: str | None) -> str | None:
    """参考范围能不能用：``missing_reference_range`` 或 ``None``。

    只判断「有没有一个可解析的区间」。定性项印的是**取值域**（`(Negative)`、`(Nil)`）
    而不是区间 —— 那种情形由 `word_domain_verdict` 与 `_has_no_own_interval` 分别处置，
    不是「缺判据」。
    """
    low, high = parse_reference_range(reference)
    return "missing_reference_range" if low is None and high is None else None


# 「报告把判据印成了同一个界」—— 值 `<20` 配上参考范围 `(<20)`（#235）。
#
# 这一类不是「值读不出来」：报告给的就是**一个界**（低于检出限、或低于参考上限），
# 而且它把同一句话**同时**印在了值与参考范围两栏。系统原本一见 `<`/`>` 就判
# `invalid_value`，于是患者面对一个他无法修正的东西 —— 报告上根本没有精确值可填。
_SIGNED_BOUND_RE = re.compile(r"(?P<sign><=|>=|≤|≥|<|>)\s*(?P<number>-?\d+(?:\.\d+)?)")


def _bound_key(text: str) -> str | None:
    """文本里那个「界」的规范写法（方向 + 数值），没有界时返回 ``None``。

    `≤`/`>=` 这类同一个关系的不同写法归一成一种（`<=` / `>=`），空白不参与比较 ——
    报告与抽取器对同一件事的排版不必逐字相同，而**方向与数值**必须。
    """
    match = _SIGNED_BOUND_RE.search(text or "")
    if match is None:
        return None
    sign = {"≤": "<=", "≥": ">="}.get(match["sign"], match["sign"])
    return f"{sign}{match['number']}"


def _is_exactly_one_bound(value_text: str | None, unit: str | None) -> bool:
    """这个格子**只有一个界**，且它在**开头** —— 之前之后都没有别的东西（除了单位）。

    这是本判据的**证伪**一步，两侧都要（评审在 #237 指出它一开始只查了后面）：

    - **前面**：`40 >20` 里那个 40 是一个测量值，`>20` 只是它旁边的一句话 —— `search`
      会把尾巴当成界，于是把「40 相对 >20」判成正常。所以界必须**匹配在开头**。
    - **后面**：`<20 IU/ml` 之后是它的单位，`<1.0` 之后什么都没有 —— 都算「这一格是一个
      界」；而 `<20 5.2` 后面还有东西，就不是了。

    **不预先折叠空白**：`<20 5.2` 折叠之后成了 `<205.2`，那第二个数就被吃进了界里、尾巴
    看起来是空的（本函数第一版就是这么错的，被跨语言那条断言逮住）。空白只在比较单位时
    不参与。
    """
    text = (value_text or "").strip()
    match = _SIGNED_BOUND_RE.match(text)
    if match is None:
        return False
    tail = text[match.end() :].strip()
    if not tail:
        return True
    return unit is not None and "".join(tail.split()).casefold() == "".join(unit.split()).casefold()


def _reference_is_exactly_this_bound(reference: str | None, key: str) -> bool:
    """参考范围里**只有这一个界**，没有第二个界或另一个数。

    `(<20)`、`< 20` 都算；而 `>20, <30` 不是 —— 那说明这一行有上下两个判据，光看其中
    一个就下「正常」会在另一个上错（评审在 #237 指出）。判据是数值字面量的个数：参考
    范围里恰好一个数，且它就是这个界。
    """
    text = reference or ""
    return len(_numbers(text)) == 1 and _bound_key(text) == key


def signed_value_reason(value_text: str | None, reference: str | None, unit: str | None) -> str | None:
    """带符号的值里，**报告自己把判据印成了同一个界**时给出准入结论，否则 ``None``。

    判据是「报告把同一句话印在了两栏」。三个条件**全部**成立才算，缺一即是「不适用」
    —— 本函数只加一个结论，从不撤销别的：

    1. **值就是一个界**（`_is_exactly_one_bound`）：界在开头，之后只剩它自己的单位。
       `<20 IU/ml` 与 `<1.0` 都算；`40 >20`（界之前有测量值）、`<20 5.2`（界之后还有东西）
       不算。
    2. **参考范围里也只有这一个界**（`_reference_is_exactly_this_bound`）：方向与数值都
       相同，且没有第二个判据。LDL-C 那一类风险目标印的是另一个数（`<2.6` 对 `<=1.8`），
       `>20, <30` 那种双界也不够 —— 都不是「报告把同一句话印在两处」。
    3. 由 1+2 蕴含：`AIP < 0.01` 的参考范围是空的，影射到哪一个阈值是医学判断、不是
       文本问题 —— 不猜（#235 的「不做」），于是它继续落 `invalid_value`。

    为什么这里敢下「正常」这个结论：报告说「低于这个界」，而那个界就是**它自己印的
    参考上界/下界**，所以「低于上界」与「在上界之内」是同一句话。放宽一步的代价是
    把一个真异常说成正常，所以本判据只认报告**自己**把同一句话印在两处的情形。
    """
    if not _is_exactly_one_bound(value_text, unit):
        return None
    key = _bound_key(value_text or "")
    if key is None or not _reference_is_exactly_this_bound(reference, key):
        return None
    return "within_reference_range"


def word_domain_verdict(value_text: str | None, reference: str | None) -> bool | None:
    """参考范围是一个**词**时，值与它是否相符 —— ``True`` 相符、``False`` 不符、``None``
    这个判据不适用（没有词、或值不是这个词域里的一个词）。

    定性项的报告原文常把它自己的取值域印在括号里：`Nitrite: 亚硝酸盐 Negative
    (Negative)`、`Bilirubin: 尿胆红素 Nil (Nil)`。那就是**这一项的判据**，不是「缺参考
    范围」—— 报告自己说清楚了什么算正常（#206：「只认报告印出来的取值域」）。

    两条限制写在明处：
    - 值的**词形**要能比：两侧都去掉括号、空白并折叠大小写，`Nil` 与 `NIL` 算相符，
      `Nil` 与 `Negative` 不算（它们是报告上的两个不同的词）。
    - 相符只说「正常」。**不符时本函数给不出结论**，交回调用方按值这一类处置（他需要
      核对报告）—— 让一个词形的值跨证据边界是另一件事（`_observation_payload` 要一个
      浮点数），不在本票射程内。
    """
    word = _as_word(reference)
    if word is None:
        return None
    value = _as_word(value_text)
    if value is None:
        return None
    return value == word


def _as_word(text: str | None) -> str | None:
    """文本当成一个**词**读出来；含数字或为空时返回 ``None``（那就不是词域）。"""
    if not text:
        return None
    stripped = text.strip().strip("（）()").strip()
    if not stripped or any(character.isdigit() for character in stripped):
        return None
    return " ".join(stripped.split()).casefold()


def value_level_reason(value_text: str | None, reference: str | None, unit: str | None = None) -> str | None:
    """「值能不能判」这一类原因的**唯一**判定：值原因优先，其次参考范围。

    准入结论与异常判定的守卫都消费本函数，所以同一行在这两个问题上不可能再得到两个
    名字（此前空值一行分别得到 `invalid_value` 与 `missing_value`）。

    **报告自己把判据印成同一个界**（`<20 IU/ml (<20)`）时先答：这一行**判得出**，值
    并没有坏（#235）。判定的顺序也在这一个函数里，不留给两侧各自摆一遍 —— 这正是上一句
    「同一处实现」的意思，而 `abnormal_flag_reason` 起初漏了这一步、被守卫立刻逮住。

    **两侧调的是同一个函数，不是两份「碰巧一致」的抄写** —— 这是本票的验收线之一，而
    它是靠这条调用关系成立的，不是靠断言。测试能钉住的是「结论逐字相同」；「同一处
    实现」只有这一条路径本身在守。
    """
    if signed_value_reason(value_text, reference, unit) is not None:
        return None
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
    # 患者已排除是一个**结论**：不必再看他这一行的值 —— 是他自己说不要的。
    if status == "excluded":
        return "excluded"
    # 尚未核对**不是**结论，只是「还没发生」。它必须让判定照常走完：可判的行
    # （值/单位/参考范围/原文证据/页码齐全）落 `awaiting_confirmation`（可判、待核对），
    # 判不了的行落它们各自真正的原因。此前这里对 `pending` 一律提前收口成 `pending`，
    # 于是两种处置相反的行拿到同一个词：一行的正确处置是「什么都不用做」，另一行是
    # 「必须他处理」（#203）。
    pending_review = status not in ADMITTED_STATUSES

    effective = effective_value(metric)
    value_text = effective.value
    reason = value_reason(value_text)
    if reason == "invalid_value":
        # 值不是一个数时，报告自己印出来的**取值域**仍然是判据（#206）：`Nitrite
        # Negative (Negative)` 判成正常，患者不必为它表态。
        verdict = word_domain_verdict(value_text, effective.reference_range)
        if verdict is True:
            return "within_reference_range"
        # 报告把判据印成了**同一个界**（`Rheumatoid Factor <20 IU/ml (<20)`）：这一行有
        # 结论，而且它是「正常」—— 报告说的是「低于这个界」，那个界就是它自己的参考上界。
        # 与上面同一句：不是「值坏了」，患者没有可修正的东西（#235）。
        signed = signed_value_reason(value_text, effective.reference_range, effective.unit)
        if signed is not None:
            return signed
        # 描述/记录项（血型、尿液外观、检验日期）本来就没有「是否异常」这回事 ——
        # 它们不是在等患者核对，而是这个判断对它不存在。
        if _has_no_own_interval(metric, effective) or _is_description(metric, effective):
            return "no_reference_concept"
        return reason
    if reason is not None:
        return reason
    # **没有异常概念**（比值型、描述型，见 `_has_no_own_interval`）在任何别的判定之前
    # 答掉：它比缺单位更根本 —— 这一类本来就没有「单位」这回事（比值是无量纲的），拿缺
    # 单位去拦它会把一个不存在的问题推给患者。顺序错了，那一类就永远走不到（#205 复核）。
    #
    # 只在**没有可解析范围**时成立：一个有自己区间的项即使名字里有「比率」也照常判。
    if _has_no_own_interval(metric, effective) and parse_reference_range(effective.reference_range) == (
        None,
        None,
    ):
        return "no_reference_concept"
    # 比值与标度值（pH、尿比重、`N:L Ratio`）的名字**就是**它的量纲：缺的不是抽取漏了
    # 单位，而是这一项本来没有单位。它们因此不该被 `missing_unit` 拦下 —— 患者没有
    # 任何东西可补（#235）。
    #
    # **但不能直接落到 `no_reference_concept`。** 那是「这一项没有异常概念」—— 一句
    # **关于这一项的断言**，而报告印了参考范围时它明明有判据（评审在 #237 指出）。说一句
    # 不成立的话还会与异常判定打架：`pH 8.0 (4.8-7.4)` 判得出 H。
    #
    # 正确的处置是**继续往下走、只是跳过单位那一关**：单位是「跨证据边界」的完备性要求
    # （`SourceObservation.unit` 是一个必填字符串），而无量纲项的「单位」就是「没有单位」
    # 这件事本身，如实送出去 `unit=""` 比编一个或据一个不存在的缺陷拦下它都诚实。
    if not effective.unit and not _unit_is_its_own_name(metric, effective):
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
        # 没有可解析的范围。真的缺判据那一类（体重、抗体滴度：该有范围而报告没印）
        # 留给患者核对 —— 它可修，而「没有异常概念」那一类不可修（它在上面就答过了）。
        return "missing_reference_range"
    # 参考范围的上下界**不要求**出现在原文证据里（#205）。范围是化验项的属性、不是这一次
    # 测量的一部分，患者要核对的是他的数值；而要求它出现会让「报告上写着范围、抽取的原文
    # 片段没带它」的行得到一句关于数据的错误理由（eGFR 报告上写着 `Normal (>=90)`，却因为
    # 片段里没带范围被判成「缺少原文证据」）。值仍然必须出现在证据里 —— 那一条说的是
    # 「这个数确实来自报告」，与本条不同。
    flag = infer_abnormal_flag(str(value), effective.reference_range, effective.unit)
    # 还没核对的行到这里已经是「可判」的了（值/单位/参考范围/证据/页码都齐）。它**不是**
    # 「未能进入解读」，那个结论要等患者核对过才成立 —— 所以这里如实说「可判、待核对」，
    # 由**前端**按异常判定决定要不要请他处理（判成 H/L 要，判成 N 不要）。
    #
    # 判成 N 且已核对过的行说 `within_reference_range` —— 它是「正常」，不是「未能解读」：
    # 把它并进「没能判定」那一类是误报（#161）。
    if flag == "N" and not pending_review:
        return "within_reference_range"
    if pending_review:
        return "awaiting_confirmation"
    if not code:
        return "unknown_metric_code"
    return None


@dataclass(frozen=True)
class AdmissionTally:
    """一整份报告的行数台账：每一行**恰好**归一类，四类相加等于已解析行数。

    `normal` 单列是有理由的，不是把 `skipped` 拆细：`within_reference_range` 的语义是
    「判定过，在参考区间内」—— **正常**。它与「没能进入解读」是两回事，患者侧的说法也
    必须不同（服务端在同一张卡片上会说「均在参考区间内」）。把它并进 `skipped` 会让
    「有 N 项未进入解读」把每一条正常指标都算进去，而那正是本次改动要消灭的矛盾。

    `not_evaluated` 收「患者还没核对过的可判行」与「患者已排除的行」：**两者都确实没有
    参与解读**（可判不等于已确认 —— 只有患者核对过的行才跨证据边界）。它们与 `skipped`
    的区别是原因不在这一行的数据上，而在患者那一步还没发生/已表态。
    """

    included: int
    normal: int
    skipped: int
    unmatched: int
    not_evaluated: int

    @property
    def total(self) -> int:
        return self.included + self.normal + self.skipped + self.unmatched + self.not_evaluated


def tally(reasons: list[str | None]) -> AdmissionTally:
    """把逐行的准入结论汇成台账。``None`` 计入 ``included``。"""
    counters = {"included": 0, "normal": 0, "skipped": 0, "unmatched": 0, "not_evaluated": 0}
    for reason in reasons:
        if reason is None:
            counters["included"] += 1
        elif reason in NORMAL_REASONS:
            counters["normal"] += 1
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


def infer_abnormal_flag(value: str | None, reference: str | None, unit: str | None = None) -> str | None:
    """纯函数形式的异常判定：``"H" | "L" | "N"``，输入不足返回 ``None``。

    定义见 GLOSSARY.md 的「异常判定」。它**只**看值与参考范围 —— 不看原文证据、不看
    页码，那是刻意的：异常判定回答「这个值相对这个范围偏高吗」，与「这条指标能不能跨过
    证据边界」是两个问题。``unit`` 唯一的用处是判「带符号的值是不是报告自己印的界」那
    一条（`<20 IU/ml` 的界后面跟着它的单位），它不是判定的一部分。
    """
    text = (value or "").strip()
    if not text:
        return None
    if any(marker in text for marker in _SIGN_MARKERS):
        # 带符号的值通常判不出来（`single_number('<20')` 给的是上界、不是一个测量值）。
        # 但报告把判据印成**同一个界**时它判得出、而且是正常：`Rheumatoid Factor <20
        # (<20)` 说的是「低于这个界」，那个界就是它印的参考上界（#235）。这一条与准入
        # 那一侧**同一份判据**（`signed_value_reason`）—— 两处各判一次会让同一行一边说
        # 「没有异常概念」、一边报 H（评审在 #237 指出）。
        return "N" if signed_value_reason(text, reference, unit) is not None else None
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


# 「这一项没有「是否异常」这个概念」—— 比值型与描述型（#205 / #206）。
#
# 判据是**报告自己印出来的东西**，不是一份我们维护的指标名清单：一份清单会随每一份新报告
# 过期，而「分子/分母」「比率」「区间名」这些标记就在页面上。
#
# 两类信号各管一半，缺一不可：
#   - **名称**（`比率` / `ratio`）：比值型的项没有自己的区间，而抽取有时把同一页 Target
#     那一栏的值落进了 `reference_range`（报告 57 的 `T Chol/HDL ratio 3.7 2.8`）——
#     只看「有没有范围」会把它当成有区间的正常项。
#   - **原文片段里的表头**（`REF. RANGES` / `Target`）：一行来自一张列了多栏参考值的表。
#
# 这条判据的上限写在明处：**它只认这几种形态**。别的「本来就没有判据」的项（例如某个只印
# 名称与数值的项）仍会落 `missing_reference_range`，患者会被问一次 —— 那比反过来（把该
# 问的项静默吞掉）安全。
_NO_OWN_INTERVAL_NAME_RE = re.compile(r"\b(?:ratio|index)\b|比率|比值", re.IGNORECASE)
_NO_OWN_INTERVAL_TEXT_RE = re.compile(
    r"ref\.?\s*range|target\s|reference\s*(?:range|interval)", re.IGNORECASE
)
# 判据明明是**阈值**的项（报告印的是 `Normal (>=90)`、`Positive (Normal <0.6)`）**不**在
# 这一条里：`parse_reference_range` 能从中解析出**一个**边界，所以它已被判成 H/L/N ——
# 那是正确的处置，本函数不把它捞走（#205 的「单边界判据」那一半）。

# 「这一项的单位是**它自己名字的一部分**」—— pH、尿比重（`SG`）、中性粒/淋巴比（`N:L`）
# 这类标度值与比值。它们的无量纲不是「抽取漏了」：`pH 7.0` 在报告上不带单位，因为 pH
# 就是个标度（定义即 −log[H⁺]）；`N:L Ratio` 是两个计数之比；`SG` 是相对水的密度比。
#
# **为什么单列一条、而不并进 `_NO_OWN_INTERVAL_*`**：那两个不看值，而大意是「这一项
# 没有异常概念」—— 那是**另一个结论**（`no_reference_concept`）。更重要的是，它们还要求
# 「范围里解析不出任何边界」，于是 `T Chol/HDL ratio` 反倒要走「缺参考范围」那条去问
# 患者：它的范围被抽取器塞了同一页 Target 栏的 `2.8`。本条的语义是「缺单位不是它的
# 缺陷」，范围照常可用 —— 比比值行更宽，所以对既有行**只增不减**。
#
# 判据分两半，因为两类信号的性质不同：
#
# - **比值**（`ratio` / `比率` / `比值`）：名字自己说了它是两个量之比，不需外部知识。
# - **标度值**（`pH` / `SG`）：这是**物理事实**，报告上没有任何文字能证明它 —— 一条
#   `Glucose: 葡萄糖 6.5` 与一条 `pH: 酸碱值 7.0` 在文本上完全同形（都是「ASCII 标签 +
#   冒号 + 中文全称 + 数字 + 区间」），差别只在「葡萄糖有量纲、pH 没有」。所以这里
#   必须有一份**有界**的词表，而它与本模块别处刻意不要的「指标名清单」不是一回事：
#   那份清单会随每一份新报告过期（所以我们不维护它），而「尿常规里哪些格子是标度/密度」
#   是**封闭**的 —— 常规尿分析的标度栏永远只有这两个，新报告不会让这张表变长。
#
# 上限写在明处：表外的无量纲项（例如某个只印名字的衍生比值指标）仍会落 `missing_unit`，
# 患者被问一次 —— 那比反过来（把该问的测量静默吞掉）安全。
#
# 比值那一半复用 `_NO_OWN_INTERVAL_NAME_RE` —— 两处问的是同一个「这是比值吗」，各写一份
# 就会分叉（一处认中文、一处不认）。
# 词间边界而不是子串：`SGOT`（谷草转氨酶的旧称）与 `pH` 里那个 `h` 都不该被误当成这两个。
_UNITLESS_SCALE_RE = re.compile(r"(?<![A-Za-z])(?:pH|SG)(?![A-Za-z])")

# 「这一行是描述或记录，不是一次测量」—— 血型、尿液外观、透明度、检验日期这类。它们没有
# 异常概念，因此也没有「该怎么判」这个问题（#206）。
#
# 判据是这一行**自己的形状**：值里一个数字都没有、参考范围也没有可解析的区间或词域。
# 一条 `Nitrite Negative (Negative)` 因此**不**在列（它有词域，能判）；而一条
# `Blood Group AB Rh(D) POSITIVE` 在列。
_DESCRIPTION_NAME_RE = re.compile(
    r"blood\s*group|\bgroup\b|appearance|colour|color|transparency|specimen|"
    r"collected|collection\s*date|report\s*date|barcode|血型|外观|颜色|透明度|"
    r"检验日期|报告日期|样本",
    re.IGNORECASE,
)


def _is_description(metric: Any, effective: Any) -> bool:
    """这一行是描述/记录项吗（没有异常概念，也不该问患者）。

    **名称 + 形状**两条一起用：名称给出候选（`Group` / `Colour` / `Transparency` /
    `Specimen`…），形状确认它确实不是一次测量（值里没有数字、没有可解析的区间或词域）。
    只按名称会误伤（`Group` 也可能是一个真的测量项），只按形状会误伤（一条把单位抽进值的
    坏行同样是「没有数字」）。
    """
    if not _DESCRIPTION_NAME_RE.search(str(getattr(metric, "metric_name", "") or "")):
        return False
    return word_domain_verdict(effective.value, effective.reference_range) is None


def _unit_is_its_own_name(metric: Any, effective: Any) -> bool:
    """缺单位是不是**这一项本来就没有单位**（比值、标度值）—— 见上面两条判据的说明。"""
    name = str(getattr(metric, "metric_name", "") or "")
    return bool(_NO_OWN_INTERVAL_NAME_RE.search(name) or _UNITLESS_SCALE_RE.search(name))


def _has_no_own_interval(metric: Any, effective: Any) -> bool:
    """这一项的判据不是一个区间，而是比值或描述 —— 见上面两个正则的说明。"""
    name = str(getattr(metric, "metric_name", "") or "")
    if _NO_OWN_INTERVAL_NAME_RE.search(name):
        return True
    # 原文证据里出现「REF. RANGES」/「Target」这类**表头**时，说明这一行来自一张列了多栏
    # 参考值的表 —— 那几栏不是「这一项自己的区间」。实测来源：报告 57 的
    # `T Chol/HDL ratio 总胆固醇与高脂胆固醇 3.7 2.8`（那个 2.8 是同一页 Target 那一栏的）。
    return bool(_NO_OWN_INTERVAL_TEXT_RE.search(str(effective.evidence_text or "")))


def _evidence_contains_value(evidence: str, value: float) -> bool:
    for match in _number_re.findall(unicodedata.normalize("NFKC", evidence)):
        if math.isclose(float(match), value, rel_tol=1e-9, abs_tol=1e-12):
            return True
    return False

