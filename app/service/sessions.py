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


def issue_session(owner_id: str, *, hours: int = 12) -> tuple[str, UserSession]:
    """为该主体签发一次浏览器会话。浏览器只拿到随机 token，库里存哈希。

    参数名从 `account_id` 改为 `owner_id`：它现在装的是主体的**存储标识**
    （`account:<tenant>:<sub>`），而不再是「账号表的主键」。列名仍是 `account_id`
    ——它是已存数据的形状，改名属于迁移而不是本票的事。

    **有效期 12 小时，Cookie 与 Bearer 共用。** 票据兑换既写 Cookie 也在响应体里给令牌，
    但两者指向**同一条**会话行（ADR 0026），所以有效期只有一处可改。旧通道（Cookie）
    此前是 30 天；它没有真实患者，与新形态一起收到 12 小时，不维护第二种寿命。
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    session = UserSession(
        account_id=owner_id,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=hours),
    )
    return token, session


def request_session_token(request) -> str:
    """**读取会话令牌的唯一入口**：先看 `Authorization: Bearer`，再看 Cookie。

    商城 H5 在另一个源上（ADR 0026），浏览器不会替它带上本仓的 Cookie，所以令牌由
    模块显式放进 `Authorization` 头；旧前端仍然靠 Cookie。两条路指向同一条服务端会话行，
    所以「令牌从哪来」只该有一个答案，而「令牌有没有效」只该有一处判定。

    两处都改为调用它（`session_owner_id` 与 `revoke_session`）之前，各自从 Cookie 里取
    一次 —— 同一条读取规则的两个副本，下一个加 Bearer 的人只会想起其中一处，
    于是「退出登录对 Bearer 不生效」这类缺陷会以「某条路径忘了」的形状出现。

    Basic Auth 的 `Authorization` 头不会被当成会话：本服务只有在运维闸门启用时才接受
    `Basic`，而 patient 会话是 `Bearer`（ADR 0026）。
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.casefold() == "bearer" and token.strip():
        return token.strip()
    return request.cookies.get(SESSION_COOKIE, "")


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_owner_id(request, db: Session) -> str | None:
    """当前请求的**会话主体存储标识**，没有有效会话时返回 None。

    **它不查账号表。** 会话行里的 `account_id` 现在直接就是主体标识
    （`account:<tenant>:<sub>`），所以「会话 → 主体」是一次直接读取，不需要
    「会话 → 账号 → 主体」这条两步链——后者是账号体系还在时的形状，留着它
    等于让退役后的代码继续假设账号表存在。
    """
    token = request_session_token(request)
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
    # **主体被停用后，它的会话必须立刻失效。** 只校验会话本身不够：`TicketSubject`
    # 上有一个 `is_active`，而停用它的理由（风控、注销、误建）恰恰要求「当前这次
    # 访问就不该继续」。这里解析出主体标识后回查一次主体记录；查不到就按不可用处理
    # ——票据主体记录是身份的唯一来源，读不到它意味着这条会话没有可验证的主体。
    if not _subject_is_active(db, session.account_id):
        return None
    session.last_seen_at = datetime.now()
    request.state.account_id = session.account_id
    return session.account_id


def _subject_is_active(db: Session, storage_id: str) -> bool:
    """该主体标识对应的主体是否仍然可用。

    只有票据主体的形状（`account:<tenant>:<sub>`）能查到记录；退役期的历史会话
    （`account:<uuid>`）没有主体行，那是既有的、只读的历史身份，**不因查不到而失效**
    ——否则所有历史会话会在这次改动后同时断掉，而票面只授权退役、未授权清空历史。
    """
    parts = storage_id.split(":", 2)
    if len(parts) != 3 or parts[0] != "account":
        return True
    _, tenant_id, external_subject = parts
    from app.data.models import TicketSubject

    subject = (
        db.query(TicketSubject)
        .filter(
            TicketSubject.tenant_id == tenant_id,
            TicketSubject.external_subject == external_subject,
        )
        .first()
    )
    if subject is None:
        return False
    return bool(subject.is_active)


def revoke_session(request, db: Session) -> bool:
    """撤销当前请求携带的会话。返回是否确实撤销了一条。

    令牌来源走 `request_session_token`，所以**退出登录对 Bearer 同样生效** ——
    Cookie 与 Bearer 是同一条会话行（ADR 0026）。
    """
    token = request_session_token(request)
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
