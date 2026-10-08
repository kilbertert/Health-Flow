"""Tests for VisionEncoder Service."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class MockVLMClient:
    """Mock VLM client."""

    def chat_with_image(self, messages, **kwargs):
        return '{"text_summary": "空腹血糖6.5mmol/L", "metrics": [{"metric_name": "空腹血糖", "metric_value": "6.5", "unit": "mmol/L", "reference_range": "3.9-6.1"}]}'


class MockLLMClient:
    """Mock LLM client."""

    def chat_with_json(self, messages, **kwargs):
        return {
            "metrics": [
                {
                    "metric_name": "空腹血糖",
                    "metric_value": "6.5",
                    "unit": "mmol/L",
                    "reference_range": "3.9-6.1",
                }
            ]
        }


@pytest.fixture
def mock_deps():
    """Mock dependencies."""
    with (
        patch("app.service.vision_encoder.get_vlm_client", return_value=MockVLMClient()),
        patch("app.service.vision_encoder.get_llm_client", return_value=MockLLMClient()),
    ):
        yield


def test_vision_encoder_init():
    """Test VisionEncoderService initialization."""
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    assert service is not None


def test_parse_image_report(mock_deps):
    """Test parsing image report."""
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()

    # Create a small PNG image (1x1 pixel)
    img_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"

    result = service.parse_image_report(img_bytes, "image/png")

    assert result.report_type == "image"
    assert result.success is True
    assert result.page_count == 1


def test_parse_unsupported_file(mock_deps):
    """Test parsing unsupported file type."""
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    result = service.parse(b"some content", "unknown.xyz")

    assert result.report_type == "unknown"
    assert result.success is False
    assert "不支持" in result.error


#: 后缀 → MIME 的断言已随第二张表一起搬到 `tests/test_report_material.py`
#: （那里是唯一的判定入口）。这里保留的是**抽取器真的消费了它**：
#: 一张认得出内容的图片，其 MIME 由内容决定，不由文件名决定。


def test_parse_routes_a_renamed_image_by_content(mock_deps):
    """PNG 改名成 .pdf：抽取器按内容走图片分支，不按文件名走 PDF 分支。"""
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    img_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"

    result = service.parse(img_bytes, "报告.pdf")

    assert result.report_type == "image"


def test_parse_unknown_extension_uses_the_content_mime_type(mock_deps):
    """后缀不认识但内容是 JPEG：交给 VLM 的是 image/jpeg，不是默认的 image/png。"""
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    service._parse_image_with_vlm = MagicMock(return_value=("", [], None))
    jpeg_data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"

    service.parse(jpeg_data, "报告.data")

    assert service._parse_image_with_vlm.call_args.args[1] == "image/jpeg"


def test_parsed_report_dataclass():
    """Test ParsedReport dataclass."""
    from app.service.vision_encoder import ParsedReport

    report = ParsedReport(
        report_type="text_pdf",
        raw_text="Test content",
        metrics=[],
        page_count=1,
        success=True,
    )

    assert report.report_type == "text_pdf"
    assert report.raw_text == "Test content"
    assert report.success is True
    assert report.error is None


def test_metric_payload_uses_reference_range_over_provider_star(mock_deps):
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    metric = service._metric_from_payload(
        {
            "metric_name": "LDL-C",
            "metric_value": "3.63",
            "unit": "mmol/L",
            "reference_range": "<2.60",
            "abnormal_flag": "*",
        },
        page_number=1,
        width=None,
        height=None,
        index=1,
    )

    assert metric is not None
    assert metric.abnormal_flag == "H"


def test_empty_image_metrics_are_not_reported_as_success(mock_deps):
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    service._vlm_client = SimpleNamespace(
        chat_with_image=lambda messages, **kwargs: '{"text_summary":"仅标题","metrics":[]}',
        last_run_id="run-empty",
        use_responses_api=False,
    )

    result = service.parse_image_report(b"not-an-image", "report.png")

    assert result.success is False
    assert result.metrics == []
    assert "未提取到" in result.error


def test_zero_or_invalid_bbox_is_not_presented_as_evidence(mock_deps):
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    metric = service._metric_from_payload(
        {
            "metric_name": "空腹血糖",
            "metric_value": "6.5",
            "unit": "mmol/L",
            "reference_range": "3.9-6.1",
            "bbox": [0, 0, 0, 0],
            "bbox_normalized": [0, 0, 1001, 1001],
        },
        page_number=1,
        width=1000,
        height=1000,
        index=1,
    )

    assert metric is not None
    assert metric.bbox is None
    assert metric.bbox_normalized is None


def test_parse_with_filename_jpg(mock_deps):
    """Test parsing JPEG file."""
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()

    # Minimal JPEG data
    jpeg_data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\x27 ,#\x1c\x1c(7teleservices5!=17==11teleservices1x;x8teleservices\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x01\x01\x01\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5v\xff\xd9"

    result = service.parse(jpeg_data, "test.jpg")

    # Should attempt to parse as image
    assert result.report_type == "image"


def test_text_metric_keeps_source_page(mock_deps):
    """Text-PDF extraction must retain the page sent to the LLM."""
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    metrics = service._extract_metrics_from_text("空腹血糖 6.5", page_number=3)

    assert len(metrics) == 1
    assert metrics[0].page_number == 3


def test_text_metric_provider_error_is_not_hidden():
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()
    service._llm_client = MagicMock()
    service._llm_client.chat_with_json.side_effect = RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        service._extract_metrics_from_text("空腹血糖 6.5", page_number=1)


def test_text_pdf_keeps_successful_pages_when_one_provider_call_fails(monkeypatch):
    from app.schema.report import MetricRecord
    from app.service.vision_encoder import VisionEncoderService

    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

        def extract_tables(self):
            return []

    class Document:
        def __init__(self):
            self.pages = [Page("page one"), Page("page two")]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=lambda _: Document()))
    service = VisionEncoderService()

    def extract(page):
        page_number, _ = page
        if page_number == 2:
            raise RuntimeError("page provider timeout")
        return [
            MetricRecord(
                metric_name="空腹血糖",
                metric_value="6.5",
                unit="mmol/L",
                reference_range="3.9-6.1",
                page_number=page_number,
                evidence_text="空腹血糖 6.5 mmol/L 3.9-6.1",
            )
        ], "provider-1"

    service._extract_text_page = extract
    result = service.parse_text_pdf(b"pdf")

    assert result.success is True
    assert [metric.page_number for metric in result.metrics] == [1]
    assert result.error == "page 2: page provider timeout"


def test_render_pdf_to_images_fallback():
    """Test PDF rendering fallback when pymupdf not available."""
    from app.service.vision_encoder import VisionEncoderService

    service = VisionEncoderService()

    # With no pymupdf, should return empty list
    result = service._render_pdf_to_images(b"fake pdf content")
    assert isinstance(result, list)


def test_text_pdf_no_longer_deduplicates(monkeypatch):
    """文本 PDF 路径**不再**自己做页内去重（#107）。

    同一观测的判定只有一个执行点（报告级，`app/service/metric_rows.py`）。解析器
    里那份页内去重没有文件编号、不看原文证据，而且只跑在这一条路径上 —— 于是
    同样的内容在文本 PDF 与扫描件上得到不同的行数。这里断言解析器**原样返回**
    它抽到的每一行，去重留给报告级。

    连带修正：`success` 按未去重的行数计算，所以「两行同名同值」不再让
    `success` 因为去重后为空而翻转。
    """
    from app.schema.report import MetricRecord
    from app.service.vision_encoder import VisionEncoderService

    class Page:
        def extract_text(self):
            return "page one"

        def extract_tables(self):
            return []

    class Document:
        def __init__(self):
            self.pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=lambda _: Document()))
    service = VisionEncoderService()

    duplicate_rows = [
        MetricRecord(
            metric_name="空腹血糖",
            metric_value="6.5",
            unit="mmol/L",
            reference_range="3.9-6.1",
            page_number=1,
            evidence_text="空腹血糖 6.5 mmol/L 3.9-6.1",
        ),
        MetricRecord(
            metric_name="空腹血糖",
            metric_value="6.5",
            unit="mmol/L",
            reference_range="3.9-6.1",
            page_number=1,
            evidence_text="空腹血糖 6.5 mmol/L 3.9-6.1",
        ),
    ]
    service._extract_text_page = lambda page: (list(duplicate_rows), "")

    parsed = service.parse_text_pdf(b"pdf")
    assert len(parsed.metrics) == 2, "解析器不再去重 —— 那是报告级的职责"
    assert parsed.success is True
