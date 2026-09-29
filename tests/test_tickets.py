"""#171 商城登录票据：离线验签、`jti` 一次性、主体记录、fail-closed 启动。

边界：核查发现商城大部分服务端鉴权处于被注释或未启用状态，因此本平台一侧**必须离线
验签**，不得以「商城网关已经校验过」为前提。这里的公钥全部是**自造密钥对**——
`#168` 交付的真实公钥尚未到手，故「用商城侧真实公钥跑一次」是本票**明说的未验证项**。
"""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base, TicketSubject
from app.service.ticket_sessions import ensure_subject
from app.service.tickets import (
    CLOCK_SKEW,
    TicketError,
    TicketVerifier,
    build_verifier,
    describe_ticket_for_audit,
    purge_expired_redemptions,
    verify_ticket,
)

AUDIENCE = "health-flow"
TENANT = "1578664130444005376"


def _keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_ticket(
    private_key: rsa.RSAPrivateKey,
    *,
    audience: str = AUDIENCE,
    sub: str = "mall-user-1",
    tenant_id: str = TENANT,
    jti: str = "jti-1",
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    sign_with: rsa.RSAPrivateKey | None = None,
    extra_claims: dict | None = None,
) -> str:
    now = datetime.now(UTC)
    issued_at = issued_at or now
    expires_at = expires_at or now + timedelta(seconds=120)
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iss": "qumall",
        "aud": audience,
        "sub": sub,
        "tenant_id": tenant_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": jti,
    }
    payload.update(extra_claims or {})
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(payload).encode())}"
    signature = (sign_with or private_key).sign(
        signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )
    return f"{signing_input}.{_b64url(signature)}"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_valid_ticket_yields_claims():
    private_key, public_key = _keypair()
    claims = verify_ticket(_make_ticket(private_key), public_key=public_key, audience=AUDIENCE)

    assert claims.jti == "jti-1"
    assert claims.subject == "mall-user-1"
    assert claims.tenant_id == TENANT


def test_audience_mismatch_is_rejected():
    """`aud` 与**一个明确的配置值**比对——不是「合法 aud 即可」。"""
    private_key, public_key = _keypair()
    token = _make_ticket(private_key, audience="some-other-service")

    with pytest.raises(TicketError, match="受众"):
        verify_ticket(token, public_key=public_key, audience=AUDIENCE)


def test_expired_ticket_is_rejected():
    private_key, public_key = _keypair()
    now = datetime.now(UTC)
    token = _make_ticket(
        private_key,
        issued_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=CLOCK_SKEW.total_seconds() + 5),
    )

    with pytest.raises(TicketError, match="已过期"):
        verify_ticket(token, public_key=public_key, audience=AUDIENCE)


def test_ticket_within_clock_skew_is_accepted():
    """允许的偏移是具名常量，不是"随便给一点"——这里钉住它的实际效果。"""
    private_key, public_key = _keypair()
    now = datetime.now(UTC)
    token = _make_ticket(
        private_key,
        issued_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=CLOCK_SKEW.total_seconds() - 5),
    )

    assert verify_ticket(token, public_key=public_key, audience=AUDIENCE).jti == "jti-1"


def test_signature_from_another_key_is_rejected():
    private_key, public_key = _keypair()
    other_private, _ = _keypair()
    token = _make_ticket(private_key, sign_with=other_private)

    with pytest.raises(TicketError, match="签名"):
        verify_ticket(token, public_key=public_key, audience=AUDIENCE)


def test_unknown_key_is_rejected():
    """未知密钥（公钥换过、票据来自别的签发方）一律拒绝，不尝试其他密钥。"""
    _, public_key = _keypair()
    stranger_private, _ = _keypair()

    with pytest.raises(TicketError, match="签名"):
        verify_ticket(_make_ticket(stranger_private), public_key=public_key, audience=AUDIENCE)


def test_non_rs256_algorithm_is_rejected():
    """`alg` 取自票据本身时接受任何值是算法混淆；这里取白名单。"""
    private_key, public_key = _keypair()
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"aud": AUDIENCE, "sub": "s", "tenant_id": "t", "jti": "j",
                                  "iat": 1, "exp": 2}).encode())

    with pytest.raises(TicketError, match="RS256"):
        verify_ticket(f"{header}.{payload}.", public_key=public_key, audience=AUDIENCE)


