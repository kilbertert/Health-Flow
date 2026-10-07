"""原文定位（GLOSSARY.md 的「原文定位」）。

「这条指标引用了原始报告的哪个位置」此前由**四层平行实现**回答，各层互不同步。
坐标合法性一层就有四种实现、三种裁决：同一个退化框 `[100,100,100,100]` 在抽取层
被拒绝、在两个 Pydantic 契约层被接受、在证据桥原样放行。

**读写分层**（这是兼容性要求，不是残留的双规则）：

- **创建边界从严**：抽取、契约写入、证据边界构造 —— 拒绝退化框，因为一个退化框
  指不到任何东西；
- **契约读取容忍**：库里可能存着历史行，读取时从严会让它们打不开。容忍只针对
  「已经存下来的形状」，不产生新的退化框。
"""

from __future__ import annotations

import json
import math
from typing import Any, Literal

BBoxIssue = Literal["length", "not_a_number", "not_finite", "negative", "out_of_range", "not_ordered", "degenerate"]


def decode_coordinates(value: object) -> list[float] | None:
    """坐标列的 JSON 字符串/列表双形态解码 —— **唯一**一处。

    此前 `app/api/report.py` 的 `load_json` 与 `evidence_bridge.py` 的
    `_coordinate_list` 各写一份。
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)):
        return list(value)
    return None


def bbox_issue(value: object, *, upper: float | None = None, strict: bool) -> BBoxIssue | None:
    """这份坐标有什么问题？没有则返回 ``None``。

    ``strict=True`` 是**创建边界**：拒绝退化框（`x2 <= x1` 或 `y2 <= y1`）——
    零面积的框指不到任何东西。``strict=False`` 是**读取边界**：只要求有序
    （`x1 <= x2`），容忍历史行里已经存下来的退化框。
    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return "length"
    try:
        coordinates = [float(item) for item in value]
    except (TypeError, ValueError):
        return "not_a_number"
    if any(not math.isfinite(item) for item in coordinates):
        return "not_finite"
    if any(item < 0 for item in coordinates):
        return "negative"
    if upper is not None and any(item > upper for item in coordinates):
        return "out_of_range"
    x1, y1, x2, y2 = coordinates
    if x2 < x1 or y2 < y1:
        return "not_ordered"
    if strict and (x2 == x1 or y2 == y1):
        return "degenerate"
    return None


def is_valid_bbox(value: object, *, upper: float | None = None, strict: bool = False) -> bool:
    return bbox_issue(value, upper=upper, strict=strict) is None


def clean_bbox(value: object, *, upper: float | None = None, strict: bool = False) -> list[float] | None:
    """做成可用的坐标列表，不合法则 ``None``（定位为空 —— **不猜测**）。"""
    decoded = decode_coordinates(value)
    if not is_valid_bbox(decoded, upper=upper, strict=strict):
        return None
    return [float(item) for item in decoded]  # type: ignore[union-attr]


def page_local_source_id(page_number: int | None, position: int) -> str:
    """页内形态 `p{page}-m{position}`（抽取器只知道自己处理的那一页）。"""
    return f"p{page_number or 1}-m{position}"


def source_id_for(file_index: int, page_number: int | None, position: int) -> str:
    """`source_id` 的最终形态 —— **全仓库唯一**一处格式定义。

    形态是 `file-{N}/p{page}-m{position}`：文件编号 + 页码 + 页内第几条。
    此前这个格式写在三处（抽取器生成、`report.py` 补前缀时内联重写兜底、
    `scripts/e2e_seed.py` 手抄），格式演进时三处必然漂移。
    """
    return f"file-{file_index}/{page_local_source_id(page_number, position)}"


def page_url(report_id: int, file_index: int, page_number: int | None) -> str | None:
    """定位页 URL —— **唯一**一处构造。

    守卫统一为「`report_id` 与 `page_number` 都在才拼 URL」：`page_number` 为空
    时返回 ``None``，而不是拼出一个含 `None` 的地址（此前两处守卫不同，其中一处
    只守卫 `report_id`，今天靠前置流程巧合保证不可达）。
    """
    if report_id is None or page_number is None:
        return None
    return f"/api/health/report/{report_id}/files/{file_index}/pages/{page_number}"


def normalize_bbox(bbox: list[float], width: int, height: int) -> list[float]:
    """像素坐标 → `[0, 1000]` 的归一化坐标。"""
    if width <= 0 or height <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    x1, y1, x2, y2 = bbox
    return [
        round(max(0.0, min(1000.0, x1 / width * 1000)), 2),
        round(max(0.0, min(1000.0, y1 / height * 1000)), 2),
        round(max(0.0, min(1000.0, x2 / width * 1000)), 2),
        round(max(0.0, min(1000.0, y2 / height * 1000)), 2),
    ]


def denormalize_bbox(bbox: list[float], width: int, height: int) -> list[float]:
    """归一化坐标 → 像素坐标。"""
    if width <= 0 or height <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    x1, y1, x2, y2 = bbox
    return [
        round(x1 / 1000 * width, 2),
        round(y1 / 1000 * height, 2),
        round(x2 / 1000 * width, 2),
        round(y2 / 1000 * height, 2),
    ]


def metric_page(metric: Any) -> int | None:
    """一条指标/来源观测指向的页码 —— 两种词汇（`page_number` / `source_page`）
    的转换只写在这里。

    前端此前也有一份同样的双读兜底（`page_number || source_page || 1`），
    #163 会让前端改用同一个词汇。服务端这一份是唯一的。
    """
    return getattr(metric, "page_number", None) or getattr(metric, "source_page", None)
