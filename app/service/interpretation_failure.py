"""一次报告解读失败的成因与去向（GLOSSARY.md 的「解读失败」）。

「一次解读为什么失败、这条命还有没有救、患者现在该做什么」此前由四处互不同步的判定
回答，患者只看得到其中一句：

1. ``app/api/report.py`` 的 ``_TRANSIENT_ERROR_MARKERS``（14 个子串）对
   ``f"{filename}: {error}"`` 做 ``in`` 匹配 —— **文件名在被匹配的文本里**：把一份
   「没抽出指标」的体检单改名成 ``timeout.jpg``，重试决策就翻转；而真正瞬时的故障
   （额度耗尽、限流）因为用词不在表里而**永不重试**。
2. ``_parse_report`` 的兜底把**任何**异常（数据库断连、目录不可用）包成
   ``ParsedReport(success=False)``，与「这份报告本来就没读出指标」汇成同一条路 ——
   一个瞬时的基础设施故障，对患者永远表现为「这份报告读不出来，请重新上传」。
   这正是 ADR-0006 那次事故的形状。
3. 成因被塞进两个 JSON 键，其中一半根本不出域。
4. 兜底文案在四层各写一遍，措辞已经分叉。

本模块是**唯一**权威：五个成因、一个分类入口、以及由成因**派生**的两个读法
（是否可重试、患者侧动作）。判定的输入优先取**结构化事实**（HTTP 状态、异常类），
拿不到结构化事实时才看文本，且给出的仍然是一个**具名成因**而不是一个布尔。

它只 import 标准库。出域契约要读这份词表（``app/schema/report.py``），而 ``app.service``
的包级导入会把 ``vision_encoder`` 拖进契约层的导入路径（实测成环，见
``app/service/__init__.py`` 的 docstring）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal

#: 一次报告解读未能产出结构化指标时的**唯一**成因结论。按「坏在哪一环」划分。
#:
#: - ``material_unreadable`` —— 报告原文读不出来：我们存的字节没了，或者这份材料的
#:   类型走不通。**唯一**该叫患者重传的成因之一：它说的正是「你给的那份我们没读到」。
#:   它**不可自动重试** —— 字节不在就是不在，再跑一遍也还是不在。
#: - ``service_unavailable`` —— 服务端**自己**读写出错（数据库、文件系统、内部逻辑）。
#: - ``provider_rejected`` —— 模型侧**拒绝**了我们的请求（凭据过期、请求本身不合格）。
#:   重发同一份是徒劳 —— ADR-0006 已明确否决「把 401 当成可重试」。
#: - ``provider_unavailable`` —— 模型**当时**不可用（限流、额度耗尽、超时、连不上、
#:   返回的不是它答应的 JSON）。这是**可恢复**的一类。
#: - ``content_unrecognized`` —— 报告原文里没有可确认的指标。另一类该叫患者处理的
#:   成因：换一份更清晰的。
InterpretationFailure = Literal[
    "material_unreadable",
    "service_unavailable",
    "provider_rejected",
    "provider_unavailable",
    "content_unrecognized",
]

#: **上游序**：一次解读里若有多个成因，报告级结论取**最靠前**的那一环 —— 它才是
#: 首先坏掉的东西，后面的往往只是它的下游（读不到原文就谈不上模型，模型都没答上就
#: 谈不上「内容里没有指标」）。
FAILURES_ORDERED: Final[tuple[InterpretationFailure, ...]] = (
    "material_unreadable",
    "service_unavailable",
    "provider_rejected",
    "provider_unavailable",
    "content_unrecognized",
)

#: 报告失败时患者能做什么。**只有两个**：重传一份，或什么都不用做。
#: 「我们这边出的问题」与「你的这份读不出来」在这里分道扬镳 —— 前者叫患者重传是错的
#: （同一份材料再传一遍既救不了服务不可用，还会留下第二份记录）。
PatientAction = Literal["upload_again", "no_action"]

#: 可重试的成因 = 服务端或模型**当时**不行，换个时刻再来有意义。
#: 凭据被拒（``provider_rejected``）不在其中：同一份凭据重发多少次都一样。
RETRYABLE_FAILURES: Final[frozenset[str]] = frozenset({"service_unavailable", "provider_unavailable"})

#: 患者侧动作的词表（出域契约从它派生 `Literal`，与 `PatientAction` 同源）。
PATIENT_ACTIONS_ORDERED: Final[tuple[PatientAction, ...]] = ("upload_again", "no_action")

#: 成因 → 患者侧动作。**派生**读法，与 ``RETRYABLE_FAILURES`` 一样不是独立事实。
PATIENT_ACTION_BY_FAILURE: Final[dict[str, PatientAction]] = {
    "material_unreadable": "upload_again",
    "service_unavailable": "no_action",
    "provider_rejected": "no_action",
    "provider_unavailable": "no_action",
    "content_unrecognized": "upload_again",
}

#: 异常发生在**哪一环**。这是**调用点**才知道的结构化事实（它知道自己在调谁），
#: 所以由调用点给出，而不是让本模块从异常文本里反推。
Layer = Literal["material", "service", "provider"]

#: HTTP 状态里「你这次请求本身不合格」的那些。4xx 里 408 / 425 是时间性的，另列。
_REJECTED_STATUSES: Final[frozenset[int]] = frozenset(
    {400, 401, 403, 404, 405, 406, 409, 410, 413, 414, 415, 422}
)
#: HTTP 状态里「稍后就好了」的那些：请求超时、太早、限流，以及服务端 5xx。
_TRANSIENT_STATUSES: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 501, 502, 503, 504, 505, 507})

#: 异常**类名**里的时间性故障。这是结构化事实（类名是代码里的名字，不是人写的句子），
#: 只是跨库（httpx / openai / 标准库）没有共同的基类可以 isinstance。
_TRANSIENT_EXCEPTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "TimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TransportError",
        "RemoteProtocolError",
        "ConnectionError",
        "ConnectionResetError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "RateLimitError",
        "ServiceUnavailableError",
        "OverloadedError",
    }
)
#: 异常类名里的「拒绝了这次请求」。
_REJECTED_EXCEPTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "HTTPStatusError",  # 无 status_code 时兜不住，下面文本兜底
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "NotFoundError",
        "UnprocessableEntityError",
    }
)

#: 只能拿到文本时的判据。**顺序有意义**：先看「谁拒绝了我们」，再看状态码数字，最后才看
#: 时间性字样。
#:
#: 「这份报告里没有可确认的指标」**不在这里** —— 它是一个**内容**事实，由抽取方在看到
#: 空指标列表时当场判定（``_provider_failure`` 只回答「模型这一环怎么坏了」）。把它降级成
#: 文本嗅探正是本 PRD 要消灭的错法：一句「未提取到可确认的指标」的文本里没有时间性字样，
#: 可它旁边若跟着一个文件名，判定就会漂。
_REJECTION_TEXT: Final[tuple[str, ...]] = (
    "invalid 'authorization'",
    "invalid authorization",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "forbidden",
    "permission denied",
    "model not found",
    "认证失败",
    "无权",
)
#: 时间性故障的**用词**。这里是最后一道兜底（结构化事实拿不到时才走到），并且每一条
#: 都描述一个**协议或可用性**事实，而不是一个可能出现在文件名里的词。
_TRANSIENT_TEXT: Final[tuple[str, ...]] = (
    "timeout",
    "timed out",
    "temporarily",
    "unavailable",
    "connection",
    "reset by peer",
    "rate limit",
    "too many requests",
    "quota",
    "overloaded",
    "capacity",
    "server error",
    "bad gateway",
    "json",
    "超时",
    "暂时",
    "不可用",
    "连接",
    "限流",
    "额度",
    "过载",
)
#: 状态码以**数字**形式印在文本里时的兜底（如 ``Error code: 401 - ...``）。
_TRANSIENT_STATUS_DIGITS: Final[tuple[str, ...]] = ("408", "425", "429", "500", "502", "503", "504")
_REJECTED_STATUS_DIGITS: Final[tuple[str, ...]] = ("400", "401", "403", "404", "422")


def classify(layer: Layer, exc: BaseException | None = None, *, text: str = "") -> InterpretationFailure:
    """把「哪一环 + 结构化事实」判成一个**具名成因**。

    ``service`` 与 ``material`` 两环的结论由**位置**决定，不看文本：一次数据库断连的
    错误文本里恰好写着什么，与它是不是基础设施故障无关。只有 ``provider`` 环需要
    分类 —— 那一环的失败可能是凭据、是限流、是超时、是协议，也可能是「报告里就是
    没有指标」。

    ``provider`` 环判不出来时的兜底是 ``provider_unavailable``，不是叫患者处理：
    一个读不出来的 provider 故障，**不是患者的输入的问题**。重试次数由队列上限
    （``REPORT_JOB_MAX_ATTEMPTS``）兜住，不会无限重试。
    """
    if layer == "material":
        return "material_unreadable"
    if layer == "service":
        return "service_unavailable"
    return _provider_failure(exc, (text or "").casefold())


def _provider_failure(exc: BaseException | None, text: str) -> InterpretationFailure:
    status = _status_code(exc)
    if status is not None:
        if status in _TRANSIENT_STATUSES:
            return "provider_unavailable"
        if status in _REJECTED_STATUSES:
            return "provider_rejected"
    names = _exception_names(exc)
    if names & _TRANSIENT_EXCEPTION_NAMES:
        return "provider_unavailable"
    if names & _REJECTED_EXCEPTION_NAMES:
        return "provider_rejected"
    if any(marker in text for marker in _REJECTION_TEXT):
        return "provider_rejected"
    if any(marker in text for marker in _TRANSIENT_STATUS_DIGITS):
        return "provider_unavailable"
    if any(marker in text for marker in _REJECTED_STATUS_DIGITS):
        return "provider_rejected"
    if any(marker in text for marker in _TRANSIENT_TEXT):
        return "provider_unavailable"
    return "provider_unavailable"


def _status_code(exc: BaseException | None) -> int | None:
    """从异常上取 HTTP 状态。结构化的第一来源，取不到是 ``None``。"""
    if exc is None:
        return None
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int):
            return candidate
    # openai 的 ``APIStatusError.code`` 是字符串，但内容就是状态码数字。
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.strip().isdigit():
        return int(code.strip())
    return None


def _exception_names(exc: BaseException | None) -> frozenset[str]:
    """异常自身与它所有基类的类名。跨库没有共同基类，类名是唯一的结构化抓手。"""
    if exc is None:
        return frozenset()
    return frozenset(cls.__name__ for cls in type(exc).__mro__)


def report_failure(causes: Iterable[InterpretationFailure]) -> InterpretationFailure | None:
    """整次解读的成因：取 ``FAILURES_ORDERED`` 里**最靠前**的那一环。

    一点都没有是 ``None`` —— 那是「没有失败」，不是第六种成因。
    """
    present = set(causes)
    return next((cause for cause in FAILURES_ORDERED if cause in present), None)


def is_retryable(failure: InterpretationFailure | str | None) -> bool:
    """这条命还有没有救。成因的**派生读法** —— 不要另存一份布尔。"""
    return failure in RETRYABLE_FAILURES


def patient_action(failure: InterpretationFailure | str | None) -> PatientAction | None:
    """患者现在该做什么。同样是派生读法。没有成因时没有动作可给。"""
    return PATIENT_ACTION_BY_FAILURE.get(failure) if failure else None


#: 出域契约用的 `Literal`。`MedicalReportResponse.interpretation_failure` 从它派生 ——
#: 与 `AdmissionReasonLiteral` 同一个做法：值集恒等靠**派生**，不靠两处抄写。
#: 归属：`vocabulary()`。
PATIENT_ACTION_LITERAL = Literal[*PATIENT_ACTIONS_ORDERED]
FAILURE_LITERAL = Literal[*FAILURES_ORDERED]


def vocabulary() -> frozenset[str]:
    """词表的全部取值。供守卫使用：它必须与派生出的 `Literal` 完全相等。"""
    return frozenset(FAILURES_ORDERED)


#: 成因 → 闸门被拒时给患者的那句话。确认/评估走不进下一步时，服务端要说的是**为什么**
#: —— 今天它拿 `["文件名: 错误原文"]` 拼一句「请重新上传或先修复失败文件」，而那句话对
#: 「服务当时不可用」这类成因是错的。
#:
#: 它与患者侧的成因渲染（`frontend/src/interpretationFailure.js`）是**两处**，不是一份：
#: 这一处是服务端在 HTTP 202/409 响应里说的话（前端直接显示 `detail` 字符串），那一处
#: 是前端按同一个成因渲染的卡片文案。两者都由成因派生、都不会各自编一句兜底 —— 但它们
#: 处在不同的层，不共享字面量（前端没有、也不该拿到这份 Python 表）。
GATE_DETAIL_BY_FAILURE: Final[dict[str, str]] = {
    "material_unreadable": "这份报告我们没能读取到原文，请重新上传。",
    "service_unavailable": "报告暂未解析完成，我们这边出了点问题，请稍后再试。",
    "provider_rejected": "报告解析服务当前不可用，这不是你的输入的问题，请稍后再试。",
    "provider_unavailable": "报告暂未解析完成，解读服务当时不可用，请稍后再试。",
    "content_unrecognized": "这份报告里没有可确认的指标，请换一份更清晰的重传。",
}


def gate_detail(failure: InterpretationFailure | str | None) -> str | None:
    """闸门被拒时说的话 —— 由成因派生；没有成因时是 ``None``（调用点用自己的兜底）。"""
    return GATE_DETAIL_BY_FAILURE.get(failure) if failure else None
