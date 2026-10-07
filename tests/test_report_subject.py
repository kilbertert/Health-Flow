"""报告主体一致性（GLOSSARY.md 的「报告主体一致性」）。

收敛前由三套通道回答，互不感知，产生四个患者可见矛盾：

1. 「停止」是死路（前端拦下 + 发送体写死 `same`）—— 前端侧，见 #152；
2. **同一个值两套语义**：单文件 + 上报 `different` 被放行并写进列（由文件数决定）；
3. **已确认报告可被二次翻转**为 `different`；
4. **零测试**：全测试套件没有任何一条断言主体闸门。

本文件钉住收敛后的服务端语义，重点是 2 与 3。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.service.report_subject import (
    SubjectGateError,
    apply_declaration,
    can_enter_reading,
    gate,
    initial_consistency,
    is_stop_declaration,
)

# ---------------------------------------------------------------------------
# 初始判定
# ---------------------------------------------------------------------------

def test_single_file_is_same_by_default():
    assert initial_consistency(1) == "same"


@pytest.mark.parametrize("count", [2, 3, 20])
def test_multiple_files_start_uncertain(count):
    """多文件才有「是不是同一个人」的问题，默认不确定。"""
    assert initial_consistency(count) == "uncertain"


# ---------------------------------------------------------------------------
# 闸门语义（三条）
# ---------------------------------------------------------------------------

def test_same_needs_no_declaration():
    """解析后已判为 same（含单文件自动判定）—— 无须表态。"""
    assert gate("same", None) == "same"


def test_uncertain_needs_an_explicit_same():
    assert gate("uncertain", "same") == "same"


def test_uncertain_without_a_declaration_is_blocked():
    with pytest.raises(SubjectGateError):
        gate("uncertain", None)


# ---------------------------------------------------------------------------
# 矛盾 2：同一个值两套语义（由文件数决定）
# ---------------------------------------------------------------------------

def test_single_file_reporting_different_is_a_stop():
    """单文件（列已是 same）+ 上报 `different` —— **不再放行**。

    旧实现的闸门测的是列的当前值：列已是 `same` 时任何表态都通过，于是同一个
    `different` 在多文件上被 422 拦下、在单文件上被写进列并置 confirmed。
    """
    with pytest.raises(SubjectGateError) as excinfo:
        gate("same", "different")
    assert "分开上传" in excinfo.value.detail


def test_uncertain_reporting_different_is_a_stop():
    with pytest.raises(SubjectGateError):
        gate("uncertain", "different")


def test_uncertain_declaration_is_a_stop():
    with pytest.raises(SubjectGateError) as excinfo:
        gate("uncertain", "uncertain")
    assert "分开上传" in excinfo.value.detail


# ---------------------------------------------------------------------------
# 矛盾 3：已确认报告被二次翻转
# ---------------------------------------------------------------------------

def test_a_confirmed_report_cannot_be_flipped_by_a_second_confirmation():
    """列已是 same 的已确认报告，再次确认时携带 `different` —— 同样被拒。

    旧实现里闸门两个条件都不满足（列是 same、表态不是 same），于是**放行**，
    把一份已生成合并解读的报告改写成 `different` 并重新评估。
    """
    with pytest.raises(SubjectGateError):
        gate("same", "different")


def test_gate_is_a_pure_function_of_the_two_inputs():
    """闸门只看「列的当前值」与「这次的表态」，不看文件数 —— 语义不再随路径变形。"""
    assert gate("same", "same") == "same"
    assert gate("uncertain", "same") == "same"
    with pytest.raises(SubjectGateError):
        gate("same", "different")
    with pytest.raises(SubjectGateError):
        gate("uncertain", "different")


# ---------------------------------------------------------------------------
# 写入与派生问题
# ---------------------------------------------------------------------------

def test_apply_declaration_settles_to_same():
    report = SimpleNamespace(subject_consistency="uncertain")
    assert apply_declaration(report, "same") == "same"
    assert report.subject_consistency == "same"


def test_apply_declaration_keeps_an_already_same_report():
    """落定的是**闸门判定出的**值（gate 的返回值），不是患者的原始表态。"""
    report = SimpleNamespace(subject_consistency="same")
    assert apply_declaration(report, "same") == "same"
    assert report.subject_consistency == "same"


def test_can_enter_reading_only_when_same():
    """患者侧「闸门是否开着」的服务端答案。"""
    assert can_enter_reading("same") is True
    assert can_enter_reading("uncertain") is False
    assert can_enter_reading("different") is False


def test_is_stop_declaration():
    assert is_stop_declaration("different") is True
    assert is_stop_declaration("uncertain") is True
    assert is_stop_declaration("same") is False
    assert is_stop_declaration(None) is False
