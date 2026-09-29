"""会话：票据兑换后由服务端建立的浏览器会话。

**会话存储不等于身份源**（票面语）。本模块只做「会话」这一半：签发、查找、撤销。
身份从哪来由调用方决定——本票之后只有一个来源：商城票据兑换出的主体
（`account:<tenant>:<sub>`）。

本模块**不再包含任何「用户自己创建身份」的路径**：没有注册、没有密码校验、没有邮箱。
自助注册与密码整体退役，账号表保留只读至自然消亡。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.data.models import UserSession

SESSION_COOKIE = "healthflow_session"


def issue_session(owner_id: str, *, days: int = 30) -> tuple[str, UserSession]:
    """为该主体签发一次浏览器会话。浏览器只拿到随机 token，库里存哈希。

    参数名从 `account_id` 改为 `owner_id`：它现在装的是主体的**存储标识**
    （`account:<tenant>:<sub>`），而不再是「账号表的主键」。列名仍是 `account_id`
    ——它是已存数据的形状，改名属于迁移而不是本票的事。
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    session = UserSession(
        account_id=owner_id,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=days),
    )
    return token, session


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_owner_id(request, db: Session) -> str | None:
    """当前请求的**会话主体存储标识**，没有有效会话时返回 None。

    **它不查账号表。** 会话行里的 `account_id` 现在直接就是主体标识
    （`account:<tenant>:<sub>`），所以「会话 → 主体」是一次直接读取，不需要
    「会话 → 账号 → 主体」这条两步链——后者是账号体系还在时的形状，留着它
    等于让退役后的代码继续假设账号表存在。
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    session = (
        db.query(UserSession)
        .filter(
            UserSession.token_hash == session_hash(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(),
        )
        .first()
    )
    if session is None:
        return None
    session.last_seen_at = datetime.now()
    request.state.account_id = session.account_id
    return session.account_id


def revoke_session(request, db: Session) -> bool:
    """撤销当前请求携带的会话。返回是否确实撤销了一条。"""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return False
    updated = (
        db.query(UserSession)
        .filter(
            UserSession.token_hash == session_hash(token),
            UserSession.revoked_at.is_(None),
        )
        .update({UserSession.revoked_at: datetime.now()}, synchronize_session=False)
    )
    db.commit()
    return bool(updated)


def revoke_all_for_owner(db: Session, owner_id: str) -> None:
    """撤销某主体的全部活跃会话（登录/兑换后清旧会话时用）。"""
    db.query(UserSession).filter(
        UserSession.account_id == owner_id,
        UserSession.revoked_at.is_(None),
    ).update({UserSession.revoked_at: datetime.now()}, synchronize_session=False)
