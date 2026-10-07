"""报告状态的唯一迁移入口（GLOSSARY.md 的「报告状态」）。

「这份报告现在处于什么阶段、下一步允许做什么」此前由四个平行通道回答：
``MedicalReport.status`` 列、``parsed_content`` JSON 里的 warnings/file_results、
``ReportExtractionJob.status``、以及审计事件的 action 词汇。写入点散在 API 与
worker 共八处，闸门（确认/评估能不能做）不在 status 列上，而是从 JSON 里重新
推导，且 ``recover_stale_jobs`` 改状态时完全不写审计。

本模块把状态收成一个：一份类型化词表、一个唯一的迁移函数。它只做两件事——
判定这次迁移是否合法、以及在同一次事务里落下状态与审计事件。
"""

from __future__ import annotations

from typing import Any, Final, Literal

from sqlalchemy.orm import Session

# 患者可见的状态词表。数据库列仍是 String（兼容既有行），但所有写入都必须经过
# 本模块，因此这五个值就是唯一的词汇。
ReportStatus = Literal["processing", "pending_confirmation", "confirmed", "assessed", "failed"]

PATIENT_STATUSES: Final[tuple[ReportStatus, ...]] = (
    "processing",
    "pending_confirmation",
    "confirmed",
    "assessed",
    "failed",
)

# 允许的迁移。这不是「完整的图」而是「今天真实存在的路径」：
#   upload        → processing
#   parse         → processing（有文件没解析完）| pending_confirmation
#   parse 失败     → failed
#   worker 重试    → processing（**从 failed** 再入队，不是从待确认）
#   worker 失败    → failed
#   confirm       → confirmed
#   assess        → assessed（从 confirmed 或 assessed 重跑）
# 明确**不允许**回到更早的阶段：一份已确认/已评估的报告不该被退回待确认，
# 待确认的报告也不该被退回 processing（那意味着它的指标会被重新解析、患者已
# 核对过的行被清空）。
_ALLOWED: Final[dict[ReportStatus, frozenset[ReportStatus]]] = {
    "processing": frozenset({"processing", "pending_confirmation", "failed"}),
    "pending_confirmation": frozenset({"pending_confirmation", "confirmed", "failed"}),
    "confirmed": frozenset({"confirmed", "assessed", "failed"}),
    "assessed": frozenset({"assessed", "failed"}),
    "failed": frozenset({"processing", "failed"}),
}


class IllegalTransition(ValueError):
    """状态迁移不在允许表内。"""

    def __init__(self, current: str | None, target: str) -> None:
        super().__init__(f"报告状态不能从 {current or '(未设置)'} 迁移到 {target}")
        self.current = current
        self.target = target


def transition(
    report: Any,
    to: ReportStatus,
    *,
    db: Session | None = None,
    action: str | None = None,
    actor: str = "system",
    detail: dict[str, object] | None = None,
) -> None:
    """把报告迁到 ``to``，并在同一次事务里写下这次迁移。

    ``action`` 是审计事件的 action 词汇（``uploaded`` / ``extraction_partial`` /
    ``extraction_completed`` / ``extraction_failed`` / ``extraction_retry_queued`` /
    ``confirmed`` / ``assessed`` / ``status_recovered``）。传 ``db`` 时才写审计——
    上传路径在对象落库前就设置状态，那时还没有 id 可写审计事件。
    """
    current = getattr(report, "status", None)
    if current is not None and to not in _ALLOWED.get(current, frozenset()):
        raise IllegalTransition(current, to)
    report.status = to
    if db is not None and action is not None:
        _write_audit(db, report, action, actor=actor, detail=detail)


def _write_audit(
    db: Session,
    report: Any,
    action: str,
    *,
    actor: str,
    detail: dict[str, object] | None,
) -> None:
    # 延迟导入避免与 app.data.models 形成导入环。
    from app.data.models import ReportAuditEvent

    db.add(
        ReportAuditEvent(
            report_id=report.id,
            action=action,
            actor=actor,
            correlation_id=getattr(report, "evidence_correlation_id", None),
            detail=detail or {},
        )
    )
