"""会话端点：退出、当前主体、报告历史。

**自助注册、密码、登录整体退役**（health-flow #172）。主体只能由商城票据兑换创建，
所以这里不再有任何「用户自己创建或证明身份」的路径。会话本身保留——票据兑换后仍需
服务端会话，**会话存储不等于身份源**。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, session_owner_dependency
from app.config import get_settings
from app.data.models import MedicalReport
from app.schema.auth import ReportHistoryItem, SessionSubjectResponse, TicketExchangeRequest
from app.service.report_ownership import subject_storage_id
from app.service.sessions import SESSION_COOKIE, revoke_session
from app.service.tickets import TicketError, build_verifier

router = APIRouter()
_ABNORMAL_FLAGS = frozenset({"H", "L", "A", "*", "HIGH", "LOW", "高", "低"})


def _is_abnormal_flag(flag: str | None) -> bool:
    return str(flag or "").strip().upper() in _ABNORMAL_FLAGS


def _abnormal_metric_count(report: MedicalReport) -> int:
    return sum(1 for metric in report.metrics if _is_abnormal_flag(metric.abnormal_flag))


def _require_owner(owner_id: str | None) -> str:
    """本票之后**没有**「回退到登录页」这条路：拿不到主体就是 401。

    票面明确写了「解析失败不得回退到登录页（那会让整个应用白屏）」。所以这里只报错，
    不重定向、不渲染任何需要身份的东西。
    """
    if not owner_id:
        raise HTTPException(status_code=401, detail="会话无效，请从商城入口重新进入")
    return owner_id


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: Session = Depends(db_dependency),
):
    revoke_session(request, db)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=SessionSubjectResponse)
async def me(
    owner_id: str | None = Depends(session_owner_dependency),
    db: Session = Depends(db_dependency),
):
    """当前会话的主体。

    **从主体标识解析，不查账号表。** `account:<tenant>:<sub>` 形状里前两段之外的部分
    就是商城的用户标识；退役期的 `account:<uuid>` 历史会话也能解析（第二段缺失时
    整个标识就是用户标识），那是「既有会话到期前仍可用」的自然结果，不是特例分支。

    展示名从**主体记录**里取（它是票据兑换时建的），查不到就用默认值——退役期的
    历史会话没有主体记录，那是正常的，不是错误分支。
    """

    storage_id = _require_owner(owner_id)
    return _subject_response(db, storage_id)


def _subject_response(db: Session, storage_id: str) -> SessionSubjectResponse:
    """主体标识 → 响应体，带上记录里的展示名（若有）。

    兑换端点与 `/me` **共用这一处**：否则两条路径会给出不同的展示名——兑换时回默认值、
    刷新后才变成记录里的名字，用户会看到名字闪一下。
    """
    from app.data.models import TicketSubject

    response = SessionSubjectResponse.from_storage_id(storage_id)
    if response.tenant_id:
        subject = (
            db.query(TicketSubject)
            .filter(
                TicketSubject.tenant_id == response.tenant_id,
                TicketSubject.external_subject == response.external_subject,
            )
            .first()
        )
        if subject is not None:
            response.display_name = subject.display_name
    return response


@router.get("/reports", response_model=list[ReportHistoryItem])
async def report_history(
    owner_id: str | None = Depends(session_owner_dependency),
    db: Session = Depends(db_dependency),
):
    storage_id = _require_owner(owner_id)
    # 归属判定仍走 ownership 模块这一处，不在这里重复「主体的报告就是 owner_id 相等的行」
    # ——那条相等关系是归属模型的属性，复制一份就是第二条等着和第一条打架的规则。
    reports = (
        db.query(MedicalReport)
        .filter(MedicalReport.owner_id == storage_id)
        .order_by(MedicalReport.created_at.desc())
        .all()
    )
    return [
        ReportHistoryItem(
            id=report.id,
            report_type=report.report_type,
            department=report.department,
            status=report.status,
            exam_date=report.exam_date,
            created_at=report.created_at,
            metric_count=len(report.metrics),
            abnormal_count=_abnormal_metric_count(report),
            finding_count=(
                len(report.evidence_result.get("findings") or []) if isinstance(report.evidence_result, dict) else 0
            ),
        )
        for report in reports
    ]


@router.post("/ticket", response_model=SessionSubjectResponse, status_code=201)
async def exchange_ticket(
    request: Request,
    response: Response,
    payload: TicketExchangeRequest,
    db: Session = Depends(db_dependency),
):
    """用一张商城票据换取本地会话。

    **这是本票之后唯一的身份建立入口**（注册与登录已退役）。顺序与失败语义都有讲究：

    - 验签与主体创建由 `redeem_and_ensure_subject` 完成，它**先提交票据消费、再建主体**
      ——所以并发下同一张票只有一个请求能走到这里（见该函数的说明）。
    - **票已消费但主体创建失败**是这条路径上一个显式的失败状态：那张票永久失效，
      用户只能重新从商城跳一次。这里把它转成一个明确的 401 与可读的说明，
      **不回退到登录页**（票面要求：回退会让整个应用白屏）。
    """
    from app.service.sessions import issue_session

    # **先做长度校验，再兑换。** 主体标识超出 `owner_id` 列宽时，
    # `subject_storage_id` 会在**票据已被消费之后**抛错——那张票就白烧了，
    # 而且用户再跳一次还是同样结果。长度只取决于票据里的两个标识，兑换前就能算，
    # 所以把这道校验前置：要么明确报错（票据未被消费），要么放行。
    verifier = getattr(request.app.state, "ticket_verifier", None)
    if verifier is None:
        # 启动期（lifespan）已经建好验签器；这里是为**没有走 lifespan 的场景**
        # （测试里的 TestClient、嵌入式调用）补一条懒建路径。它调的是同一个
        # `build_verifier`，所以 fail-closed 的性质不变：配置不全就抛，转成 503。
        # 结果缓存在 app.state 上，不每个请求重读密钥文件。
        try:
            verifier = build_verifier()
        except TicketError as exc:
            raise HTTPException(status_code=503, detail=f"票据验签未就绪：{exc}") from exc
        request.app.state.ticket_verifier = verifier

    # **先校验主体标识长度，再兑换。** 超出 `owner_id` 列宽时，`subject_storage_id()`
    # 会在**票据已被消费之后**才抛错——那张票白烧了，用户再跳一次也是同样结果。
    # 长度只取决于票据里的两个标识，验签通过就能算，所以前置：不合法就明确报错
    # （此时票据**尚未**被消费）。
    try:
        claims = verifier.verify(payload.ticket)
        subject_storage_id(claims.tenant_id, claims.subject)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"票据主体标识不可用：{exc}") from exc
    except TicketError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        identity = verifier.redeem_and_ensure_subject(db, payload.ticket)
    except TicketError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    db.expire_all()
    token, session_row = issue_session(
        identity.storage_id,
        days=get_settings().AUTH_SESSION_DAYS,
    )
    db.add(session_row)
    db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max(1, get_settings().AUTH_SESSION_DAYS) * 86400,
        httponly=True,
        secure=get_settings().auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return _subject_response(db, identity.storage_id)
