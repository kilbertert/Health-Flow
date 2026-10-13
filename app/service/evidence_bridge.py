"""HTTP bridge from confirmed Health-Flow rows to genesis-evidence."""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from app.config import Settings, get_settings
from app.schema.evidence import EvidenceMatchResponse, MetricCatalogItem
from app.service.admission import (
    ADMITTED_STATUSES,
    SKIPPED_REASONS,
    admission_reason,
    infer_abnormal_flag,
    parse_reference_range,
    single_number,
    value_level_reason,
)
from app.service.confirmation_decision import is_excluded
from app.service.metric_effective_value import effective_value
from app.service.origin_location import clean_bbox, page_url

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
# 名称归一化用的括号剥离（**可重入**，逐层剥到不动为止）。**不是**参考范围解析的那一个
# —— 参考范围的括号由 `admission.parse_reference_range` 的正则自己处理，这里剥的是指标名
# 里的括注。
#
# 为什么必须重入：`LDL-C (低脂蛋白(坏)胆固醇)` 里嵌套着一对括号，而**嵌套**是真实的
# 抽取形态（报告 57）。单次 `sub` 只剥最外那对，留下 `ldl-c (低脂蛋白` 与一个孤立右括号
# —— 别名前缀还在，后缀却被切坏，于是这一行既匹配不上别名、也匹配不上目录。
_PARENTHETICAL_RE = re.compile(r"[（(][^（()）]*[）)]")

# 别名后缀的两种合法形态（见 `resolve_metric_code` 的规则 2）。
_ALIAS_SUFFIX_ASCII_RE = re.compile(r"[a-z0-9%+._-]+")
_ALIAS_SUFFIX_CJK_RE = re.compile(r"[㐀-䶿一-鿿]+")


def _strip_redundant_parentheticals(name: str) -> str:
    """逐层剥掉括注，直到名称不再变化。"""
    previous = name
    while True:
        stripped = _PARENTHETICAL_RE.sub("", previous)
        if stripped == previous:
            return stripped
        previous = stripped


