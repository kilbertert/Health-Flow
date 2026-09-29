"""票据 → 本地会话的主体记录。

票据只承载身份（`tenant_id` + `sub`），不承载档案内容。首次见到一个
`(tenant_id, sub)` 时建立本地主体记录；之后同一主体复用同一条记录。

**不引入手机号等外部字段做对齐**：那会依赖我们无法验证的标识——票据里也没有它，
硬凑一个只会把「商城说他是谁」当成我们自己的判断。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.data.models import TicketSubject
from app.service.tickets import TicketClaims

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubjectIdentity:
    """本地主体记录对外的最小形状。"""

    subject_id: str
    tenant_id: str
    external_subject: str
    created: bool

    @property
    def storage_id(self) -> str:
        """该主体在 `owner_id` / 会话里的持久化形状：`account:<tenant>:<sub>`。

        由 `subject_storage_id` 单点构造，不在这里另拼一份——两处拼接必然漂移。
        """
        from app.service.report_ownership import subject_storage_id

        return subject_storage_id(self.tenant_id, self.external_subject)


def _display_name(claims: TicketClaims) -> str:
    """展示名。**不含任何从票据推导出的个人信息**——票里只有标识符。

    刻意不用 `sub` 当展示名：它是个外部标识，把它显示给患者既无意义也泄漏来源。
    """
    del claims
    return "商城用户"


def _identity_of(subject: TicketSubject, *, created: bool) -> SubjectIdentity:
    return SubjectIdentity(
        subject_id=subject.id,
        tenant_id=subject.tenant_id,
        external_subject=subject.external_subject,
        created=created,
    )


def _identity_filter(claims: TicketClaims):
    return (
        TicketSubject.tenant_id == claims.tenant_id,
        TicketSubject.external_subject == claims.subject,
    )


def _existing_subject(db: Session, claims: TicketClaims) -> TicketSubject | None:
    """在**一个新事务**里重读既有行。

    并发落败方需要重读，而入库失败后当前事务已经不可用（失败的那条 INSERT 还在它
    里面），所以这里必须先 `rollback()` 再用一个干净的会话读。用 `_new_session(db)`
    而不是直接 `db.query`，是为了让这条重读路径在测试里也能被驱动到。

    **那个新会话必须关掉。** 它是一次性的，用完就丢；不关的话每次并发落败都漏一个
    连接，反复失败时把连接池耗干——而并发落败恰恰是高峰时才会发生的事。用 `with`
    而不是手动 `close()`：异常路径也要关。
    """
    db.rollback()
    with _new_session(db) as reader:
        return reader.query(TicketSubject).filter(*_identity_filter(claims)).one()


def _new_session(db: Session) -> Session:
    """同一引擎上的新会话。生产上就是普通会话；测试会替换它。"""
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)()


def ensure_subject(db: Session, claims: TicketClaims) -> SubjectIdentity:
    """取得（必要时创建）`(tenant_id, sub)` 对应的本地主体。

    并发下靠唯一约束裁决：两个请求可能同时判定「不存在」，只有数据库能决定谁创建。
    落败的一方重新读取既有行，而不是报错——对调用方来说，两个请求都应当成功，
    且拿到同一个主体。
    """
    existing = db.query(TicketSubject).filter(*_identity_filter(claims)).first()
    if existing is not None:
        return _identity_of(existing, created=False)

    subject = TicketSubject(
        id=str(uuid.uuid4()),
        tenant_id=claims.tenant_id,
        external_subject=claims.subject,
        display_name=_display_name(claims),
        created_at=datetime.now(UTC),
    )
    db.add(subject)
    try:
        db.flush()
    except IntegrityError:
        # 有人抢先创建了。重读并复用**而不是**报错：两个并发请求都应当成功。
        return _identity_of(_existing_subject(db, claims), created=False)
    logger.info("票据首次建立本地主体 tenant=%s", claims.tenant_id)
    return _identity_of(subject, created=True)
