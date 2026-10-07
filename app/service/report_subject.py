"""报告主体一致性（GLOSSARY.md 的「报告主体一致性」）。

「这批报告文件是不是同一个人的」此前由三套通道回答，且互不感知：

- **上传时的隐式判定**：单文件自动判 `same`，多文件判 `uncertain` —— 埋在表单
  处理里的一行表达式；
- **确认时的闸门**：`report.subject_consistency != "same" and confirmation.subject_consistency != "same"`
  → 422。注意它测的是**列的当前值**，不是患者这次的表态：列已是 `same` 时，
  任何表态（包括 `different`）都放行；
- **表态写入**：`confirmation.subject_consistency or report.subject_consistency or "same"`。

三套之间的语义不统一，产生了四个患者可见矛盾（详见 #100）。本模块把它们收成
一处：初始判定、闸门、表态写入都调用这里。
"""

from __future__ import annotations

from typing import Any, Literal

SubjectConsistency = Literal["same", "different", "uncertain"]


class SubjectGateError(ValueError):
    """主体闸门未通过。``detail`` 是给患者的说明。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def initial_consistency(file_count: int) -> SubjectConsistency:
    """上传时的初始判定：单文件自动判为同一主体，多文件不确定。

    单文件没有「是不是同一个人」的问题 —— 一个文件只能属于一个人。多文件才有，
    所以默认 `uncertain`，等患者显式确认。
    """
    return "same" if file_count == 1 else "uncertain"


def gate(current: str | None, declared: str | None) -> SubjectConsistency:
    """确认闸门。通过则返回**应当落定的**一致性值，不通过则抛 ``SubjectGateError``。

    语义统一为三条：

    1. 解析后的一致性为 ``same``（含单文件自动判定）—— **无须表态**，落定 ``same``；
    2. 不为 ``same`` —— 须显式 ``same`` 表态，落定 ``same``；
    3. **任何非 ``same`` 的显式表态是停止** —— 不置 confirmed、不进入评估。

    与旧实现的差别在两条，都是修正：

    - 旧实现把「列已是 same」当作闸门通过，于是单文件 + 上报 ``different`` 会
      被**放行**并把 ``different`` 写进列（同一个值两套语义，由文件数决定）；
    - 旧实现允许已确认的报告被二次确认翻转为 ``different``。
    """
    if declared is not None and declared != "same":
        raise SubjectGateError(
            "这批文件不属于同一主体，不能作为一次报告合并解读；请分开上传。"
            if declared == "different"
            else "无法确认这批文件属于同一主体，请分开上传后逐份确认。"
        )
    if current == "same" or declared == "same":
        return "same"
    raise SubjectGateError("请先确认所有文件属于同一主体")


def can_enter_reading(current: str | None) -> bool:
    """这份报告现在能不能进入确认/解读（患者侧「闸门是否开着」的服务端答案）。"""
    return current == "same"


def is_stop_declaration(declared: str | None) -> bool:
    """患者这次的表态是不是「停止」（不合并解读）。"""
    return declared is not None and declared != "same"


def apply_declaration(report: Any, declared: str | None) -> SubjectConsistency:
    """把患者的表态落定到报告上，返回落定值。

    调用方应当先过 :func:`gate`（它会拒掉「停止」的表态）；这里只做写入，
    保持「判定 / 闸门 / 写入」三者各自单一职责。
    """
    settled: SubjectConsistency = "same" if declared == "same" or report.subject_consistency == "same" else "uncertain"
    report.subject_consistency = settled
    return settled