def _alias_suffix_is_redundant(suffix: str, alias: str) -> bool:
    """别名之后那段多出来的文字，是不是**同一件事的中文写法** —— 两种形态，缺一不可。

    这里是本票（#235）修的那一处：抽取名常把一项印成「英文缩写 + 中文全称」，例如
    `LDL-C 低脂蛋白(坏)胆固醇`、`Total Chol 总胆固醇`。归一化后名称**以别名开头**，
    后缀却是中文，而旧规则只认「以 `/` 开头」或「纯 ASCII」的后缀 —— 于是全库唯一的
    真异常（`LDL-C 3.63`）连不到知识卡、进不了解读。

    **中文后缀只在别名本身是 ASCII 时才认**，这是评审在 #237 指出的一处漏洞：中文里
    没有词间空格，所以「中文别名 + 中文」与「两项粘连」在字面上不可分 ——
    `空腹血糖餐后血糖` 会被判成「空腹血糖」的中文写法，而它其实是**两项**，于是拿到一张
    错的知识卡。而「英文缩写 + 中文全称」之所以可分，正因为**前半是拉丁字母、后半是汉字**
    —— 那种拼接不会是两个中文指标名连在一起。所以：

    - **纯 ASCII 后缀**（`ldl-c / cholesterol`）—— 旧的 `[a-z0-9%+._-]+` 原样保留，
      别名是什么都可以；
    - **纯中文后缀**（`低脂蛋白胆固醇`）—— 只认 `alias` 是纯 ASCII 的情形。

    混合脚本（`ldl-c低density`）一律拒绝 —— 放行它等于承认「任意粘连」都算同义词。
    这条判据的上限写在明处：一个**中文缩写**后面跟着它自己的中文全称会被拒绝（那种形态
    在观测到的报告里不存在，而猜错的方向是把两项合成一项）。别名表里的中文条目本来就
    是完整指标名，精确匹配那一条覆盖它们。
    """
    if not suffix:
        return False
    if suffix.startswith(("/", "／")):
        return True
    if _ALIAS_SUFFIX_ASCII_RE.fullmatch(suffix):
        return True
    return alias.isascii() and bool(_ALIAS_SUFFIX_CJK_RE.fullmatch(suffix))


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

    规则 2 的「后缀」判据在 `_alias_suffix_is_redundant`（本票 #235 修的是它：抽取名
    常写成「英文别名 + 中文全称」，旧规则只认 `/` 开头或纯 ASCII 后缀，于是
    `LDL-C 低脂蛋白(坏)胆固醇` 解析不出来 —— 那是全库唯一的真异常）。
    """
    text = unicodedata.normalize("NFKC", _strip_redundant_parentheticals(name)).casefold()
    normalized = "".join(text.split())
    known = set(catalog) if catalog is not None else None
    if known is not None and normalized in known:
        return normalized
    # **最长别名优先**：`Non-HDL-C` 与 `Non-HDL`、`Total Cholesterol` 与 `Total Chol`
    # 都在表里，短的那个是长的那个的前缀。按表序取第一个命中的话，命中的是哪一个取决
    # 于字面顺序 —— 那是一个随每次编辑别名表都会变的隐式判据。
    candidate: str | None = None
    matched_length = -1
    for label, code in METRIC_ALIASES.items():
        alias = "".join(unicodedata.normalize("NFKC", label).casefold().split())
        if not normalized.startswith(alias):
            continue
        suffix = normalized.removeprefix(alias)
        if not (normalized == alias or _alias_suffix_is_redundant(suffix, alias)):
            continue
        if len(alias) > matched_length:
            candidate, matched_length = code, len(alias)
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


def _observation_payload(metric: Any, *, code: str, effective: Any) -> dict[str, object]:
    """跨证据边界的观察载荷。**只有准入结论为「进入解读」时才会被调用** ——
    所以这里可以放心地假定值、原文证据、页码都已齐备。

    单位**不**在此列，是刻意的：无量纲项（比值、pH、尿比重）的「单位」就是「没有单位」
    这件事本身 —— 达标是 `unit=""` 而不是 `unit=None`，因为契约（`SourceObservation.unit`）
    要求一个字符串。准入只把无量纲项放到这里来（缺单位的**测量**行在那边就被拦下了），
    所以这条兜底不会替任何真实测量掩盖一个缺失的单位。
    """
    reference_low, reference_high = parse_reference_range(effective.reference_range)
    return {
        "observation_id": f"health-flow-metric-{metric.id}",
        "confirmation_status": "confirmed",
        "metric_code": code,
        "value": single_number(effective.value),
        "unit": effective.unit or "",
        "reference_low": reference_low,
        "reference_high": reference_high,
        "evidence_text": effective.evidence_text,
        "source_file_index": metric.source_file_index,
        "source_page": metric.page_number,
        "source_id": metric.source_id,
        "source_url": page_url(metric.report_id, metric.source_file_index, metric.page_number),
        "bbox": _coordinates_for_boundary(getattr(metric, "bbox", None)),
        "bbox_normalized": _coordinates_for_boundary(getattr(metric, "bbox_normalized", None)),
    }


def _unmatched_payload(metric: Any, *, effective: Any) -> dict[str, object]:
    """`unknown_metric_code` 的行：值判得出来、也没有可匹配的编码。

    它的 `source_observation` 是承载「患者要看的是哪一行」与定位的那些字段 —— 所以
    这里与 `_observation_payload` 用同一份输入（`effective_value`），不是各取一套。
    """
    reference_low, reference_high = parse_reference_range(effective.reference_range)
    value = single_number(effective.value)
    observation_id = f"health-flow-metric-{metric.id}"
    source_observation = {
        "observation_id": observation_id,
        "metric_code": None,
        "metric_label": metric.metric_name or "未命名指标",
        "value": value,
        "unit": effective.unit or "",
        "reference_low": reference_low,
        "reference_high": reference_high,
        "evidence_text": effective.evidence_text,
        "source_file_index": metric.source_file_index,
        "source_page": metric.page_number,
        "source_id": metric.source_id,
        # 定位 URL 只有一个 builder；守卫统一为「report_id 与 page_number 都在才拼」。
        "source_url": page_url(metric.report_id, metric.source_file_index, metric.page_number),
        "bbox": _coordinates_for_boundary(getattr(metric, "bbox", None)),
        "bbox_normalized": _coordinates_for_boundary(getattr(metric, "bbox_normalized", None)),
    }
    return {
        "observation_id": observation_id,
        "metric_code": None,
        "metric_label": metric.metric_name or "未命名指标",
        "condition_codes": [],
        "reason": "unknown_metric_code",
        "source_observation": source_observation,
    }


def build_observations_with_unmatched(
    metrics: Iterable[Any],
    catalog: Iterable[str] | None = None,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    """把已核对的行分成「跨证据边界」与「没跨过去，及为什么」。

    准入结论由 `admission.admission_reason` **单点**给出 —— 本函数不再自己拼原因串。
    此前它在这里用一段三元表达式与八处 `skipped.append` 各自写下名字，于是「空值」
    在这一侧是 `invalid_value`、在判定守卫那一侧是 `missing_value`：同一个事实两个
    名字。现在两侧调的是同一个函数，分叉不可能再出现。

    三桶的**划分**没变（那不在本票射程内），变的是每条原因从哪来 —— 以及
    `Skipped` 的词表不再是这里手写的第二个来源。
    """
    observations: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    unmatched: list[dict[str, object]] = []
    for metric in metrics:
        status = getattr(metric, "confirmation_status", None) or "pending"
        if status not in ADMITTED_STATUSES:
            # 尚未核对、患者已排除：本票给它们准入结论，但它们不进跨证据边界的
            # 载荷、也不进 skipped / unmatched 两个数组（分桶不动）。结论随指标行
            # 逐条出域，是下一票的事。
            continue
        effective = effective_value(metric)
        # 优先消费确认时落定的编码。落定为空时（确认那刻目录不可用，或该编码当时
        # 不在目录里）**用权威目录重新裁决一次** —— 但只在目录**可用**时。目录不可
        # 用时保持 unmatched：没有目录就没有验证，宁可不匹配也不猜。
        code = metric.metric_code
        if not code and catalog is not None:
            code = resolve_metric_code(metric.metric_name or "", catalog)
        reason = admission_reason(metric, code=code)
        if reason is None:
            observations.append(_observation_payload(metric, code=code, effective=effective))
        elif reason == "unknown_metric_code":
            unmatched.append(_unmatched_payload(metric, effective=effective))
        elif reason in SKIPPED_REASONS:
            skipped.append({"observation_id": f"health-flow-metric-{metric.id}", "reason": reason})
        else:  # pragma: no cover - 词表漂移由 test_admission 的守卫逮住
            raise ValueError(f"准入结论没有对应的桶：{reason!r}")
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


def _inference_inputs(metric: Any) -> tuple[str, str | None, str | None]:
    """异常判定的输入：当前最佳值（确认值优先）、当前参考范围与单位（同样确认优先）。

    三条优先级本身在 ``app/service/metric_effective_value.py`` 里 —— 那里是唯一
    实现，证据门禁、异常判定与响应契约都消费它。本函数只负责把生效值转成判定
    需要的形状（值转字符串、参考范围与单位原样）。

    单位与值、参考范围同源（都在同一个四元组里）而不是从 ``metric.unit`` 另取一次：
    「这个界之后还剩什么」要按**生效**单位判，否则患者修正过单位的行会在这一侧与
    准入那一侧给出不同答案。
    """
    effective = effective_value(metric)
    return str(effective.value or ""), effective.reference_range, effective.unit


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
    定性项（`Nitrite Negative (Negative)`）由报告自己印出的取值域判成 N，走的是
    `admission.admission_reason` 的正路 —— 本函数对它与对数值项一视同仁。

    判据消费 `confirmation_decision.is_excluded`，不再自带一份「不等于 excluded」的反向
    表述 —— 那是同一条判定的第二份写法（证据门禁读的是正面枚举 `ADMITTED_STATUSES`），
    两者在**新增一种决策**时给出不同答案。两处的差别（这里对 ``pending`` 也作答）是**两个
    不同的问题**，不是两份实现。
    """
    return not is_excluded(getattr(metric, "confirmation_status", None))


