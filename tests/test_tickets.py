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

from app.data.models import Base, TicketRedemption, TicketSubject
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


def test_audience_as_single_element_array_is_accepted():
    """商城侧签出的 `aud` 是数组（hutool 的 setAudience 把每个参数都放进数组）。

    RFC 7519 §4.1.3 明确允许 `aud` 是字符串或字符串数组，两种都要认；
    这条是 2026-10-01 端到端验收撞到的那次：真实票里是 `["health-flow"]`，
    只比字符串会把每一张真票都判成"受众不符"。
    """
    private_key, public_key = _keypair()
    token = _make_ticket(private_key, extra_claims={"aud": [AUDIENCE]})

    claims = verify_ticket(token, public_key=public_key, audience=AUDIENCE)

    assert claims.subject == "mall-user-1"
    # **回填必须是受众标识本身**，不是 `str(["health-flow"])` 那种 Python 字面量——
    # 后者拿去做任何比较都会失败。
    assert claims.audience == AUDIENCE
    assert claims.audience == "health-flow"


def test_audience_array_not_containing_us_is_rejected():
    """数组形式同样要"正好包含本服务"，不是"只要有 aud 就放行"。"""
    private_key, public_key = _keypair()
    token = _make_ticket(private_key, extra_claims={"aud": ["some-other-service", "another"]})

    with pytest.raises(TicketError, match="受众"):
        verify_ticket(token, public_key=public_key, audience=AUDIENCE)


def test_audience_of_wrong_type_is_rejected():
    """既不是字符串也不是字符串数组的 `aud` 一律拒，不做类型强转。"""
    private_key, public_key = _keypair()
    token = _make_ticket(private_key, extra_claims={"aud": [123]})

    with pytest.raises(TicketError, match="格式"):
        verify_ticket(token, public_key=public_key, audience=AUDIENCE)


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


def _settings(path: str = "", audience: str = "", max_ttl: int = 600) -> SimpleNamespace:
    return SimpleNamespace(
        MALL_TICKET_PUBLIC_KEY_PATH=path,
        MALL_TICKET_AUDIENCE=audience,
        MALL_TICKET_MAX_TTL_SECONDS=max_ttl,
    )


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


# --- #171 review：兑换入口的安全不变量 -----------------------------------------


def test_ticket_lifetime_has_an_upper_bound():
    """票面写的是 120 秒。若验签方不设上界，「短有效期」在代码里就没有对应物——
    一张 `exp` 在三年后的票据会被一路接受，而它的重放窗口就是三年。"""
    private_key, public_key = _keypair()
    now = datetime.now(UTC)
    token = _make_ticket(
        private_key,
        issued_at=now,
        expires_at=now + timedelta(days=365 * 3),
    )

    with pytest.raises(TicketError, match="寿命"):
        verify_ticket(token, public_key=public_key, audience=AUDIENCE)


def test_ordinary_short_ticket_is_within_the_bound():
    """另一半：正常票据不能撞到上界，否则这道检查会变成误伤。"""
    private_key, public_key = _keypair()
    assert verify_ticket(_make_ticket(private_key), public_key=public_key, audience=AUDIENCE).jti


def test_redeem_commits_so_a_later_rollback_cannot_unspend_the_ticket(db):
    """**这是本 PR 最值得留档的一条不变量。**

    兑换之后调用方还要建主体，而那条路径在并发落败时会 `rollback()`。如果消费只停在
    `flush()`，那次回滚会连同消费行一起抹掉——`jti` 唯一约束防的重放，被同一个事务里
    的另一个回滚撤销。所以 `redeem` 必须**提交**。

    这里直接演示差别：兑换后回滚当前会话，再用**新会话**看消费行还在不在。
    """
    private_key, public_key = _keypair()
    verifier = TicketVerifier(public_key=public_key, audience=AUDIENCE)
    token = _make_ticket(private_key)

    verifier.redeem(db, token)
    db.rollback()  # 模拟调用方后续的并发落败回滚

    with sessionmaker(bind=db.get_bind(), expire_on_commit=False)() as fresh:
        assert fresh.query(TicketRedemption).count() == 1
        with pytest.raises(TicketError, match="已被使用"):
            verifier.redeem(fresh, token)


def test_same_ticket_redeems_only_once_on_the_full_entry_path(db):
    """在**端到端入口**（兑换 → 建主体）上断言一次性，而不只是 `redeem` 单独调用。

    路径里多了一个会 `rollback()` 的主体创建，所以「只成功一次」必须在组合之后仍然成立。
    """
    private_key, public_key = _keypair()
    verifier = TicketVerifier(public_key=public_key, audience=AUDIENCE)
    token = _make_ticket(private_key)

    first = verifier.redeem_and_ensure_subject(db, token)
    assert first.created is True

    with pytest.raises(TicketError, match="已被使用"):
        verifier.redeem_and_ensure_subject(db, token)
    with sessionmaker(bind=db.get_bind(), expire_on_commit=False)() as fresh:
        assert fresh.query(TicketSubject).count() == 1


def test_full_entry_path_survives_a_lost_race_without_respending(db):
    """并发场景下走完整入口：落败方的回滚**不能**让票据重新可用。

    两个请求同时到达时，一个建成主体、另一个走恢复路径。关键是第二个请求结束之后，
    票据仍然是「已消费」的。
    """
    private_key, public_key = _keypair()
    verifier = TicketVerifier(public_key=public_key, audience=AUDIENCE)
    token = _make_ticket(private_key)

    # 第一个请求正常完成。
    verifier.redeem_and_ensure_subject(db, token)

    # 第二个请求（另一会话）必须被拒，且这不取决于它是否能读到主体表。
    other = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    try:
        with pytest.raises(TicketError, match="已被使用"):
            verifier.redeem_and_ensure_subject(other, token)
    finally:
        other.close()


def test_reader_session_is_closed_after_a_lost_race(db):
    """并发落败时开的那个一次性读会话必须被关闭——不关就漏连接，反复失败会耗干池。"""
    private_key, public_key = _keypair()
    claims = verify_ticket(_make_ticket(private_key), public_key=public_key, audience=AUDIENCE)

    # 让那行先由另一个会话提交，再把本会话的首次读取弄瞎（真实竞态的形状）。
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

    closed: list[bool] = []

    class _TrackedSession(_Session):
        def close(self):
            closed.append(True)
            return super().close()

    real_query = _Session.query
    state = {"blinded": False}

    def blinded_query(self, *args, **kwargs):
        query = real_query(self, *args, **kwargs)
        if self is db and not state["blinded"]:
            state["blinded"] = True
            return _BlindFirst(query)
        return query

    def tracked_new_session(inner):
        return _TrackedSession(bind=inner.get_bind(), expire_on_commit=False)

    with (
        patch.object(_Session, "query", blinded_query),
        patch("app.service.ticket_sessions._new_session", side_effect=tracked_new_session),
    ):
        identity = ensure_subject(db, claims)

    assert identity.subject_id == "winner"
    assert closed, "并发落败时打开的读会话没有被关闭"