def test_missing_claim_is_rejected():
    """缺 claim 的票据即使签名合法也必须拒——逐项校验不能靠"验签过了就都行"。"""
    private_key, public_key = _keypair()
    now = datetime.now(UTC)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = {
        "aud": AUDIENCE,
        "sub": "mall-user-1",
        "tenant_id": TENANT,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=120)).timestamp()),
        # 刻意不写 jti
    }
    signing_input = f"{header}.{_b64url(json.dumps(payload).encode())}"
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    token = f"{signing_input}.{_b64url(signature)}"

    with pytest.raises(TicketError, match="jti"):
        verify_ticket(token, public_key=public_key, audience=AUDIENCE)


def test_same_jti_cannot_be_redeemed_twice(db):
    private_key, public_key = _keypair()
    verifier = TicketVerifier(public_key=public_key, audience=AUDIENCE)
    token = _make_ticket(private_key)

    assert verifier.redeem(db, token).jti == "jti-1"
    with pytest.raises(TicketError, match="已被使用"):
        verifier.redeem(db, token)


def test_jti_uniqueness_is_enforced_by_the_database_not_by_a_read(db):
    """原子性靠唯一约束，不靠「先查再写」——这里直接撞唯一约束验证它存在。

    查-再-写在任何并发下都是错的：两条请求可能同时通过检查再各自插入。
    """
    _, public_key = _keypair()
    verifier = TicketVerifier(public_key=public_key, audience=AUDIENCE)
    del verifier  # 这条用例只测约束本身
    _first, public_key_first = _keypair()

    token = _make_ticket(_keypair()[0])
    claims = verify_ticket(token, public_key=public_key_first, audience=AUDIENCE) if False else None
    del claims
    # 直接插入两行同一 jti，第二行必须被数据库拒绝。
    from app.data.models import TicketRedemption

    now = datetime.now(UTC)
    db.add(TicketRedemption(jti="dup", tenant_id=TENANT, subject="s", redeemed_at=now, purge_after=now))
    db.flush()
    db.add(TicketRedemption(jti="dup", tenant_id=TENANT, subject="s", redeemed_at=now, purge_after=now))
    with pytest.raises(IntegrityError):
        db.flush()


def test_redemption_records_are_purged_at_expiry(db):
    """保留期到 `exp` 即清——更长保留没有安全收益。"""
    private_key, public_key = _keypair()
    verifier = TicketVerifier(public_key=public_key, audience=AUDIENCE)
    now = datetime.now(UTC)
    token = _make_ticket(private_key, issued_at=now, expires_at=now + timedelta(seconds=120))
    verifier.redeem(db, token, now=now)

    assert purge_expired_redemptions(db, now=now) == 0
    assert purge_expired_redemptions(db, now=now + timedelta(seconds=120) + CLOCK_SKEW) == 1


def test_audit_identifier_is_a_digest_not_the_ticket():
    """审计记摘要不记票据：票据在有效期内等同凭据，整份入库等于复制凭据。"""
    private_key, _ = _keypair()
    token = _make_ticket(private_key)
    digest = describe_ticket_for_audit(token)

    assert digest != token
    assert token not in digest
    assert len(digest) == 64


def test_first_sighting_creates_subject_without_password(db):
    private_key, public_key = _keypair()
    claims = verify_ticket(_make_ticket(private_key), public_key=public_key, audience=AUDIENCE)

    first = ensure_subject(db, claims)
    second = ensure_subject(db, claims)

    assert first.created is True
    assert second.created is False
    assert first.subject_id == second.subject_id
    subject = db.query(TicketSubject).one()
    assert subject.external_subject == "mall-user-1"
    # 无密码、无邮箱：`TicketSubject` 上没有这些列，只能由票据创建。
    assert not hasattr(subject, "password_hash")
    assert not hasattr(subject, "email")


