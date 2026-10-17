"""报告状态与审计事件的唯一写入入口（GLOSSARY.md 的「报告状态」「审计事件」）。

「这份报告现在处于什么阶段、下一步允许做什么」此前由四个平行通道回答：
``MedicalReport.status`` 列、``parsed_content`` JSON 里的 warnings/file_results、
``ReportExtractionJob.status``、以及审计事件的 action 词汇。写入点散在 API 与
worker 共八处，闸门（确认/评估能不能做）不在 status 列上，而是从 JSON 里重新
推导，且 ``recover_stale_jobs`` 改状态时完全不写审计。

本模块把状态收成一个：一份类型化词表、一个唯一的迁移函数。它只做两件事——
判定这次迁移是否合法、以及在同一次事务里落下状态与审计事件。

**审计事件也只有这一个生产者。** 「这份报告发生过什么」此前由两个互不相识的写入口
回答（本模块的 ``transition`` 与 API 模块私有的 ``_audit``），各自写同一张表、用同一批
action 名字，两边都不知道对方会为同一件事再写一条 —— 一次确认落下两条 ``confirmed``
（一条空壳、一条带实体），同一条 ``assessed`` 有两个 actor。现在：

- **状态变了** → ``transition(..., action=...)``，每次迁移**恰好一条**事件；
- **状态没变、只是记了一笔** → ``record_event(...)``，且 action 必须取自
  ``audit_vocabulary.AUDIT_ONLY_ACTIONS``。

两个入口都落在同一个 ``_write_event`` 上，所以「全仓库产生 ``ReportAuditEvent(`` 的地方
只有一处」是结构性的，不是约定。
"""

from __future__ import annotations

from typing import Any, Final, Literal

from sqlalchemy.orm import Session

from app.service.audit_vocabulary import (
    AUDIT_ACTIONS,
    AUDIT_ONLY_ACTIONS,
)

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
    """把报告迁到 ``to``，并在同一次事务里写下**恰好一条**审计事件。

    ``action`` 取自 ``audit_vocabulary.TRANSITION_ACTIONS``（状态变了的那一类）。传
    ``db`` 时才写审计 —— 上传路径在对象落库前就设置状态，那时还没有 id 可写事件；
    那一笔由调用方在 flush 之后用 ``record_event("uploaded", ...)`` 补，它是一类
    **审计专用**事件（状态没有动过），不再与迁移同形。

    ``detail`` 必须在**调用处一次给全**：此前 API 层用 `transition(action=...)` 写一个
    空壳、再紧跟一条 `_audit` 补实体，同一次评估因此落下两条 ``assessed`` 且 actor 不同。
    """
    current = getattr(report, "status", None)
    if current is not None and to not in _ALLOWED.get(current, frozenset()):
        raise IllegalTransition(current, to)
    report.status = to
    if db is not None and action is not None:
        _write_event(db, report, action, actor=actor, detail=detail)


def record_event(
    db: Session,
    report: Any,
    action: str,
    *,
    actor: str,
    detail: dict[str, object] | None = None,
) -> None:
    """记一笔**状态没有动过**的审计事件。

    ``action`` 必须取自 ``audit_vocabulary.AUDIT_ONLY_ACTIONS``（``uploaded``、
    ``extraction_committed_unacked``、``extraction_dropped_for_settled_report``）。
    传一个迁移类的 action 会被拒绝：那正是本入口存在的理由 —— 读者必须能分辨
    「状态动过」与「状态没动、只是记了一笔」，而两者同名过一次。
    """
    if action not in AUDIT_ONLY_ACTIONS:
        raise ValueError(
            f"{action!r} 不是审计专用事件；状态变了的事实走 transition(..., action=...)"
            f"（可取：{sorted(AUDIT_ONLY_ACTIONS)}）"
        )
    _write_event(db, report, action, actor=actor, detail=detail)


def _write_event(
    db: Session,
    report: Any,
    action: str,
    *,
    actor: str,
    detail: dict[str, object] | None,
) -> None:
    """**全仓库唯一**产生 ``ReportAuditEvent`` 的地方。

    词表是封闭的，而这里再校验一次：列仍是 ``String(64)``（兼容既有行，沿用报告状态列
    的做法），所以数据库不会替我们挡下一个拼错的 action。一个词表外的名字会写进一条
    契约读不懂的记录 —— 在写入点拒绝，比事后从流里读出来更容易定位。
    """
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"{action!r} 不在审计事件词表里（见 app/service/audit_vocabulary.py）")
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
