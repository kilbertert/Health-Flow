"""HTTP bridge from confirmed Health-Flow rows to genesis-evidence."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from typing import Any

import httpx

from app.config import Settings, get_settings

METRIC_ALIASES = {
    "收缩压": "systolic_blood_pressure",
    "舒张压": "diastolic_blood_pressure",
    "空腹血糖": "fasting_glucose",
    "糖化血红蛋白": "hba1c",
    "甘油三酯": "triglycerides",
    "高密度脂蛋白胆固醇": "hdl_c",
    "低密度脂蛋白胆固醇": "ldl_c",
    "总胆固醇": "total_cholesterol",
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
_RANGE_RE = re.compile(r"(?P<low>-?\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(?P<high>-?\d+(?:\.\d+)?)")
_UPPER_RE = re.compile(r"(?:<|<=|≤)\s*(?P<high>-?\d+(?:\.\d+)?)")
_LOWER_RE = re.compile(r"(?:>|>=|≥)\s*(?P<low>-?\d+(?:\.\d+)?)")
_VALID_METRIC_CODES = frozenset(METRIC_ALIASES.values())


class EvidenceBridgeError(RuntimeError):
    """Raised when the evidence service cannot return a trustworthy result."""


def metric_code_for_name(name: str) -> str | None:
    normalized = "".join(_PARENTHETICAL_RE.sub("", name.casefold()).split())
    for label, code in METRIC_ALIASES.items():
        if normalized == "".join(label.casefold().split()):
            return code
    return normalized if normalized in set(METRIC_ALIASES.values()) else None


def build_observations(metrics: Iterable[Any]) -> list[dict[str, object]]:
    """Build only confirmed observations; model flags never determine abnormality."""

    observations: list[dict[str, object]] = []
    for metric in metrics:
        if metric.confirmation_status not in {"confirmed", "corrected"}:
            continue
        value_text = metric.confirmed_value or metric.metric_value
        unit = metric.confirmed_unit or metric.unit
        code = metric.metric_code or metric_code_for_name(metric.metric_name or "")
        if (
            not code
            or code not in _VALID_METRIC_CODES
            or not unit
            or not metric.evidence_text
            or metric.page_number is None
        ):
            continue
        value = _single_number(value_text)
        if value is None:
            continue
        reference = metric.confirmed_reference_range
        if reference is None:
            reference = metric.reference_range
        reference_low, reference_high = parse_reference_range(reference)
        observations.append(
            {
                "observation_id": f"health-flow-metric-{metric.id}",
                "confirmation_status": "confirmed",
                "metric_code": code,
                "value": value,
                "unit": unit,
                "reference_low": reference_low,
                "reference_high": reference_high,
                "evidence_text": metric.evidence_text,
                "source_file_index": metric.source_file_index,
                "source_page": metric.page_number,
                "source_id": metric.source_id,
            }
        )
    return observations


async def match_published_evidence(
    observations: list[dict[str, object]],
    *,
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    headers = {"X-Correlation-Id": str(uuid.uuid4())}
    if settings.GENESIS_EVIDENCE_API_KEY:
        headers["X-Genesis-Evidence-Key"] = settings.GENESIS_EVIDENCE_API_KEY
    payload = {"schema_version": "1", "observations": observations}
    try:
        async with httpx.AsyncClient(timeout=settings.GENESIS_EVIDENCE_TIMEOUT_SECONDS) as client:
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
        result = response.json()
    except ValueError as exc:
        raise EvidenceBridgeError("证据服务返回格式无效") from exc
    if not isinstance(result, dict):
        raise EvidenceBridgeError("证据服务返回格式无效")
    return result


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


def _single_number(value: str | None) -> float | None:
    matches = _NUMBER_RE.findall(value or "")
    return float(matches[0]) if len(matches) == 1 else None
