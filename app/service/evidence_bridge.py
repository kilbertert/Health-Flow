"""HTTP bridge from confirmed Health-Flow rows to genesis-evidence."""

from __future__ import annotations

import json
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
from app.service.metric_effective_value import effective_value

METRIC_ALIASES = {
    "收缩压": "systolic_blood_pressure",
    "舒张压": "diastolic_blood_pressure",
    "空腹血糖": "fasting_glucose",
    "糖化血红蛋白": "hba1c",
    "甘油三酯": "triglycerides",
    "高密度脂蛋白胆固醇": "hdl_c",
    "低密度脂蛋白胆固醇": "ldl_c",
    "低脂蛋白胆固醇": "ldl_c",
    "总胆固醇": "total_cholesterol",
    "非高密度脂蛋白胆固醇": "non_hdl_c",
    "Non-HDL": "non_hdl_c",
    "Non-HDL-C": "non_hdl_c",
    "Non HDL": "non_hdl_c",
    "Non HDL-C": "non_hdl_c",
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
_RANGE_RE = re.compile(r"(?P<low>-?\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(?P<high>-?\d+(?:\.\d+)?)")
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


def resolve_metric_code(name: str, catalog: Iterable[str] | None) -> str | None:
    """指标名 → 标准指标编码。**全仓库唯一的解析规则**（GLOSSARY.md 的「标准指标编码解析」）。

    规则（顺序即优先级）：

    1. **目录精确匹配**：名称归一化后直接在已发布目录里 —— 目录是唯一事实来源。
    2. **别名归一化 + 回目录验证**：用本地别名表把名称归一成一个候选编码，候选
       **必须回目录确认存在**才算解析成功。别名表只是「目录解析失败前的名称归一化
       辅助」，不是第二套事实来源 —— 目录里没有的编码，别名解析出来也不算数。
    3. **目录不可用**（``catalog is None``）：显式降级，只做别名归一化，不做存在性
       验证。调用方据此把编码留作「待目录恢复后重新裁决」，而不是当成已确认。

    解析不出来返回 ``None``：这样的指标不跨证据边界，保留为可追溯的未匹配项。
    """
    text = unicodedata.normalize("NFKC", _PARENTHETICAL_RE.sub("", name)).casefold()
    normalized = "".join(text.split())
    known = set(catalog) if catalog is not None else None
    if known is not None and normalized in known:
        return normalized
    candidate: str | None = None
    for label, code in METRIC_ALIASES.items():
        alias = "".join(unicodedata.normalize("NFKC", label).casefold().split())
        suffix = normalized.removeprefix(alias) if normalized.startswith(alias) else ""
        if normalized == alias or (
            suffix
            and (suffix.startswith(("/", "／")) or (suffix.isascii() and re.fullmatch(r"[a-z0-9%+._-]+", suffix)))
        ):
            candidate = code
            break
    if candidate is None and normalized in set(METRIC_ALIASES.values()):
        candidate = normalized
    if candidate is None:
        return None
    # 目录可用时，别名候选必须回目录验证 —— 否则别名表就成了第二套事实来源。
    if known is not None and candidate not in known:
        return None
    return candidate


def metric_code_for_name(name: str) -> str | None:
    """名称归一化，**不做目录验证**（``catalog=None`` 的降级形态）。

    只保留给既有测试与「目录不可用时的降级路径」；正式的解析入口是
    ``resolve_metric_code``，新代码不应直接调用本函数。
    """
    return resolve_metric_code(name, None)


def build_observations(metrics: Iterable[Any]) -> list[dict[str, object]]:
    """Build confirmed observations, preserving the legacy list return type."""

    observations, _ = build_observations_with_skipped(metrics)
    return observations


def build_observations_with_skipped(
    metrics: Iterable[Any],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Build confirmed observations and rows that cannot cross."""
    observations, skipped, _ = build_observations_with_unmatched(metrics)
    return observations, skipped


def build_observations_with_unmatched(
    metrics: Iterable[Any],
    catalog: Iterable[str] | None = None,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    """Separate safe evidence observations from abnormal unmapped rows."""
    observations: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    unmatched: list[dict[str, object]] = []
    for metric in metrics:
        if metric.confirmation_status not in {"confirmed", "corrected"}:
            continue
        effective = effective_value(metric)
        value_text = effective.value
        unit = effective.unit
        # 优先消费确认时落定的编码。落定为空时（确认那刻目录不可用，或该编码
        # 当时不在目录里）**用权威目录重新裁决一次** —— 但只在目录**可用**时。
        # 目录不可用时保持 unmatched：没有目录就没有验证，宁可不匹配也不猜。
        # 这条正是旧代码的反面 —— 旧代码在目录不可用时用本地别名快照复活。
        code = metric.metric_code
        if not code and catalog is not None:
            code = resolve_metric_code(metric.metric_name or "", catalog)
        evidence = effective.evidence_text
        if not unit or not evidence or metric.page_number is None:
            reason = (
                "missing_unit" if not unit else "missing_source_evidence" if not evidence else "missing_source_page"
            )
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": reason,
                }
            )
            continue
        if any(marker in str(value_text or "") for marker in ("<", ">", "≤", "≥")):
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "invalid_value",
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
        reference = effective.reference_range
        reference_low, reference_high = parse_reference_range(reference)
        if reference_low is None and reference_high is None:
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "missing_reference_range",
                }
            )
            continue
        if reference_low is not None and not _evidence_contains_value(evidence, reference_low):
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "missing_source_evidence",
                }
            )
            continue
        if reference_high is not None and not _evidence_contains_value(evidence, reference_high):
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "missing_source_evidence",
                }
            )
            continue
        flag = infer_abnormal_flag(str(value), reference)
        # 判成 N 就是「在参考范围内」；无法判定时 infer_abnormal_flag 返回 None，
        # 不在这里跳过 —— 上面的检查已按具体原因（invalid_value / missing_reference_range）
        # 各自给出过理由，走到这里说明判定是可判定的。
        if flag == "N":
            skipped.append(
                {
                    "observation_id": f"health-flow-metric-{metric.id}",
                    "reason": "within_reference_range",
                }
            )
            continue
        if not code:
            bbox = _coordinate_list(getattr(metric, "bbox", None))
            bbox_normalized = _coordinate_list(getattr(metric, "bbox_normalized", None))
            source_observation = {
                "observation_id": f"health-flow-metric-{metric.id}",
                "metric_code": None,
                "metric_label": metric.metric_name or "未命名指标",
                "value": value,
                "unit": unit,
                "reference_low": reference_low,
                "reference_high": reference_high,
                "evidence_text": evidence,
                "source_file_index": metric.source_file_index,
                "source_page": metric.page_number,
                "source_id": metric.source_id,
                "source_url": (
                    f"/api/health/report/{metric.report_id}/files/{metric.source_file_index}/pages/{metric.page_number}"
                    if metric.report_id is not None
                    else None
                ),
                "bbox": bbox,
                "bbox_normalized": bbox_normalized,
            }
            unmatched.append(
                {
                    "observation_id": source_observation["observation_id"],
                    "metric_code": None,
                    "metric_label": metric.metric_name or "未命名指标",
                    "condition_codes": [],
                    "reason": "unknown_metric_code",
                    "source_observation": source_observation,
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
                    f"/api/health/report/{metric.report_id}/files/{metric.source_file_index}/pages/{metric.page_number}"
                    if metric.report_id is not None and metric.page_number is not None
                    else None
                ),
                "bbox": _coordinate_list(getattr(metric, "bbox", None)),
                "bbox_normalized": _coordinate_list(getattr(metric, "bbox_normalized", None)),
            }
        )
    return observations, skipped, unmatched


async def match_published_evidence(
    observations: list[dict[str, object]],
    *,
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    headers = _service_headers(settings, correlate=True)
    payload = {"schema_version": "3", "observations": observations}
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
        result = EvidenceMatchResponse.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise EvidenceBridgeError("证据服务返回格式无效") from exc
    return result.model_dump(mode="json")


async def fetch_metric_catalog(*, settings: Settings | None = None) -> list[dict[str, str]]:
    settings = settings or get_settings()
    url = settings.GENESIS_EVIDENCE_METRICS_URL.strip()
    if not url:
        url = settings.GENESIS_EVIDENCE_API_URL.rsplit("/api/evidence/matches", 1)[0]
        url = f"{url}/api/metrics"
    try:
        async with httpx.AsyncClient(timeout=settings.GENESIS_EVIDENCE_TIMEOUT_SECONDS) as client:
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


def _inference_inputs(metric: Any) -> tuple[str, str | None]:
    """异常判定的输入：当前最佳值（确认值优先）与当前参考范围（确认范围优先）。

    两条优先级本身在 ``app/service/metric_effective_value.py`` 里 —— 那里是唯一
    实现，证据门禁、异常判定与响应契约都消费它。本函数只负责把生效值转成判定
    需要的形状（值转字符串、参考范围原样）。
    """
    effective = effective_value(metric)
    return str(effective.value or ""), effective.reference_range


def _decidable(metric: Any) -> bool:
    """这条指标是否进入判定。

    只有一条与「患者做了什么」有关的闸门：``excluded`` 的行被患者明确排除，
    不进入解读（证据门禁的入口守卫只处理 ``confirmed`` / ``corrected``），所以
    也不该被服务端标成异常 —— 否则历史摘要会为患者排除掉的指标计一条异常，而
    解读根本不会看到它。

    ``pending`` 不在此列：模型标了异常、值又可判定的行，患者正是要在确认页上
    看到它。把 pending 一并排除会让一份待确认的报告显示「未见异常指标」，那比
    多显示一个异常候选危险得多。

    其余的「能不能判」由值、参考范围决定，与本函数无关（空值走 ``missing_value``）。
    """
    return getattr(metric, "confirmation_status", None) != "excluded"


def abnormal_flag_reason(metric: Any) -> str | None:
    """判定不可判定时给出原因，可判定时返回 ``None``。

    原因词汇与证据门禁的 skipped reason 同源（``missing_value`` / ``invalid_value`` /
    ``missing_reference_range`` 见 ``build_observations_with_unmatched``），所以
    「响应里判成 N」与「门禁给出的跳过理由」说的是同一件事。
    """
    if not _decidable(metric):
        return "not_decidable"
    text, reference = _inference_inputs(metric)
    text = text.strip()
    if not text:
        return "missing_value"
    if any(marker in text for marker in ("<", ">", "≤", "≥")):
        return "invalid_value"
    if _single_number(text) is None:
        return "invalid_value"
    low, high = parse_reference_range(reference)
    if low is None and high is None:
        return "missing_reference_range"
    return None


def infer_abnormal_flag_for_metric(metric: Any) -> str | None:
    """一条指标行当前生效的异常判定：``"H" | "L" | "N"``，无法判定为 ``None``。

    这是服务端唯一入口，输入优先级与证据门禁一致，因此「患者看到什么」与
    「门禁拿什么去比对」是同一个答案。
    """
    if abnormal_flag_reason(metric) is not None:
        return None
    text, reference = _inference_inputs(metric)
    return infer_abnormal_flag(text, reference)


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


def _coordinate_list(value: object) -> list[float] | None:
    """Normalize ORM JSON values before they cross the evidence API boundary."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        coordinates = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return coordinates if all(math.isfinite(item) for item in coordinates) else None
