"""报告原始材料的类型判定（GLOSSARY.md 的「报告原始材料」）。

这些断言打在**唯一的判定入口**上（`app/service/report_material.py`）：受理后缀、
后缀 → MIME、内容嗅探、抽取路由四件事各只有一处实现，任何一处从别处长回来
都会让这里变红。

阴性对照（写这些断言时实际跑过）：把 `resolve` 里的 `sniffed or declared` 改成
`declared or sniffed`，`test_renamed_png_is_judged_by_content` 立刻变红；把
`_get_mime_type` 的默认值事还原到 `vision_encoder`，`test_second_mime_table_is_gone`
变红。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.service.report_material import (
    ACCEPTED_EXTENSIONS,
    KIND_MEDIA_TYPE,
    ReportMaterial,
    declared_kind,
    extraction_route,
    media_type_for_extension,
    mismatch_reason,
    page_count,
    resolve,
    sniff_kind,
)

TINY_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
TINY_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
TINY_GIF = b"GIF89a" + b"\x00" * 24
TINY_BMP = b"BM" + b"\x00" * 24
MINIMAL_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 16


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        (MINIMAL_PDF, "pdf"),
        (TINY_PNG, "png"),
        (TINY_JPEG, "jpeg"),
        (TINY_GIF, "gif"),
        (TINY_BMP, "bmp"),
    ],
)
def test_sniff_recognises_each_accepted_family(content, kind):
    assert sniff_kind(content) == kind


def test_sniff_returns_none_when_it_cannot_tell():
    """嗅不出来是 None，不是「未知类型」——这两件事在调用点含义不同。"""
    assert sniff_kind(b"not a document at all") is None
    assert sniff_kind(b"") is None


def test_renamed_png_is_judged_by_content():
    """把 PNG 改名成 .pdf：按内容判成 png，且标记为声明与内容矛盾。"""
    material = resolve("报告.pdf", TINY_PNG)

    assert material.kind == "png"
    assert material.media_type == "image/png"
    assert material.source == "content"
    assert material.mismatch is True
    assert "扩展名说的是 PDF" in mismatch_reason(material)
    assert "实际是 PNG 图片" in mismatch_reason(material)


def test_matching_extension_and_content_is_not_a_mismatch():
    material = resolve("报告.pdf", MINIMAL_PDF)

    assert material.kind == "pdf"
    assert material.source == "content"
    assert material.mismatch is False


def test_unrecognised_content_falls_back_to_the_declared_extension():
    """嗅不出类型时以患者提交的后缀为准 —— 截断的 PDF 仍然是 PDF，不猜别的。"""
    material = resolve("scan.pdf", b"%PD")

    assert material.kind == "pdf"
    assert material.source == "suffix"
    assert material.mismatch is False


def test_unknown_extension_with_recognisable_content_is_judged_by_content():
    """后缀不认识，但内容是认得的图片：按内容放行。"""
    material = resolve("报告.data", TINY_PNG)

    assert material.kind == "png"
    assert material.source == "content"
    assert material.declared_kind == "unknown"
    # 声明端认不出来时不算矛盾 —— 没有可比较的两种说法。
    assert material.mismatch is False


def test_nothing_recognisable_is_unknown():
    material = resolve("报告.txt", b"hello")

    assert material.kind == "unknown"
    assert material.media_type == "application/octet-stream"
    assert material.source == "none"
    assert extraction_route(material) == "unknown"
    assert material.is_pdf is False


def test_the_four_answers_agree_with_each_other():
    """受理集合、MIME 表、后端/前端共用的扩展名与标签必须是对同一批类型的四种说法。"""
    assert {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp"} == ACCEPTED_EXTENSIONS
    assert set(KIND_MEDIA_TYPE) == {"pdf", "jpeg", "png", "gif", "bmp"}
    for filename, media_type in [
        ("a.pdf", "application/pdf"),
        ("a.jpg", "image/jpeg"),
        ("a.JPEG", "image/jpeg"),
        ("a.png", "image/png"),
        ("a.gif", "image/gif"),
        ("a.bmp", "image/bmp"),
        ("a.txt", "application/octet-stream"),
    ]:
        assert media_type_for_extension(filename) == media_type


def test_extension_helpers_are_case_insensitive():
    assert declared_kind("REPORT.PDF") == "pdf"
    # `.pdf` 是一个**以点开头的名字**，不是「空名 + 后缀」；`Path.suffix` 也这么看。
    assert declared_kind(".pdf") == "unknown"
    assert declared_kind("noextension") == "unknown"


def test_extraction_route_answers_the_family_question():
    """五种受理类型各自路由到 pdf / image，其余一律 unknown。"""
    for filename, content in [
        ("a.pdf", MINIMAL_PDF),
        ("a.png", TINY_PNG),
        ("a.jpg", TINY_JPEG),
        ("a.gif", TINY_GIF),
        ("a.bmp", TINY_BMP),
    ]:
        expected = "pdf" if filename == "a.pdf" else "image"
        assert extraction_route(resolve(filename, content)) == expected, filename
    assert extraction_route(resolve("a.bin", b"\x00\x01\x02")) == "unknown"


def test_no_second_mime_table_survives_in_the_parser():
    """`vision_encoder` 里那张默认落到 image/png 的第二张表必须消失。"""
    from app.service.vision_encoder import VisionEncoderService

    assert not hasattr(VisionEncoderService, "_get_mime_type")


def test_the_parser_no_longer_carries_its_own_suffix_route():
    """`vision_encoder.parse` 不再自带后缀清单 —— 路由由判定模块给出。

    静态守卫，不是行为测试：一个重新长出来的后缀元组在大多数输入上与共享实现
    **给出同样的答案**，行为测试证明不了它。这里直接断言那份字面量不存在。
    """
    source = (Path(__file__).resolve().parents[1] / "app/service/vision_encoder.py").read_text()

    assert '".jpg", ".jpeg", ".png", ".gif", ".bmp"' not in source
    assert 'endswith(".pdf")' not in source


def test_material_is_immutable():
    material = resolve("a.png", TINY_PNG)

    with pytest.raises(dataclasses.FrozenInstanceError):
        material.kind = "pdf"  # type: ignore[misc]

    assert isinstance(material, ReportMaterial)


# --- 页数（#170）-----------------------------------------------------------
#
# 页数是同一个概念的另一半：类型判定回答「是什么」，页数回答「有几页」。
# 下面这些把「未知」与「共 1 页」钉成两个不同的答案。


def test_readable_pdf_reports_its_real_page_count():
    assert page_count("a.pdf", _real_pdf(3)) == 3
    assert page_count("a.pdf", _real_pdf(1)) == 1


def test_damaged_pdf_is_unknown_not_one():
    """损坏的 PDF 此前被说成「共 1 页」。

    阴性对照（写这条时实际跑过）：把 `page_count()` 的 `except` 分支改回
    `return 1`，本用例与请求级的那条一起变红。
    """
    assert page_count("a.pdf", b"%PDF-1.4\n%%EOF\n") is None
    # 连 PDF 头都没有的「.pdf」：后缀说是 PDF，打不开 —— 同样是未知。
    assert page_count("报告.pdf", b"not a pdf at all") is None


def test_images_are_a_known_single_page():
    """图片是**确定**的一页，不是未知 —— 把它也变成 None 会让正常的单页报告失去翻页器。"""
    assert page_count("a.png", TINY_PNG) == 1
    assert page_count("a.jpg", TINY_JPEG) == 1
    assert page_count("a.gif", TINY_GIF) == 1
    assert page_count("a.bmp", TINY_BMP) == 1


def test_unrecognised_material_has_no_guessable_page_count():
    """判定不出类型时不猜页数。"""
    assert page_count("a.bin", b"\x00\x01\x02") is None


def test_the_page_count_decision_lives_in_one_place():
    """页数不再由 `app/api/report.py` 自己算 —— 那里只剩调用。

    静态守卫：一处内联副本（例如「非 PDF 就 return 1」）与原实现给出**同样的
    答案**，行为测试分不出它。这里断言那段被判定的代码不在调用点重新出现。

    注意 `fitz.open` 在 `app/api/report.py` 里**是合法**的 —— 那是「渲染某一页原文」
    的路由，不是页数计算；所以这里盯的是页数计算那段（`max(1, document.page_count)`
    与两个 `return 1` 兜底），不是整个文件的 `fitz` 用量。
    """
    source = (Path(__file__).resolve().parents[1] / "app/api/report.py").read_text()

    assert "document.page_count" not in source
    assert "max(1, document.page_count)" not in source
    # 页数现在只有一条路径：在写入点直接调用原始材料模块。
    assert "page_count=page_count(filename, content)" in source


def _real_pdf(pages: int) -> bytes:
    """一张真的能被 `fitz` 打开的 PDF（测试夹具，不是断言对象）。"""
    import fitz

    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    data = document.tobytes()
    document.close()
    return data
