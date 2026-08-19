"""HTTP bridge from confirmed Health-Flow rows to genesis-evidence."""

from __future__ import annotations

import math
import re
import unicodedata
import uuid
from collections.abc import Iterable
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from app.config import Settings, get_settings
from app.schema.evidence import EvidenceMatchResponse, MetricCatalogItem

METRIC_ALIASES = {
    "收缩压": "systolic_blood_pressure",
    "舒张压": "diastolic_blood_pressure",
    "空腹血糖": "fasting_glucose",
    "糖化血红蛋白": "hba1c",
    "甘油三酯": "triglycerides",
    "高密度脂蛋白胆固醇": "hdl_c",
    "低密度脂蛋白胆固醇": "ldl_c",
    "总胆固醇": "total_cholesterol",
    "Total Cholesterol": "total_cholesterol",
    "Total Chol": "total_cholesterol",
    "Triglyceride": "triglycerides",
    "Triglycerides": "triglycerides",
    "HDL-C": "hdl_c",
    "LDL-C": "ldl_c",
    "丙氨酸氨基转移酶": "alt",
    "天门冬氨酸氨基转移酶": "ast",
    "γ-谷氨酰转移酶": "ggt",
    "尿酸": "uric_acid",
    "估算肾小球滤过率": "egfr",
    "肌酐": "creatinine",
    "尿白蛋白肌酐比": "uacr",
    "血红蛋白": "hemoglobin",
    "平均红细胞体积": "mcv",
    "铁蛋白": "ferritin",
    "转铁蛋白饱和度": "tsat",
    "25-羟维生素 D": "25_oh_vitamin_d",
    "骨密度 T 值": "bone_density_t_score",
    "钙": "calcium",
    "碱性磷酸酶": "alp",
    "握力": "grip_strength",
    "步速": "walking_speed",
    "肌肉量": "muscle_mass",
    "白蛋白": "albumin",
    "体重指数": "bmi",
    "身体质量指数": "bmi",
    "前白蛋白": "prealbumin",
    "谷丙转氨酶": "alt",
    "谷草转氨酶": "ast",
    "血钙": "calcium",
}
_PARENTHETICAL_RE = re.compile(r"[（(][^）)]*[）)]")
_NUMBER_RE = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])")
_RANGE_RE = re.compile(
    r"(?P<low>-?\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(?P<high>-?\d+(?:\.\d+)?)"
)
_UPPER_RE = re.compile(r"(?:<|<=|≤)\s*(?P<high>-?\d+(?:\.\d+)?)")
_LOWER_RE = re.compile(r"(?:>|>=|≥)\s*(?P<low>-?\d+(?:\.\d+)?)")


class EvidenceBridgeError(RuntimeError):
    """Raised when the evidence service cannot return a trustworthy result."""


def _service_headers(settings: Settings, *, correlate: bool = False) -> dict[str, str]:
    api_key = settings.GENESIS_EVIDENCE_API_KEY.strip()
    if not api_key:
        raise EvidenceBridgeError("证据服务认证未配置")
    headers = {"X-Genesis-Evidence-Key": api_key}
    if correlate:
        headers["X-Correlation-Id"] = str(uuid.uuid4())
    return headers


def metric_code_for_name(name: str) -> str | None:
    text = unicodedata.normalize("NFKC", _PARENTHETICAL_RE.sub("", name)).casefold()
    normalized = "".join(text.split())
    for label, code in METRIC_ALIASES.items():
        alias = "".join(unicodedata.normalize("NFKC", label).casefold().split())
        suffix = normalized.removeprefix(alias) if normalized.startswith(alias) else ""
        if normalized == alias or (
            suffix
            and (
                suffix.startswith(("/", "／"))
                or (suffix.isascii() and re.fullmatch(r"[a-z0-9%+._-]+", suffix))
            )
        ):
            return code
    return normalized if normalized in set(METRIC_ALIASES.values()) else None


def build_observations(metrics: Iterable[Any]) -> list[dict[str, object]]:
    """Build confirmed observations, preserving the legacy list return type."""

    observations, _ = build_observations_with_skipped(metrics)
    return observations


