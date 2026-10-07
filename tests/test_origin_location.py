"""原文定位（GLOSSARY.md 的「原文定位」）。

收敛前坐标合法性有**四种实现、三种裁决**：同一个退化框在抽取层被拒绝、在两个
Pydantic 契约层被接受、在证据桥原样放行。本文件钉住收敛后的规则与**读写分层**。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.service.origin_location import (
    bbox_issue,
    clean_bbox,
    decode_coordinates,
    is_valid_bbox,
    metric_page,
    page_local_source_id,
    page_url,
    source_id_for,
)

DEGENERATE = [100.0, 100.0, 100.0, 100.0]


# ---------------------------------------------------------------------------
# 坐标规则：四种实现收敛为一处
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason", ["length", "not_a_number", "not_finite", "negative", "out_of_range", "not_ordered"])
def test_invalid_coordinates_report_their_reason(reason):
    cases = {
        "length": [1.0, 2.0, 3.0],
        "not_a_number": [1.0, "x", 3.0, 4.0],
        "not_finite": [1.0, float("inf"), 3.0, 4.0],
        "negative": [-1.0, 2.0, 3.0, 4.0],
        "out_of_range": [1.0, 2.0, 1001.0, 4.0],
        "not_ordered": [300.0, 2.0, 100.0, 4.0],
    }
    assert bbox_issue(cases[reason], upper=1000 if reason == "out_of_range" else None, strict=True) == reason


def test_degenerate_box_is_rejected_at_the_creation_boundary():
    """**创建边界从严**：零面积的框指不到任何东西。

    这曾经是三种裁决分歧的核心：抽取层拒绝它、两个契约层接受它、证据桥原样放行。
    """
    assert bbox_issue(DEGENERATE, strict=True) == "degenerate"
    assert clean_bbox(DEGENERATE, strict=True) is None


def test_degenerate_box_is_tolerated_on_the_read_boundary():
    """**读取边界容忍**：库里可能存着历史行，读取时从严会让它们打不开。

    容忍不等于认可 —— 它只保证已经存下来的形状不会让报告打不开。
    """
    assert bbox_issue(DEGENERATE, strict=False) is None
    assert clean_bbox(DEGENERATE, strict=False) == DEGENERATE


def test_valid_box_passes_both_boundaries():
    box = [120.0, 340.0, 280.0, 360.0]
    assert is_valid_bbox(box, strict=True)
    assert clean_bbox(box, strict=True) == box
    assert clean_bbox(box, strict=False) == box


def test_decode_coordinates_accepts_both_forms():
    """坐标列的 JSON 字符串/列表双形态解码 —— 唯一一处。"""
    assert decode_coordinates("[1, 2, 3, 4]") == [1, 2, 3, 4]
    assert decode_coordinates([1, 2, 3, 4]) == [1, 2, 3, 4]
    assert decode_coordinates("not json") is None
    assert decode_coordinates(None) is None


# ---------------------------------------------------------------------------
# source_id 与定位 URL 各只有一处
# ---------------------------------------------------------------------------

def test_source_id_format_is_defined_once():
    assert page_local_source_id(3, 1) == "p3-m1"
    assert source_id_for(2, 3, 1) == "file-2/p3-m1"
    # 页码缺失时取第 1 页（抽取器对无页码的行的既有行为）。
    assert page_local_source_id(None, 4) == "p1-m4"


def test_page_url_guards_both_identifiers():
    """守卫统一：`report_id` 与 `page_number` 都在才拼 URL。

    此前两处守卫不同，其中一处只守卫 `report_id` —— `page_number` 为空时会拼出
    一个含 `None` 的地址，靠前置流程巧合保证不可达。
    """
    assert page_url(7, 1, 2) == "/api/health/report/7/files/1/pages/2"
    assert page_url(7, 1, None) is None
    assert page_url(None, 1, 2) is None


def test_metric_page_reads_both_vocabularies():
    from types import SimpleNamespace

    assert metric_page(SimpleNamespace(page_number=2)) == 2
    assert metric_page(SimpleNamespace(source_page=3)) == 3
    assert metric_page(SimpleNamespace()) is None


# ---------------------------------------------------------------------------
# 四层裁决一致（本票的核心断言）
# ---------------------------------------------------------------------------

def test_all_four_layers_now_agree_on_a_degenerate_box():
    """同一个退化框，四层必须给出**同一个**裁决。

    收敛前：抽取层拒绝、两个契约层接受、证据桥原样放行 —— 「这是一个合法位置
    坐标吗」有四种实现、三种答案。收敛后四层都引用同一处判定。
    """
    from app.data.models import MetricRecord as MetricModel
    from app.schema.evidence import SourceObservation
    from app.schema.report import MetricRecord
    from app.service.evidence_bridge import build_observations_with_unmatched
    from app.service.vision_encoder import VisionEncoderService

    # 1) 抽取入口：拒绝
    assert VisionEncoderService._clean_bbox(DEGENERATE) is None
    # 2) MetricRecord 契约：拒绝（创建边界）
    with pytest.raises(ValidationError):
        MetricRecord(metric_name="空腹血糖", metric_value="5.2", bbox=DEGENERATE)
    # 3) SourceObservation 契约：拒绝（创建边界）
    with pytest.raises(ValidationError):
        SourceObservation(
            observation_id="obs-1",
            value=5.2,
            unit="mmol/L",
            evidence_text="空腹血糖 5.2 mmol/L",
            source_file_index=1,
            source_page=1,
            bbox=DEGENERATE,
        )
    # 4) 证据边界：读取容忍（历史行可能存着退化框），但**不产生**新的
    metric = MetricModel(
        id=1, report_id=1, metric_name="空腹血糖", metric_value="6.8", unit="mmol/L",
        reference_range="3.9-6.1", page_number=1, evidence_text="空腹血糖 6.8 mmol/L (3.9-6.1)",
        source_file_index=1, confirmation_status="confirmed", metric_code="fasting_glucose",
        bbox=DEGENERATE,
    )
    observations, _, _ = build_observations_with_unmatched([metric])
    assert observations and observations[0]["bbox"] == DEGENERATE


# ---------------------------------------------------------------------------
# 单一定义的守卫
# ---------------------------------------------------------------------------

def test_the_source_id_format_is_defined_in_exactly_one_place():
    """`file-{N}/p{page}-m{position}` 的格式只在 origin_location 里拼。

    行为测试证明不了这一条：内联拼出同样字符串的副本**输出一致**，只有格式演进
    时才会漂移。所以这里直接守定义位置（同本仓既有的 `_ABNORMAL_FLAGS` 守卫）。
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    # 只认**代码**里的拼接，不认注释/文档字符串里的格式说明。
    # 只认**代码**里的拼接，不认注释/文档字符串里的格式说明。
    # 覆盖两种历史写法：f-string 生成页内形态、`'p' + str(page)` 内联拼兜底。
    pattern = re.compile(r"""(f"[^"]*p\{page|["']p["']\s*\+\s*str\()""")
    offenders = []
    for root in ("app", "scripts"):
        for path in (repo / root).rglob("*.py"):
            if path.name == "origin_location.py":
                continue
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(repo)}:{line_number}")
    assert offenders == [], f"source_id 格式在 origin_location 之外还有实现: {offenders}"


