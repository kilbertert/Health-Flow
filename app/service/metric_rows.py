"""同一观测（GLOSSARY.md 的「同一观测」）。

「这两行是不是同一个观测」此前由两套判定键回答，且对同一份内容给出**不同的
行数**：

- 解析器的**页内去重**（`vision_encoder.parse_text_pdf`）：键是
  `(页号, 指标名, 值, 单位, 参考范围)` —— 没有文件编号、不看原文证据、用原始指标名；
- **报告级去重**（`app/api/report.py`）：键是
  `(文件编号, 页号, 归一化原文/名称, 值, 单位, 参考范围)`。

两套键在三个维度上不同，于是同样的内容在**文本 PDF** 与**扫描件/图片**上得到
不同的行数 —— 而患者看到的《报告历史》的「N 项指标」就是解析出的行数。

本模块是唯一判定：文件编号、页码、值、单位、参考范围全部相等，且按「**原文
证据优先、缺失时回落指标名**」归一化后的字符串相等。
"""

from __future__ import annotations

from typing import Any


def row_identity(metric: Any) -> str:
    """一行的「同一性字符串」：原文证据优先，缺失时回落指标名。

    这条优先级此前埋在 `app/api/report.py` 的一行表达式里，从未被命名。它是
    同一观测判定的核心 —— 把优先级写下来，规则才有可能被讨论和测试。
    """
    source = getattr(metric, "evidence_text", None) or getattr(metric, "metric_name", None) or ""
    return " ".join(str(source).split()).casefold()


def row_key(metric: Any) -> tuple[object, ...]:
    """一行的同一性键。**全仓库唯一** —— 两个执行点都消费它。"""
    return (
        getattr(metric, "source_file_index", 1),
        getattr(metric, "page_number", None),
        row_identity(metric),
        getattr(metric, "metric_value", None),
        getattr(metric, "unit", None),
        getattr(metric, "reference_range", None),
    )


def same_metric_row(a: Any, b: Any) -> bool:
    """两行是不是同一个观测。

    同一份原始文件的同一页上，由同一段原文证据支持的同名、同值、同单位、同参考
    范围的指标行视为同一个观测。**不同文件或不同页上的重复出现是两个观测** ——
    它们各自呈现（文件编号与页码都在键里）。
    """
    return row_key(a) == row_key(b)


def deduplicate(metrics: list[Any]) -> list[Any]:
    """按同一观测去重，保留每组的**第一条**。

    唯一的执行点。此前解析器里还有一份「页内去重」，它对同一份内容给出与这里
    不同的行数（它没有文件编号、不看原文证据），而且只跑在文本 PDF 路径上 ——
    于是行数取决于患者上传的是文本 PDF 还是扫描件/图片。
    """
    seen: set[tuple[object, ...]] = set()
    unique: list[Any] = []
    for metric in metrics:
        key = row_key(metric)
        if key in seen:
            continue
        seen.add(key)
        unique.append(metric)
    return unique