def build_observations_with_skipped(
    metrics: Iterable[Any],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Build confirmed observations and explicitly record rows that cannot cross."""
    observations: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for metric in metrics:
        if metric.confirmation_status not in {"confirmed", "corrected"}:
            continue
        value_text = metric.confirmed_value or metric.metric_value
        unit = metric.confirmed_unit or metric.unit
        code = metric.metric_code or metric_code_for_name(metric.metric_name or "")
        evidence = (
            getattr(metric, "confirmed_evidence_text", None) or metric.evidence_text
        )
        if not code or not unit or not evidence or metric.page_number is None:
            reason = (
                "unknown_metric_code"
                if not code
                else "missing_unit"
                if not unit
                else "missing_source_evidence"
                if not evidence
                else "missing_source_page"
            )
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": reason,
                }
            )
            continue
        value = _single_number(value_text)
        if value is None:
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "invalid_value",
                }
            )
            continue
        if not _evidence_contains_value(evidence, value):
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "missing_source_evidence",
                }
            )
            continue
        reference = metric.confirmed_reference_range
        if reference is None:
            reference = metric.reference_range
        reference_low, reference_high = parse_reference_range(reference)
        if reference_low is not None and not _evidence_contains_value(
            evidence, reference_low
        ):
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "missing_source_evidence",
                }
            )
            continue
        if reference_high is not None and not _evidence_contains_value(
            evidence, reference_high
        ):
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "missing_source_evidence",
                }
            )
            continue
        observations.append(
            {
                "observation_id": f"health-flow-metric-{metric.id}",
                "confirmation_status": "confirmed",
                "metric_code": code,
                "value": value,
                "unit": unit,
                "reference_low": reference_low,
                "reference_high": reference_high,
                "evidence_text": evidence,
                "source_file_index": metric.source_file_index,
                "source_page": metric.page_number,
                "source_id": metric.source_id,
                "source_url": (
                    f"/api/health/report/{metric.report_id}/files/"
                    f"{metric.source_file_index}/pages/{metric.page_number}"
                    if metric.report_id is not None and metric.page_number is not None
                    else None
                ),
                "bbox": getattr(metric, "bbox", None),
                "bbox_normalized": getattr(metric, "bbox_normalized", None),
            }
        )
    return observations, skipped


async def match_published_evidence(
    observations: list[dict[str, object]],
    *,
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    headers = _service_headers(settings, correlate=True)
    payload = {"schema_version": "2", "observations": observations}
    try:
        async with httpx.AsyncClient(
            timeout=settings.GENESIS_EVIDENCE_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                settings.GENESIS_EVIDENCE_API_URL,
                json=payload,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise EvidenceBridgeError("证据服务暂不可用") from exc
    if response.status_code >= 400:
        raise EvidenceBridgeError(f"证据服务返回 HTTP {response.status_code}")
    try:
        result = EvidenceMatchResponse.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise EvidenceBridgeError("证据服务返回格式无效") from exc
    return result.model_dump(mode="json")


async def fetch_metric_catalog(
    *, settings: Settings | None = None
) -> list[dict[str, str]]:
    settings = settings or get_settings()
    url = settings.GENESIS_EVIDENCE_METRICS_URL.strip()
    if not url:
        url = settings.GENESIS_EVIDENCE_API_URL.rsplit("/api/evidence/matches", 1)[0]
        url = f"{url}/api/metrics"
    try:
        async with httpx.AsyncClient(
            timeout=settings.GENESIS_EVIDENCE_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(url, headers=_service_headers(settings))
    except httpx.HTTPError as exc:
        raise EvidenceBridgeError("指标目录服务暂不可用") from exc
    if response.status_code >= 400:
        raise EvidenceBridgeError(f"指标目录服务返回 HTTP {response.status_code}")
    try:
        payload = TypeAdapter(list[MetricCatalogItem]).validate_python(response.json())
    except (ValueError, ValidationError) as exc:
        raise EvidenceBridgeError("指标目录服务返回格式无效") from exc
    return [item.model_dump() for item in payload]


def parse_reference_range(value: str | None) -> tuple[float | None, float | None]:
    text = (value or "").strip()
    match = _RANGE_RE.search(text)
    if match:
        low, high = float(match["low"]), float(match["high"])
        return (low, high) if low <= high else (None, None)
    match = _UPPER_RE.search(text)
    if match:
        return None, float(match["high"])
    match = _LOWER_RE.search(text)
    if match:
        return float(match["low"]), None
    return None, None


def infer_abnormal_flag(value: str | None, reference: str | None) -> str | None:
    text = (value or "").strip()
    if not text or any(marker in text for marker in ("<", ">", "≤", "≥")):
        return None
    number = _single_number(text)
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


def _single_number(value: str | None) -> float | None:
    matches = _NUMBER_RE.findall(value or "")
    return float(matches[0]) if len(matches) == 1 else None


def _evidence_contains_value(evidence: str, value: float) -> bool:
    for match in _NUMBER_RE.findall(unicodedata.normalize("NFKC", evidence)):
        if math.isclose(float(match), value, rel_tol=1e-9, abs_tol=1e-12):
            return True
    return False