def test_the_page_url_template_is_defined_in_exactly_one_place():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    offenders = []
    for root in ("app", "scripts"):
        for path in (repo / root).rglob("*.py"):
            if path.name == "origin_location.py":
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "/pages/" in line and "f\"" in line:
                    offenders.append(f"{path.relative_to(repo)}:{line_number}")
    assert offenders == [], f"定位 URL 在 origin_location 之外还有构造: {offenders}"


# ---------------------------------------------------------------------------
# 复审发现的三条（#164）
# ---------------------------------------------------------------------------

def test_stored_degenerate_box_does_not_break_the_report_response():
    """历史行里的退化框读出来是**定位为空**，不是让整份报告打不开。

    收敛后契约的创建边界拒绝退化框 —— 但库里已经存着的那一行不会自己改变。
    走**整条响应构造**（`_metric_response`）才证明得了这一点：单测那个小工具
    证明不了它被接上了（第一版就是这么写的，负控没变红）。
    """
    from app.api.report import _metric_response
    from app.data.models import MetricRecord as MetricModel

    stored = MetricModel(
        id=1, report_id=1, metric_name="空腹血糖", metric_value="5.2", unit="mmol/L",
        reference_range="3.9-6.1", page_number=1,
        bbox="[100, 100, 100, 100]",  # 库里就是这么存的
        bbox_normalized="[100, 100, 100, 100]",
    )
    response = _metric_response(stored)
    assert response.bbox is None, "退化框读出来应当是「定位为空」"
    assert response.bbox_normalized is None

    good = MetricModel(
        id=2, report_id=1, metric_name="血糖", metric_value="5.2", unit="mmol/L",
        reference_range="3.9-6.1", page_number=1, bbox="[120, 340, 280, 360]",
    )
    assert _metric_response(good).bbox == [120.0, 340.0, 280.0, 360.0]


def test_provider_source_id_is_preserved():
    """抽取器给了自定义 `source_id` 就保留它，只补文件前缀。"""
    from app.api.report import _final_source_id

    assert _final_source_id("p3-m1", 2, 3, 1) == "file-2/p3-m1"
    assert _final_source_id("provider-abc", 2, 3, 1) == "file-2/provider-abc"
    assert _final_source_id("file-2/p3-m1", 2, 3, 1) == "file-2/p3-m1"
    # 没有自定义标识时才生成。
    assert _final_source_id(None, 2, 3, 1) == "file-2/p3-m1"


def test_denormalize_keeps_a_narrow_box_alive():
    """极窄的框在反归一化后**不能**塌成零面积。

    宽度 1px 的竖线在 `round(..., 2)` 之后两个端点可能并到一起 —— 那会产出退化
    框、被创建边界拒绝，整张图抽不出指标（评审在 #164 指出）。
    """
    from app.service.origin_location import denormalize_bbox

    # 归一化差 0.01（在 100px 宽的图上不到 0.01px）—— 舍入后两端点相等。
    narrow = [500.0, 100.0, 500.01, 200.0]
    box = denormalize_bbox(narrow, 100, 100)
    assert box[0] != box[2], f"端点被舍入并到一起: {box}"


def test_boundary_validator_rejects_reversed_and_negative():
    """证据边界不再放行逆序与负数坐标。

    此前那一层只查长度与有限性 —— 是「同一概念四种判定」里的第四种。
    """
    from app.service.evidence_bridge import _coordinates_for_boundary

    assert _coordinates_for_boundary([300.0, 2.0, 100.0, 4.0]) is None  # 逆序
    assert _coordinates_for_boundary([-1.0, 2.0, 3.0, 4.0]) is None  # 负数
    assert _coordinates_for_boundary([100.0, 100.0, 100.0, 100.0]) == [100.0, 100.0, 100.0, 100.0]  # 读取容忍