def abnormal_flag_reason(metric: Any) -> str | None:
    """判定不可判定时给出原因，可判定时返回 ``None``。

    原因词汇**就是**证据门禁那份（`app/service/admission.py` 的唯一词表），并且
    值/参考范围这一类由**同一个函数**判出 —— 所以「判不出来」在这两处不可能再各起
    一个名字。此前这句 docstring 声称同源，而实际上 ``missing_value`` 根本不在证据
    门禁的词表里，空值一行在两侧得到两个不同的名字。
    """
    if not _decidable(metric):
        return "not_decidable"
    text, reference, unit = _inference_inputs(metric)
    # 与准入结论**同一份**判定：空值一行在这一侧与证据门禁那一侧从此同名
    # （此前分别是 `missing_value` 与 `invalid_value`）。带符号的「报告自己给了判据」
    # 那一类也在同一个函数里先答 —— 不在这里另摆一遍顺序（#235）。
    return value_level_reason(text, reference, unit)


def infer_abnormal_flag_for_metric(metric: Any) -> str | None:
    """一条指标行当前生效的异常判定：``"H" | "L" | "N"``，无法判定为 ``None``。

    这是服务端唯一入口，输入优先级与证据门禁一致，因此「患者看到什么」与
    「门禁拿什么去比对」是同一个答案。
    """
    if abnormal_flag_reason(metric) is not None:
        return None
    text, reference, unit = _inference_inputs(metric)
    return infer_abnormal_flag(text, reference, unit)


# 参考范围解析、数值解析与异常判定的**实现**住在 `app/service/admission.py`
# （「解读准入」）。这里保留 `parse_reference_range` / `infer_abnormal_flag` 的
# **同名导出**，因为 `vision_encoder`（抽取时判异常标记）与既有测试从这个模块取它们
# —— 换掉导入路径是纯粹的搬家，不捎带在收敛词表这一票里。


def _coordinates_for_boundary(value: object) -> list[float] | None:
    """证据边界上读出来的坐标：**读取边界，容忍历史行**。

    判定走 `app/service/origin_location.py` 的同一处（`strict=False`）——
    此前这里只查长度与有限性，连**逆序**与**负数**都放行，是本票要消灭的
    「同一概念四种判定」里的第四种。
    """
    return clean_bbox(value, strict=False)
