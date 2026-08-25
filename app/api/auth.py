"""Email/password account and server-side session endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.config import get_settings
from app.data.models import MedicalReport, UserAccount, UserSession
from app.schema.auth import (
    AccountResponse,
    LoginRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    ReportHistoryItem,
)
from app.service.auth import (
    SESSION_COOKIE,
    account_for_request,
    issue_session,
    new_account,
    normalize_email,
    session_hash,
    verify_password,
)

router = APIRouter()
_ABNORMAL_FLAGS = frozenset({"H", "L", "A", "*", "HIGH", "LOW", "高", "低"})


def _is_abnormal_flag(flag: str | None) -> bool:
    return str(flag or "").strip().upper() in _ABNORMAL_FLAGS


def _abnormal_metric_count(report: MedicalReport) -> int:
    return sum(1 for metric in report.metrics if _is_abnormal_flag(metric.abnormal_flag))


def _account_response(account: UserAccount) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        email=account.email,
        display_name=account.display_name,
        created_at=account.created_at,
    )


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max(1, settings.AUTH_SESSION_DAYS) * 86400,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _account(request: Request, db: Session) -> UserAccount:
    account = account_for_request(request, db)
    if account is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return account


@router.post("/register", response_model=AccountResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    response: Response,
    db: Session = Depends(db_dependency),
):
    account = new_account(payload.email, payload.password, payload.display_name)
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该邮箱已注册") from exc
    token, session = issue_session(account.id, days=get_settings().AUTH_SESSION_DAYS)
    db.add(session)
    db.commit()
    db.refresh(account)
    _set_session_cookie(response, token)
    return _account_response(account)


@router.post("/login", response_model=AccountResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(db_dependency),
):
    account = (
        db.query(UserAccount)
        .filter(UserAccount.email == normalize_email(payload.email))
        .first()
    )
    if (
        account is None
        or not account.is_active
        or not verify_password(payload.password, account.password_hash)
    ):
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    now = datetime.now()
    db.query(UserSession).filter(
        UserSession.account_id == account.id,
        UserSession.revoked_at.is_(None),
    ).update({UserSession.revoked_at: now}, synchronize_session=False)
    token, session = issue_session(account.id, days=get_settings().AUTH_SESSION_DAYS)
    db.add(session)
    db.commit()
    _set_session_cookie(response, token)
    return _account_response(account)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: Session = Depends(db_dependency),
):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        db.query(UserSession).filter(
            UserSession.token_hash == session_hash(token),
            UserSession.revoked_at.is_(None),
        ).update({UserSession.revoked_at: datetime.now()}, synchronize_session=False)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=AccountResponse)
async def me(request: Request, db: Session = Depends(db_dependency)):
    return _account_response(_account(request, db))


@router.patch("/profile", response_model=AccountResponse)
async def update_profile(
    payload: ProfileUpdateRequest,
    request: Request,
    db: Session = Depends(db_dependency),
):
    account = _account(request, db)
    account.display_name = payload.display_name.strip()
    db.commit()
    db.refresh(account)
    return _account_response(account)


@router.get("/reports", response_model=list[ReportHistoryItem])
async def report_history(request: Request, db: Session = Depends(db_dependency)):
    account = _account(request, db)
    reports = (
        db.query(MedicalReport)
        .filter(MedicalReport.owner_id == account.id)
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
                len(report.evidence_result.get("findings") or [])
                if isinstance(report.evidence_result, dict)
                else 0
            ),
        )
        for report in reports
    ]
