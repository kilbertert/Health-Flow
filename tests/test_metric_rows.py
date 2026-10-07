"""同一观测（GLOSSARY.md 的「同一观测」）。

收敛前有两套判定键，对同一份内容给出**不同的行数**，而且第 1 套只跑在文本 PDF
路径上 —— 于是行数取决于患者上传的是文本 PDF 还是扫描件/图片，并直接显示在
《报告历史》的「N 项指标」上。

本文件钉住收敛后的唯一判定，重点是**两种输入路径必须同结果**。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.service.metric_rows import deduplicate, row_identity, same_metric_row


def _row(**overrides):
    fields = {
        "source_file_index": 1,
        "page_number": 1,
        "metric_name": "空腹血糖",
        "metric_value": "6.5",
        "unit": "mmol/L",
        "reference_range": "3.9-6.1",
        "evidence_text": "空腹血糖 6.5 mmol/L",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


# ---------------------------------------------------------------------------
# 判定规则
# ---------------------------------------------------------------------------

def test_identical_rows_are_the_same_observation():
    assert same_metric_row(_row(), _row())


def test_different_file_is_a_different_observation():
    """不同文件上的重复出现是两个观测 —— 文件编号在键里。"""
    assert not same_metric_row(_row(), _row(source_file_index=2))


def test_different_page_is_a_different_observation():
    assert not same_metric_row(_row(), _row(page_number=2))


def test_different_value_unit_or_range_is_a_different_observation():
    assert not same_metric_row(_row(), _row(metric_value="6.6"))
    assert not same_metric_row(_row(), _row(unit="mg/dL"))
    assert not same_metric_row(_row(), _row(reference_range="3.9-6.0"))


def test_evidence_text_wins_over_the_metric_name():
    """原文证据优先：证据不同就是两个观测，哪怕指标名相同。

    这是旧的两套键分歧的源头之一 —— 第 1 套键用原始指标名，于是「空腹血糖」与
    「空腹血糖(GLU)」在文本 PDF 上是两个观测、在扫描件上是……另一种答案。
    """
    a = _row(evidence_text="空腹血糖 6.5 mmol/L")
    b = _row(metric_name="空腹血糖(GLU)", evidence_text="空腹血糖(GLU) 6.5 mmol/L")
    assert row_identity(a) != row_identity(b)
    assert not same_metric_row(a, b)


def test_metric_name_is_the_fallback_when_evidence_is_missing():
    a = _row(evidence_text=None)
    b = _row(evidence_text="", metric_name="空腹血糖")
    assert same_metric_row(a, b)


def test_identity_normalises_whitespace_and_case():
    a = _row(evidence_text="空腹血糖  6.5  mmol/L")
    b = _row(evidence_text=" 空腹血糖 6.5 mmol/L ")
    assert same_metric_row(a, b)


# ---------------------------------------------------------------------------
# 两种输入路径同结果（本票的核心断言）
# ---------------------------------------------------------------------------

def test_text_pdf_and_scanned_pdf_agree_on_the_row_count():
    """同一份内容，文本 PDF 与扫描件/图片必须得到**同样的行数**。

    收敛前：解析器的页内去重只跑在 `parse_text_pdf` 上，且它不看原文证据、没有
    文件编号 —— 同一页上「同名同值但证据不同」的两行，在文本 PDF 上被合成一行、
    在扫描件上保留两行。收敛后去重只有一个执行点，两种路径自然同结果。
    """
    rows = [
        _row(evidence_text="空腹血糖 6.5 mmol/L"),
        _row(evidence_text="空腹血糖(GLU) 6.5 mmol/L"),  # 同名同值，证据不同
        _row(evidence_text="空腹血糖(GLU) 6.5 mmol/L"),  # 与上一条完全相同
    ]
    # 唯一的执行点对任何输入路径都给同样的答案。
    assert len(deduplicate(rows)) == 2


def test_deduplicate_keeps_the_first_of_each_group():
    first = _row(evidence_text="空腹血糖 6.5 mmol/L", metric_name="第一条")
    second = _row(evidence_text="空腹血糖 6.5 mmol/L", metric_name="第二条")
    unique = deduplicate([first, second])
    assert unique == [first]