def test_same_subject_in_another_tenant_is_a_different_subject(db):
    private_key, public_key = _keypair()
    claims_a = verify_ticket(
        _make_ticket(private_key, tenant_id="tenant-a"), public_key=public_key, audience=AUDIENCE
    )
    claims_b = verify_ticket(
        _make_ticket(private_key, tenant_id="tenant-b", jti="jti-2"),
        public_key=public_key,
        audience=AUDIENCE,
    )

    assert ensure_subject(db, claims_a).subject_id != ensure_subject(db, claims_b).subject_id


class _BlindFirst:
    """把链式查询的 `.first()` 变成"看不见"，其余方法原样代理。

    用来构造真实竞态的那一半：**读的时候那行还不存在**。
    """

    def __init__(self, inner):
        self._inner = inner

    def filter(self, *args, **kwargs):
        return _BlindFirst(self._inner.filter(*args, **kwargs))

    def first(self):
        return None

    def one(self):
        return self._inner.one()


def test_subject_creation_survives_a_lost_race(db):
    """并发下两个请求都该成功并拿到同一主体，而不是一个报错。

    真实竞态的形状：本会话**读到「不存在」之后、插入之前**，另一个会话插入了同一主体。
    这里让那行先由另一个会话提交（数据库里确实有它），再把本会话的首次读取弄瞎，
    于是它必定撞上唯一约束、走恢复路径。
    """
    private_key, public_key = _keypair()
    claims = verify_ticket(_make_ticket(private_key), public_key=public_key, audience=AUDIENCE)

    other = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    other.add(
        TicketSubject(
            id="winner",
            tenant_id=claims.tenant_id,
            external_subject=claims.subject,
            display_name="商城用户",
            created_at=datetime.now(UTC),
        )
    )
    other.commit()
    other.close()

    from sqlalchemy.orm import Session as _Session

    real_query = _Session.query
    state = {"blinded": False}

    def blinded_query(self, *args, **kwargs):
        query = real_query(self, *args, **kwargs)
        if self is db and not state["blinded"]:
            state["blinded"] = True
            return _BlindFirst(query)
        return query

    with patch.object(_Session, "query", blinded_query):
        identity = ensure_subject(db, claims)

    assert identity.created is False
    assert identity.subject_id == "winner"
    assert db.query(TicketSubject).count() == 1


# --- fail-closed 启动：双向测 ------------------------------------------------


def _settings(path: str = "", audience: str = "") -> SimpleNamespace:
    return SimpleNamespace(MALL_TICKET_PUBLIC_KEY_PATH=path, MALL_TICKET_AUDIENCE=audience)


def test_verifier_is_not_built_without_a_public_key(tmp_path):
    with pytest.raises(TicketError, match="公钥路径"):
        build_verifier(_settings(audience=AUDIENCE))


def test_verifier_is_not_built_without_an_audience(tmp_path):
    private_key, _ = _keypair()
    path = tmp_path / "ticket.pub"
    path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    with pytest.raises(TicketError, match="受众"):
        build_verifier(_settings(path=str(path)))


def test_verifier_is_not_built_from_an_unparsable_public_key(tmp_path):
    path = tmp_path / "ticket.pub"
    path.write_text("not a pem")

    with pytest.raises(TicketError, match="无法解析"):
        build_verifier(_settings(path=str(path), audience=AUDIENCE))


def test_verifier_is_not_built_from_a_missing_file(tmp_path):
    with pytest.raises(TicketError, match="不存在"):
        build_verifier(_settings(path=str(tmp_path / "nope.pub"), audience=AUDIENCE))


def test_configured_verifier_verifies(tmp_path):
    """另一半：配齐之后**确实能验签**。只测「没配就别启动」证明不了这道门是通的。"""
    private_key, _ = _keypair()
    path = tmp_path / "ticket.pub"
    path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    verifier = build_verifier(_settings(path=str(path), audience=AUDIENCE))

    assert verifier.verify(_make_ticket(private_key)).subject == "mall-user-1"


def test_application_refuses_to_start_without_ticket_configuration():
    """服务启动路径必须拒绝，而不是启动一个不验签的服务。"""
    from app.main import app, lifespan

    async def boot():
        async with lifespan(app):
            return "booted"

    with pytest.raises(RuntimeError, match="拒绝启动"):
        asyncio.run(boot())
