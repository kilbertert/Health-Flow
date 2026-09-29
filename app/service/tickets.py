"""商城登录票据的离线验签与一次性消费。

本平台接受商城签发的一次性登录票据，在**本地**验签后换取本地会话。这条边界是必须
的：核查发现商城大部分服务端鉴权处于被注释或未启用状态，因此**不得**以「商城网关
已经校验过」为前提（genesis-evidence #171）。

票据是 RS256 JWT，公钥由商城侧离线交付。**公钥不可用时拒绝启动**——不静默降级为
不验签；一个「验不了签但照常放行」的配置，比没有这道门更危险，因为它看起来是有的。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.data.models import TicketRedemption

if TYPE_CHECKING:
    # 只为注解：`ticket_sessions` 反过来 import 本模块的 `TicketClaims`，顶层导入会成环。
    from app.service.ticket_sessions import SubjectIdentity

logger = logging.getLogger(__name__)

#: 允许的时钟偏移。票据 `exp` 只有 120 秒，两端机器的时间不会正好一致；
#: 60 秒既能容忍常见的 NTP 偏移，又不会显著扩大重放窗口（相对 120 秒的票面寿命
#: 至多让它多活一半）。取整一分钟便于运维解释「为什么刚过期的票还能用」。
CLOCK_SKEW = timedelta(seconds=60)

#: 票据寿命上限。票面约定 120 秒；这里取 10 分钟作为**验签方的强制**上界——
#: 它比约定宽，所以正常的 120 秒票据永远不会撞到它，但一张 `exp` 在三年后的
#: 票据会被拒。补这一步是因为「短有效期」若只写在票面上，代码里就没有对应物。
DEFAULT_MAX_TTL_SECONDS = 600

_ALGORITHM = "RS256"


class TicketError(RuntimeError):
    """票据不可接受。消息面向运维日志，不面向患者。"""


@dataclass(frozen=True)
class TicketClaims:
    """验签通过后的票据载荷。字段名与商城签发的 claim 对齐。"""

    jti: str
    subject: str
    tenant_id: str
    audience: str
    issued_at: datetime
    expires_at: datetime


def _b64url_decode(segment: str) -> bytes:
    padding_needed = (-len(segment)) % 4
    try:
        return base64.urlsafe_b64decode(segment + "=" * padding_needed)
    except (binascii.Error, ValueError) as exc:
        raise TicketError("票据不是合法的 base64url 段") from exc


def _load_public_key(public_key_pem: str) -> rsa.RSAPublicKey:
    try:
        key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise TicketError("公钥无法解析") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise TicketError("公钥不是 RSA 公钥")
    return key


def load_public_key_from_file(path: str) -> rsa.RSAPublicKey:
    """从文件加载公钥。启动期调用——失败即拒绝启动，不做降级。"""
    key_path = Path(path).expanduser()
    if not key_path.is_file():
        raise TicketError(f"票据公钥文件不存在：{key_path}")
    return _load_public_key(key_path.read_text(encoding="utf-8"))


def verify_ticket(
    token: str,
    *,
    public_key: rsa.RSAPublicKey,
    audience: str,
    max_ttl_seconds: int = DEFAULT_MAX_TTL_SECONDS,
    now: datetime | None = None,
) -> TicketClaims:
    """验签并校验标准 claim。任何一项不满足都抛 `TicketError`。

    `aud` 与**一个明确的配置值**比对，而不是「是合法的 aud 就行」——后者的代码里
    没有任何东西对应「受众被钉死」这条要求，等于没做。
    """
    now = now or datetime.now(UTC)
    parts = token.split(".")
    if len(parts) != 3:
        raise TicketError("票据不是三段式 JWT")
    header_segment, payload_segment, signature_segment = parts

    try:
        header = json.loads(_b64url_decode(header_segment))
    except json.JSONDecodeError as exc:
        raise TicketError("票据头部不是 JSON") from exc
    if not isinstance(header, dict) or header.get("alg") != _ALGORITHM:
        # 只接受 RS256。接受 `alg` 来自票据本身（尤其是 none/HS256）是经典的
        # 算法混淆漏洞，所以这里取白名单而不是「按头部声明去验」。
        raise TicketError("票据算法不是 RS256")

    try:
        public_key.verify(
            _b64url_decode(signature_segment),
            f"{header_segment}.{payload_segment}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise TicketError("票据签名不匹配") from exc

    try:
        payload = json.loads(_b64url_decode(payload_segment))
    except json.JSONDecodeError as exc:
        raise TicketError("票据载荷不是 JSON") from exc
    if not isinstance(payload, dict):
        raise TicketError("票据载荷不是对象")

    if payload.get("aud") != audience:
        raise TicketError("票据受众与本服务不符")

    for name in ("sub", "tenant_id", "jti", "exp", "iat"):
        if not str(payload.get(name) or "").strip():
            raise TicketError(f"票据缺少 {name}")

    try:
        issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise TicketError("票据时间字段不是合法的时间戳") from exc

    if now > expires_at + CLOCK_SKEW:
        raise TicketError("票据已过期")
    if now < issued_at - CLOCK_SKEW:
        raise TicketError("票据尚未生效")

    # 短有效期不只是签发方的承诺，也是验签方的强制：票面 AC 写的是 120 秒，若这里
    # 不设上界，「票面寿命」在代码里就没有对应物——一张 `exp` 在三年后的票据会被
    # 一路接受，而它的重放窗口就是三年。
    if expires_at - issued_at > timedelta(seconds=max_ttl_seconds) + CLOCK_SKEW:
        raise TicketError("票据寿命超出上限")

    return TicketClaims(
        jti=str(payload["jti"]),
        subject=str(payload["sub"]),
        tenant_id=str(payload["tenant_id"]),
        audience=str(payload["aud"]),
        issued_at=issued_at,
        expires_at=expires_at,
    )


class TicketVerifier:
    """验签器：持有公钥与受众配置，并负责 `jti` 的一次性消费。

    构造即代表「已配置」；未配置时调用方应拒绝启动，而不是构造一个空验签器。
    """

    def __init__(
        self,
        *,
        public_key: rsa.RSAPublicKey,
        audience: str,
        max_ttl_seconds: int = DEFAULT_MAX_TTL_SECONDS,
    ) -> None:
        if not audience.strip():
            raise TicketError("票据受众未配置")
        self._public_key = public_key
        self._audience = audience.strip()
        self._max_ttl_seconds = max_ttl_seconds

    @property
    def audience(self) -> str:
        return self._audience

    def verify(self, token: str, *, now: datetime | None = None) -> TicketClaims:
        return verify_ticket(
            token,
            public_key=self._public_key,
            audience=self._audience,
            max_ttl_seconds=self._max_ttl_seconds,
            now=now,
        )

    def redeem(self, db: Session, token: str, *, now: datetime | None = None) -> TicketClaims:
        """验签 + **提交**消费 `jti`。同一票据第二次调用必被拒。

        原子性靠**唯一约束**，不靠「先查再写」：两条并发请求可能同时通过检查、
        再各自插入。这里先插入、让数据库裁决，`IntegrityError` 即「已被用过」。

        **消费必须提交，不能只 flush。** 调用方在兑换之后还要建主体记录，而那条
        路径在并发落败时会 `rollback()`——如果消费只停在 flush，那次 rollback 会
        连同消费行一起抹掉，于是同一张票可以被兑换第二次：`jti` 唯一约束防的事，
        被同一个事务里的另一个回滚撤销。提交把消费落到一个**不会被后续回滚波及**
        的事务边界上；代价是「票据被用过但主体没建成」时那张票不能再用——这是
        正确的方向（宁可让用户重新跳一次，也不能让票据可重放）。
        """
        claims = self.verify(token, now=now)
        record = TicketRedemption(
            jti=claims.jti,
            tenant_id=claims.tenant_id,
            subject=claims.subject,
            redeemed_at=now or datetime.now(UTC),
            # 保留到 `exp` 即够：`exp` 已经封死重放窗口，更长保留没有安全收益。
            purge_after=claims.expires_at + CLOCK_SKEW,
        )
        db.add(record)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise TicketError("票据已被使用") from exc
        return claims

    def redeem_and_ensure_subject(
        self,
        db: Session,
        token: str,
        *,
        now: datetime | None = None,
    ) -> SubjectIdentity:
        """兑换一张票据并取得本地主体：**兑换入口的完整路径**。

        顺序是安全的：`redeem` 先**提交**消费，`ensure_subject` 才在之后做它自己的
        读-写（含并发落败时的 `rollback`）。那次回滚只影响主体创建这一段事务，
        碰不到已经提交的消费行——所以并发下同一张票只有一个请求能走到这里。

        这个函数是本票唯一的生产调用方：它让「消费先落地」成为路径的一部分，
        而不是留给将来接入的人按注释去猜顺序。
        """
        from app.service.ticket_sessions import ensure_subject

        claims = self.redeem(db, token, now=now)
        # 顺带清理过期消费行：这张表会无界增长，而「以后接周期清理」在实践中就是
        # 「永远不清理」。索引在 `purge_after` 上，没东西可清时是一条廉价的无匹配 DELETE。
        self.purge_spent_redemptions(db, now=now)
        identity = ensure_subject(db, claims)
        # 主体也要**提交**：入口路径返回之后调用方就拿不到这个会话了，停在 flush
        # 等于让「建好的主体」随会话关闭一起消失，而下一次同一张票早就被消费掉了
        # ——那会把用户卡在「票没了、主体也没建成」。
        db.commit()
        return identity

    def purge_spent_redemptions(self, db: Session, *, now: datetime | None = None) -> int:
        """在兑换路径上顺带清理过期消费行。

        留一个真实调用点，是因为「以后接周期清理」在实践中就是「永远不清理」——
        这张表会无界增长。
        """
        return purge_expired_redemptions(db, now=now)


def purge_expired_redemptions(db: Session, *, now: datetime | None = None) -> int:
    """清理已过期的消费记录。保留期到 `exp` 即清。"""
    now = now or datetime.now(UTC)
    removed = (
        db.query(TicketRedemption)
        .filter(TicketRedemption.purge_after <= now)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(removed)


def build_verifier(settings: Settings | None = None) -> TicketVerifier:
    """按配置构造验签器。**配置不全即抛错**，由启动路径转成拒绝启动。"""
    settings = settings or get_settings()
    if not settings.MALL_TICKET_PUBLIC_KEY_PATH.strip():
        raise TicketError("未配置票据公钥路径")
    if not settings.MALL_TICKET_AUDIENCE.strip():
        raise TicketError("未配置票据受众")
    return TicketVerifier(
        public_key=load_public_key_from_file(settings.MALL_TICKET_PUBLIC_KEY_PATH),
        audience=settings.MALL_TICKET_AUDIENCE,
        max_ttl_seconds=settings.MALL_TICKET_MAX_TTL_SECONDS,
    )


def describe_ticket_for_audit(token: str) -> str:
    """审计用的凭据标识：**票据摘要，不是票据本身**。

    票据在有效期内等同凭据——把它整份写进日志或审计表，等于把凭据在另一个地方
    复制了一份。审计要回答的是「我们收到的是哪一张、验签结果如何」，摘要足以回答，
    且泄漏后无法用来兑换。`jti` 已经单独记录，摘要只是让「收到的原始凭据」可复核。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "CLOCK_SKEW",
    "DEFAULT_MAX_TTL_SECONDS",
    "TicketClaims",
    "TicketError",
    "TicketVerifier",
    "build_verifier",
    "describe_ticket_for_audit",
    "load_public_key_from_file",
    "purge_expired_redemptions",
    "verify_ticket",
]
