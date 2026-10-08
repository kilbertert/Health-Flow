"""PDF/image parsing with coordinate-aware metric extraction."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

from app.config import get_settings
from app.model.llm import get_llm_client, get_vlm_client
from app.schema.report import MetricRecord
from app.service.evidence_bridge import infer_abnormal_flag
from app.service.origin_location import (
    clean_bbox,
    denormalize_bbox,
    normalize_bbox,
    page_local_source_id,
)
from app.service.report_material import (
    RENDERED_PAGE_KIND,
    RENDERED_PAGE_MEDIA_TYPE,
    extraction_route,
)
from app.service.report_material import (
    resolve as resolve_material,
)


@dataclass
class ParsedReport:
    report_type: str
    raw_text: str
    metrics: list[MetricRecord]
    page_count: int
    success: bool
    error: str | None = None
    provider: str = ""
    model: str = ""
    run_id: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    provider_run_id: str = ""
    provider_run_ids: tuple[str, ...] = ()


EXTRACTION_PROMPT_VERSION = "health-flow-report-extraction-v2"
IMAGE_EXTRACTION_PROMPT = """
逐行转录页面中有名称和数值的观测项，输出严格 JSON，不要输出 Markdown，不解释或推断。
每个 metric 必须包含 metric_name、metric_value；如果能定位，请返回页面像素坐标 bbox
[x1,y1,x2,y2]、归一化坐标 bbox_normalized [0,0,1000,1000]、evidence_text。
JSON 格式：
{"text_summary":"页面摘要","metrics":[
 {"metric_name":"空腹血糖","metric_value":"6.5","unit":"mmol/L",
  "reference_range":"3.9-6.1","abnormal_flag":"H","bbox":[0,0,0,0],
  "bbox_normalized":[0,0,0,0],"evidence_text":"原文片段"}
]}
无法确认的坐标返回 null，禁止猜测坐标。
""".strip()
TEXT_EXTRACTION_PROMPT = (
    "从以下文本逐行转录有名称和数值的观测项，只输出 JSON，不解释或推断："
    '{"metrics":[{"metric_name":"","metric_value":"","unit":"",'
    '"reference_range":"","abnormal_flag":"","evidence_text":""}]}'
)
PROMPT_HASH = hashlib.sha256(
    f"{EXTRACTION_PROMPT_VERSION}\n{IMAGE_EXTRACTION_PROMPT}\n{TEXT_EXTRACTION_PROMPT}".encode()
).hexdigest()


class VisionEncoderService:
    """Route text PDFs, scanned PDFs and images through the suitable parser."""

    def __init__(self) -> None:
        self._vlm_client = None
        self._llm_client = None

    @property
    def vlm_client(self):
        if self._vlm_client is None:
            self._vlm_client = get_vlm_client()
        return self._vlm_client

    @property
    def llm_client(self):
        if self._llm_client is None:
            self._llm_client = get_llm_client()
        return self._llm_client

    def detect_pdf_type(self, pdf_bytes: bytes) -> tuple[str, int]:
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                page_count = len(pdf.pages)
                text_pages = sum(1 for page in pdf.pages if (page.extract_text() or "").strip())
                return ("text_pdf" if text_pages >= 1 else "scanned_pdf", page_count)
        except ImportError:
            return "scanned_pdf", 0
        except Exception:
            return "unknown", 0

    def parse_text_pdf(self, pdf_bytes: bytes) -> ParsedReport:
        try:
            import pdfplumber

            pages: list[str] = []
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                page_count = len(pdf.pages)
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    tables = page.extract_tables() or []
                    rows = [" | ".join(str(cell or "") for cell in row) for table in tables for row in table if row]
                    pages.append(text if text.strip() else "\n".join(rows))

            raw_text = "\n\n".join(page for page in pages if page)
            indexed_pages = [
                (page_number, page_text) for page_number, page_text in enumerate(pages, start=1) if page_text.strip()
            ]
            workers = max(1, min(get_settings().REPORT_PARSE_WORKERS, len(indexed_pages)))
            page_results: list[tuple[list[MetricRecord], str]] = []
            errors: list[str] = []
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    (
                        page_number,
                        executor.submit(self._extract_text_page, (page_number, page_text)),
                    )
                    for page_number, page_text in indexed_pages
                ]
                for page_number, future in futures:
                    try:
                        page_results.append(future.result())
                    except Exception as exc:
                        errors.append(f"page {page_number}: {exc}")
            metrics = [metric for group, _ in page_results for metric in group]
            provider_run_ids = tuple(run_id for _, run_id in page_results if run_id)
            # 去重不在这里做：同一观测的判定只有一个执行点（报告级，
            # app/service/metric_rows.py），否则同一份内容在文本 PDF 与扫描件上
            # 会得到不同的行数。success 按**未去重**的 metrics 计算。
            parsed = self._with_trace(
                ParsedReport(
                    report_type="text_pdf",
                    raw_text=raw_text,
                    metrics=metrics,
                    page_count=page_count,
                    success=bool(metrics),
                    error=("; ".join(errors) if errors else None if metrics else "未提取到可确认的医学指标"),
                ),
                self.llm_client,
            )
            return replace(
                parsed,
                provider_run_id=provider_run_ids[0] if provider_run_ids else "",
                provider_run_ids=provider_run_ids,
            )
        except Exception as exc:
            return self._with_trace(ParsedReport("text_pdf", "", [], 0, False, str(exc)), self.llm_client)

    def parse_scanned_pdf(self, pdf_bytes: bytes) -> ParsedReport:
        images = self._render_pdf_to_images(pdf_bytes)
        if not images:
            return self._with_trace(
                ParsedReport(
                    "scanned_pdf",
                    "",
                    [],
                    0,
                    False,
                    "无法渲染 PDF 页面，请安装 PyMuPDF 和 Pillow",
                ),
                self.vlm_client,
            )

        metrics: list[MetricRecord] = []
        texts: list[str] = []
        errors: list[str] = []
        provider_run_ids: list[str] = []
        for page_number, image_bytes in enumerate(images, start=1):
            parsed = self._parse_image_with_vlm(image_bytes, RENDERED_PAGE_MEDIA_TYPE, page_number)
            texts.append(parsed[0])
            metrics.extend(parsed[1])
            run_id = str(getattr(self.vlm_client, "last_run_id", "") or "")
            if run_id:
                provider_run_ids.append(run_id)
            if parsed[2]:
                errors.append(f"page {page_number}: {parsed[2]}")

        parsed = self._with_trace(
            ParsedReport(
                report_type="scanned_pdf",
                raw_text="\n\n".join(texts),
                metrics=metrics,
                page_count=len(images),
                success=bool(metrics),
                error=("; ".join(errors) if errors else None if metrics else "未提取到可确认的医学指标"),
            ),
            self.vlm_client,
        )
        return replace(
            parsed,
            provider_run_id=provider_run_ids[0] if provider_run_ids else "",
            provider_run_ids=tuple(provider_run_ids),
        )

    def parse_image_report(self, image_bytes: bytes, mime_type: str = "image/png") -> ParsedReport:
        text, metrics, error = self._parse_image_with_vlm(image_bytes, mime_type, 1)
        parsed = self._with_trace(
            ParsedReport(
                "image",
                text,
                metrics,
                1,
                error is None and bool(metrics),
                error or (None if metrics else "未提取到可确认的医学指标"),
            ),
            self.vlm_client,
        )
        return replace(
            parsed,
            provider_run_id=str(getattr(self.vlm_client, "last_run_id", "") or ""),
            provider_run_ids=(str(getattr(self.vlm_client, "last_run_id", "") or ""),)
            if getattr(self.vlm_client, "last_run_id", "")
            else (),
        )

    @staticmethod
    def _with_trace(parsed: ParsedReport, client: Any) -> ParsedReport:
        settings = get_settings()
        provider = (
            "openai-compatible-responses" if getattr(client, "use_responses_api", False) else "openai-compatible-chat"
        )
        return replace(
            parsed,
            provider=provider,
            model=settings.VLLM_MODEL,
            run_id=str(uuid.uuid4()),
            prompt_version=EXTRACTION_PROMPT_VERSION,
            prompt_hash=PROMPT_HASH,
            provider_run_id=str(getattr(client, "last_run_id", "") or ""),
        )

    def parse(self, content: bytes, filename: str) -> ParsedReport:
        """按**原始材料模块给出的判定**路由，不再按文件名自己猜。

        类型判定（后缀 / MIME / 内容嗅探）唯一权威在 `app/service/report_material.py`；
        这里只消费它，不再保有自己的后缀清单或 MIME 表。
        """
        material = resolve_material(filename, content)
        route = extraction_route(material)
        if route == "pdf":
            pdf_type, _ = self.detect_pdf_type(content)
            return self.parse_text_pdf(content) if pdf_type == "text_pdf" else self.parse_scanned_pdf(content)
        if route == "image":
            return self.parse_image_report(content, material.media_type)
        return ParsedReport("unknown", "", [], 0, False, f"不支持的文件类型：{filename}")

    def _parse_image_with_vlm(
        self, image_bytes: bytes, mime_type: str, page_number: int
    ) -> tuple[str, list[MetricRecord], str | None]:
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        width, height = self._image_size(image_bytes)
        prompt = IMAGE_EXTRACTION_PROMPT
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        try:
            response = self.vlm_client.chat_with_image(messages, temperature=0)
            payload = self._parse_json_response(response)
            raw_metrics = payload.get("metrics", [])
            if not isinstance(raw_metrics, list):
                raise TypeError("VLM metrics 不是数组")
            metrics = [
                self._metric_from_payload(item, page_number, width, height, index)
                for index, item in enumerate(raw_metrics, start=1)
            ]
            metrics = [item for item in metrics if item]
            return (
                str(payload.get("text_summary", "")),
                metrics,
                None if metrics else "未提取到可确认的医学指标",
            )
        except Exception as exc:
            return "", [], str(exc)

    def _metric_from_payload(
        self,
        data: dict[str, Any],
        page_number: int,
        width: int | None,
        height: int | None,
        index: int,
    ) -> MetricRecord | None:
        if not isinstance(data, dict):
            return None
        name = str(data.get("metric_name", "")).strip()
        value = str(data.get("metric_value", "")).strip()
        if not name or not value:
            return None

        bbox = self._clean_bbox(data.get("bbox"))
        normalized = self._clean_bbox(data.get("bbox_normalized"), upper=1000)
        if normalized is None and bbox and width and height:
            normalized = self.normalize_bbox(bbox, width, height)
        if bbox is None and normalized and width and height:
            bbox = self.denormalize_bbox(normalized, width, height)

        reference_range = data.get("reference_range")
        reference_range = str(reference_range).strip() if reference_range else None
        raw_flag = str(data.get("abnormal_flag") or "").strip()
        abnormal_flag = infer_abnormal_flag(value, reference_range)
        if abnormal_flag is None:
            abnormal_flag = "A" if raw_flag == "*" else raw_flag or None

        evidence_text = data.get("evidence_text")
        evidence_text = str(evidence_text).strip() if evidence_text else None
        unit = data.get("unit")
        unit = str(unit).strip() if unit else None

        return MetricRecord(
            metric_name=name,
            metric_value=value,
            unit=unit,
            reference_range=reference_range,
            trend=data.get("trend"),
            abnormal_flag=abnormal_flag,
            bbox=bbox,
            bbox_normalized=normalized,
            page_number=page_number,
            evidence_text=evidence_text,
            source_id=str(data.get("source_id") or page_local_source_id(page_number, index)),
        )

    @staticmethod
    def _clean_bbox(value: Any, *, upper: float | None = None) -> list[float] | None:
        """抽取入口的坐标清洗。规则在 `app/service/origin_location.py`（唯一一处）——
        **创建边界从严**：退化框指不到任何东西，拒绝。"""
        return clean_bbox(value, upper=upper, strict=True)

    @staticmethod
    def normalize_bbox(bbox: list[float], width: int, height: int) -> list[float]:
        return normalize_bbox(bbox, width, height)

    @staticmethod
    def denormalize_bbox(bbox: list[float], width: int, height: int) -> list[float]:
        return denormalize_bbox(bbox, width, height)

    @staticmethod
    def _parse_json_response(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            return response
        text = str(response or "").strip().replace("```json", "").replace("```", "")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("VLM 未返回 JSON")
        payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("VLM JSON 顶层不是对象")
        return payload

    @staticmethod
    def _image_size(image_bytes: bytes) -> tuple[int | None, int | None]:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as image:
                return image.width, image.height
        except Exception:
            return None, None

    def _render_pdf_to_images(self, pdf_bytes: bytes, dpi: int = 144) -> list[bytes]:
        try:
            import fitz

            document = fitz.open(stream=pdf_bytes, filetype="pdf")
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            images = [page.get_pixmap(matrix=matrix).tobytes(RENDERED_PAGE_KIND) for page in document]
            document.close()
            return images
        except Exception:
            return []

    def _extract_metrics_from_text(self, text: str, page_number: int = 1) -> list[MetricRecord]:
        metrics, _ = self._extract_text_page((page_number, text))
        return metrics

    def _extract_text_page(self, page: tuple[int, str]) -> tuple[list[MetricRecord], str]:
        page_number, text = page
        if not text.strip():
            return [], ""
        prompt = f"{TEXT_EXTRACTION_PROMPT}\n文本：{text[:12000]}"
        result = self.llm_client.chat_with_json(
            messages=[
                {
                    "role": "system",
                    "content": "你是结构化数据转录器，只转录原文，不添加结论。",
                },
                {"role": "user", "content": prompt},
            ],
            json_schema={"type": "object"},
            temperature=0,
        )
        values = result.get("metrics", []) if isinstance(result, dict) else []
        records: list[MetricRecord] = []
        for index, item in enumerate(values, start=1):
            metric = self._metric_from_payload(item, page_number, None, None, index)
            if metric:
                records.append(metric)
        return records, str(getattr(self.llm_client, "last_run_id", "") or "")


_vision_encoder_service: VisionEncoderService | None = None


def get_vision_encoder_service() -> VisionEncoderService:
    global _vision_encoder_service
    if _vision_encoder_service is None:
        _vision_encoder_service = VisionEncoderService()
    return _vision_encoder_service
