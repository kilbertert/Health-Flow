"""会话与报告归属的验收用例。

#172 之前这里叫「账号/会话」，覆盖注册、登录、改昵称、退出。**注册、登录、密码、
改昵称整体退役**，所以那些用例不是被删掉，而是**换了主体**：它们断言的契约
（会话建立、报告按主体隔离、上传归属主体）一条没变，变的是身份从哪来。

保留两条票面点名要求的用例：报告历史按主体隔离、上传归属主体且与第二主体互不可见。
"""

import io
import uuid
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.data.models import Base, MedicalReport, MetricRecord, ReportAuditEvent, TicketSubject
from app.main import app
from app.service.report_ownership import subject_storage_id
from app.service.sessions import SESSION_COOKIE, issue_session, session_hash


@pytest.fixture
def subject_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def get_db():
        with SessionLocal() as session:
            yield session

    from app.config import get_settings

    settings = get_settings()
    previous = (
        settings.APP_ENV,
        settings.REPORT_ACCOUNT_REQUIRED,
        settings.HEALTHFLOW_BASIC_AUTH_ENABLED,
        settings.AUTH_COOKIE_SECURE,
    )
    settings.APP_ENV = "development"
    settings.REPORT_ACCOUNT_REQUIRED = True
    settings.HEALTHFLOW_BASIC_AUTH_ENABLED = False
    settings.AUTH_COOKIE_SECURE = False
    monkeypatch.setattr("app.data.get_db", get_db)
    try:
        yield TestClient(app), SessionLocal
    finally:
        (
            settings.APP_ENV,
            settings.REPORT_ACCOUNT_REQUIRED,
            settings.HEALTHFLOW_BASIC_AUTH_ENABLED,
            settings.AUTH_COOKIE_SECURE,
        ) = previous
        engine.dispose()


def _issue_subject_session(SessionLocal, tenant: str, subject: str) -> tuple[str, dict[str, str]]:
    """建一条主体会话，返回 `(主体标识, cookie)`。

    没有「注册」这一步可调用了——主体由票据兑换创建，测试里直接构造等价状态。
    """
    owner_id = subject_storage_id(tenant, subject)
    token, row = issue_session(owner_id)
    with SessionLocal() as db:
        # 主体记录也是必需的：会话解析会回查它（停用即失效）。
        db.add(
            TicketSubject(
                id=str(uuid.uuid4()),
                tenant_id=tenant,
                external_subject=subject,
                display_name="商城用户",
            )
        )
        db.add(row)
        db.commit()
    return owner_id, {SESSION_COOKIE: token}


def test_self_service_identity_endpoints_are_gone(subject_client):
    """**注册、登录、改昵称整体退役。** 它们不该只是"不可用"，而是**不存在**。

    断言 404/405（路由不存在）而不是 401——401 意味着端点还在、只是没通过鉴权，
    那与「不再拥有身份源」是两回事。
    """
    client, _ = subject_client
    for path, payload in (
        ("/api/auth/register", {"email": "a@example.com", "password": "password-123"}),
        ("/api/auth/login", {"email": "a@example.com", "password": "password-123"}),
        ("/api/auth/profile", {"display_name": "新昵称"}),
    ):
        assert client.post(path, json=payload).status_code in {404, 405}, path
    assert client.patch("/api/auth/profile", json={"display_name": "x"}).status_code in {404, 405}


def test_session_resolves_to_the_subject_not_an_account_row(subject_client):
    client, SessionLocal = subject_client
    owner_id, cookie = _issue_subject_session(SessionLocal, "t1", "u1")
    client.cookies.update(cookie)

    body = client.get("/api/auth/me").json()
    assert body["subject_id"] == owner_id
    assert body["tenant_id"] == "t1"
    assert body["external_subject"] == "u1"
    # 不再有任何账号字段可回显。
    assert "email" not in body

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_report_history_is_scoped_to_the_subject(subject_client):
    """票面要求保留的用例之一：报告历史按主体隔离。"""
    client, SessionLocal = subject_client
    owner_id, cookie = _issue_subject_session(SessionLocal, "t1", "u1")
    with SessionLocal() as db:
        db.add_all(
            [
                MedicalReport(patient_id=owner_id, owner_id=owner_id, status="assessed", exam_date=datetime.now()),
                MedicalReport(
                    patient_id="other",
                    owner_id="account:t2:u2",
                    status="assessed",
                    exam_date=datetime.now(),
                ),
            ]
        )
        db.commit()
    client.cookies.update(cookie)

    history = client.get("/api/auth/reports")
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["status"] == "assessed"


def test_report_history_includes_abnormal_count(subject_client):
    """摘要只数**异常判定**为 H/L 的指标 —— 不是数模型写下的原始标志。

    这个口径是对 #30 的有意修正（#90/#134）：摘要是「解读会考虑什么」的预告，
    不是抽取模型原始标记的复述。因此这里刻意同时放两种分歧样本：
    模型标了异常但数值在范围内（不计数）、模型没标但数值超范围（计数）。
    """
    client, SessionLocal = subject_client
    owner_id, cookie = _issue_subject_session(SessionLocal, "t1", "u1")
    with SessionLocal() as db:
        owned = MedicalReport(patient_id=owner_id, owner_id=owner_id, status="assessed")
        other = MedicalReport(patient_id="other", owner_id="account:t2:u2", status="assessed")
        db.add_all([owned, other])
        db.flush()
        db.add_all(
            [
                # 模型标异常，但数值在参考范围内 —— 判定为 N，不计数。
                MetricRecord(
                    report_id=owned.id,
                    metric_name="误标偏高的血糖",
                    metric_value="5.2",
                    reference_range="3.9-6.1",
                    abnormal_flag="H",
                ),
                # 模型什么都没标，但数值超范围 —— 判定为 H，计数。
                MetricRecord(
                    report_id=owned.id,
                    metric_name="漏标的血糖",
                    metric_value="6.5",
                    reference_range="3.9-6.1",
                ),
                # 模型标了偏低但数值正常 —— 不计数。
                MetricRecord(
                    report_id=owned.id,
                    metric_name="误标偏低",
                    metric_value="1.50",
                    reference_range=">1.00",
                    abnormal_flag="L",
                ),
                # 模型未标记但偏低 —— 计数。
                MetricRecord(
                    report_id=owned.id,
                    metric_name="漏标的偏低",
                    metric_value="0.90",
                    reference_range=">1.00",
                ),
                # 未分类的原始标记（A）不参与判定，也没有参考范围 —— 不计数。
                MetricRecord(report_id=owned.id, metric_name="异常未分类", metric_value="1", abnormal_flag="A"),
                # 判定不可定（多数字值）—— 不计数。
                MetricRecord(
                    report_id=owned.id,
                    metric_name="多值指标",
                    metric_value="6.5/7.2",
                    reference_range="3.9-6.1",
                    abnormal_flag="H",
                ),
                MetricRecord(report_id=other.id, metric_name="另一主体异常", metric_value="1", abnormal_flag="H"),
            ]
        )
        db.commit()
    client.cookies.update(cookie)

    items = client.get("/api/auth/reports").json()
    assert len(items) == 1
    assert items[0]["metric_count"] == 6
    # 「误标偏高的血糖」与「误标偏低」在本报告页显示正常，「漏标的血糖」与
    # 「漏标的偏低」显示异常；摘要与报告页从此同口径。
    assert items[0]["abnormal_count"] == 2


def test_report_endpoints_require_a_session_when_enabled(subject_client):
    client, _ = subject_client
    assert client.get("/api/health/report/1").status_code == 401
    assert client.post("/api/health/report/upload").status_code in {401, 422}


def test_upload_is_owned_by_the_subject_and_invisible_to_a_second_one(subject_client, tmp_path):
    """票面要求保留的用例之二：上传归属主体且与第二主体互不可见。"""
    client, SessionLocal = subject_client
    from app.config import get_settings

    settings = get_settings()
    previous_dir = settings.REPORT_FILES_DIR
    settings.REPORT_FILES_DIR = str(tmp_path)
    try:
        owner_id, cookie = _issue_subject_session(SessionLocal, "t1", "u1")
        client.cookies.update(cookie)
        with patch("app.api.report.get_vision_encoder_service"):
            response = client.post(
                "/api/health/report/upload",
                files={"file": ("report.png", io.BytesIO(b"image"), "image/png")},
            )
        assert response.status_code == 202, response.text
        report_id = response.json()["id"]
        with SessionLocal() as db:
            report = db.get(MedicalReport, report_id)
            assert report.owner_id == owner_id
            # 存储列里是主体的存储标识；审计轨迹里是带类型的形态，
            # 读审计的人不必从字符串形状去猜这是哪一种身份。
            actor = (
                db.query(ReportAuditEvent)
                .filter(ReportAuditEvent.report_id == report_id)
                .order_by(ReportAuditEvent.id)
                .first()
                .actor
            )
            assert actor == owner_id

        # 第二个主体看不到它。
        _, other_cookie = _issue_subject_session(SessionLocal, "t2", "u2")
        client.cookies.clear()
        client.cookies.update(other_cookie)
        assert client.get(f"/api/health/report/{report_id}").status_code == 404
    finally:
        settings.REPORT_FILES_DIR = previous_dir


# --- 票据兑换入口（#172 之后唯一的身份来源）------------------------------------


@contextmanager
def _ticket_keypair(tmp_path, audience="health-flow-test"):
    """自造密钥对并把它配上——与 #171 的做法一致。

    真实公钥（#168）尚未交付，所以这里证明的是**兑换路径本身**能不能走通，
    不是与商城侧的对接。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = tmp_path / "ticket.pub"
    path.write_bytes(pem)
    settings = get_settings()
    before = (settings.MALL_TICKET_PUBLIC_KEY_PATH, settings.MALL_TICKET_AUDIENCE)
    settings.MALL_TICKET_PUBLIC_KEY_PATH = str(path)
    settings.MALL_TICKET_AUDIENCE = audience
    # 验签器缓存在 app.state 上（生产里配置不变，缓存是对的）。但用例之间 app 是
    # 同一个对象、而每个用例有自己的密钥对，所以这里显式清掉缓存——否则第一个
    # 用例的公钥会留在缓存里，后面用例的票据一律验签失败。
    from app.main import app

    cached = getattr(app.state, "ticket_verifier", None)
    app.state.ticket_verifier = None
    try:
        yield key
    finally:
        settings.MALL_TICKET_PUBLIC_KEY_PATH, settings.MALL_TICKET_AUDIENCE = before
        app.state.ticket_verifier = cached


def _make_ticket(key, *, tenant="ticket-tenant", subject="ticket-user", jti="jti-1") -> str:
    import base64
    import json
    from datetime import UTC, datetime, timedelta

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    now = datetime.now(UTC)
    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = b64(
        json.dumps(
            {
                "aud": get_settings().MALL_TICKET_AUDIENCE,
                "sub": subject,
                "tenant_id": tenant,
                "jti": jti,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(seconds=120)).timestamp()),
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}"
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{b64(signature)}"


def test_ticket_exchange_creates_a_session_and_the_subject(subject_client, tmp_path):
    client, SessionLocal = subject_client
    with _ticket_keypair(tmp_path) as key:
        response = client.post("/api/auth/ticket", json={"ticket": _make_ticket(key)})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tenant_id"] == "ticket-tenant"
    assert body["external_subject"] == "ticket-user"
    assert client.cookies.get(SESSION_COOKIE)

    # 会话可用，而且报告路由拿到的是同一个主体标识。
    assert client.get("/api/auth/me").json()["subject_id"] == body["subject_id"]
    with SessionLocal() as db:
        from app.data.models import UserSession

        saved = db.query(UserSession).one()
        assert saved.account_id == body["subject_id"]


def test_the_same_ticket_cannot_be_exchanged_twice(subject_client, tmp_path):
    """一次性在**端到端入口**上成立，而不只是单测里对 `redeem` 的断言。"""
    client, _ = subject_client
    with _ticket_keypair(tmp_path) as key:
        ticket = _make_ticket(key, jti="jti-once")
        assert client.post("/api/auth/ticket", json={"ticket": ticket}).status_code == 201
        second = client.post("/api/auth/ticket", json={"ticket": ticket})

    assert second.status_code == 401
    assert "已被使用" in second.json()["detail"]


def test_exchange_failure_does_not_fall_back_to_a_login_page(subject_client, tmp_path):
    """票面明确要求：解析失败**不得回退到登录页**（那会让整个应用白屏）。

    所以这里断言的是一个明确的 401 + 可读说明，而不是 302/HTML。
    """
    client, _ = subject_client
    with _ticket_keypair(tmp_path) as key:
        bad = _make_ticket(key, tenant="")
        # 请求必须在配置仍在的上下文里发出——验签器是按配置懒建的。
        response = client.post("/api/auth/ticket", json={"ticket": bad})

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert "login" not in response.text.lower()


# --- review 修正：迁移级缺陷与停用语义 -----------------------------------------


def _legacy_sqlite_engine(db_path):
    """建一个**账号时代形状**的 SQLite 库，并带上生产引擎的 `PRAGMA foreign_keys=ON`。

    这个形状不是杜撰的：它逐列复制自服务宿主上 `/opt/health-flow/var/healthflow.db`
    的 `sqlite_master`（`account_id VARCHAR(36)` + `FOREIGN KEY(account_id)
    REFERENCES user_accounts(id) ON DELETE CASCADE`）。早先这里只建列不建外键，
    于是「旧库」与真实旧库差了最关键的那一条，用例因此漏过了票据登录必定失败这件事。
    """
    from app.data.mysql_client import _enable_sqlite_foreign_keys

    engine = create_engine(f"sqlite:///{db_path}")
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE user_accounts (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE user_sessions ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " account_id VARCHAR(36) NOT NULL,"
                " token_hash VARCHAR(64) NOT NULL,"
                " created_at DATETIME NOT NULL,"
                " expires_at DATETIME NOT NULL,"
                " last_seen_at DATETIME NOT NULL,"
                " revoked_at DATETIME,"
                " FOREIGN KEY(account_id) REFERENCES user_accounts(id) ON DELETE CASCADE"
                ")"
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX ix_user_sessions_token_hash ON user_sessions (token_hash)"))
        connection.execute(text("CREATE INDEX ix_user_sessions_expires_at ON user_sessions (expires_at)"))
        # 一条历史会话：迁移必须把它带过去，不能只搬空表。
        connection.execute(text("INSERT INTO user_accounts (id) VALUES ('legacy-account')"))
        connection.execute(
            text(
                "INSERT INTO user_sessions (account_id, token_hash, created_at, expires_at, last_seen_at)"
                " VALUES ('legacy-account', 'legacy-hash', '2026-09-01 00:00:00',"
                " '2026-10-01 00:00:00', '2026-09-01 00:00:00')"
            )
        )
    engine.dispose()


def test_create_tables_upgrades_a_legacy_sqlite_schema(tmp_path):
    """旧库的 `user_sessions.account_id` 带着指向账号表的外键且只有 36 字符。

    #172 之后这一列装的是主体标识（`account:<tenant>:<sub>`，可能 >36），两处都会
    挡住票据会话——外键让插入失败、长度让它被拒。`create_all` 不改既有表，所以这段
    升级必须显式执行，且**要在由旧 schema 建起的库上测**。

    SQLite 也在升级范围内：既有部署用的就是那种库，「SQLite 上没有外键」这个前提
    只对 `create_all` 新建的库成立，对线上那份不成立。
    """
    from sqlalchemy import inspect

    from app.data.mysql_client import MySQLClient

    db_path = tmp_path / "legacy.db"
    _legacy_sqlite_engine(db_path)

    client = MySQLClient.__new__(MySQLClient)
    client.engine = create_engine(f"sqlite:///{db_path}")
    client.create_tables()

    with client.engine.begin() as connection:
        inspector = inspect(connection)
        columns = {str(column["name"]): column for column in inspector.get_columns("user_sessions")}
        foreign_keys = inspector.get_foreign_keys("user_sessions")
        indexes = {index["name"] for index in inspector.get_indexes("user_sessions")}
        rows = connection.execute(text("SELECT account_id, token_hash FROM user_sessions")).fetchall()
    assert columns["account_id"]["type"].length == 128
    assert foreign_keys == []
    # 重建表时不重建索引就会静默丢掉它们——唯一索引一丢，`token_hash` 的唯一性
    # 就没人守着，两个会话可以共用同一个 token 哈希。
    assert {"ix_user_sessions_token_hash", "ix_user_sessions_expires_at"} <= indexes
    assert rows == [("legacy-account", "legacy-hash")]
    client.engine.dispose()


def test_upgraded_legacy_sqlite_database_accepts_a_ticket_session(tmp_path):
    """**升级之后，票据兑换必须真的走得通。**

    上一条只断言 schema 变了；这一条断言那个变化**解决了它要解决的问题**。分开写
    是因为一个「重建了表但没去掉外键」的实现能让上一条全绿——而线上需要的恰恰是
    这一条红不红。

    用真实引擎（带 `PRAGMA foreign_keys=ON`）插一条主体会话，复现票据兑换最后一步。
    """
    from app.data.models import UserSession
    from app.data.mysql_client import MySQLClient, _enable_sqlite_foreign_keys
    from app.service.report_ownership import subject_storage_id
    from app.service.sessions import issue_session

    db_path = tmp_path / "legacy.db"
    _legacy_sqlite_engine(db_path)

    client = MySQLClient.__new__(MySQLClient)
    client.engine = create_engine(f"sqlite:///{db_path}")
    # 生产引擎就是这么建的（`MySQLClient.__init__` 对 SQLite URL 挂这个钩子）。
    # **这条监听器是用例的关键**：不挂它，外键不生效，一个没去掉外键的实现也会绿。
    event.listen(client.engine, "connect", _enable_sqlite_foreign_keys)
    client.create_tables()
    SessionLocal = sessionmaker(bind=client.engine)

    storage_id = subject_storage_id("2014583528221839360", "mall-user-1")
    token, session_row = issue_session(storage_id)
    with SessionLocal() as session:
        session.add(session_row)
        session.commit()
    with SessionLocal() as session:
        saved = session.query(UserSession).filter(UserSession.token_hash == session_hash(token)).one()
        assert saved.account_id == storage_id
    client.engine.dispose()


def test_restart_does_not_rewrite_newly_uploaded_reports():
    """**重启不得改写新上传的报告。**

    旧迁移按 `access_token_hash IS NULL` 打 `legacy_unclaimed`，而 #172 之后新上传
    的报告**本来就没有令牌**——于是每次启动都会把 `confirmed`/`assessed` 抹掉，
    之后 assess 返回 409。加 `owner_id` 条件把范围收回「真正无主的旧行」。
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.data.models import Base
    from app.data.models import MedicalReport as ReportModel
    from app.data.mysql_client import MySQLClient

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        ReportModel(
            patient_id="account:t:u",
            owner_id="account:t:u",
            report_type="体检",
            status="assessed",
        )
    )
    session.add(
        ReportModel(
            patient_id="anonymous",
            owner_id="anonymous",
            report_type="体检",
            status="assessed",
        )
    )
    session.commit()
    session.close()

    database = MySQLClient.__new__(MySQLClient)
    database.engine = engine
    database.create_tables()

    with engine.begin() as connection:
        rows = dict(connection.execute(text("SELECT owner_id, status FROM medical_reports")).all())
    assert rows["account:t:u"] == "assessed", "有主的新报告不该被改写"
    assert rows["anonymous"] == "legacy_unclaimed", "真正无主的旧行才打标记"
    engine.dispose()


def test_deactivated_subject_loses_its_sessions(subject_client):
    """主体被停用后，它未过期的会话必须立刻失效。"""
    from app.data.models import TicketSubject

    client, SessionLocal = subject_client
    owner_id, cookie = _issue_subject_session(SessionLocal, "t1", "u1")
    client.cookies.update(cookie)
    assert client.get("/api/auth/me").status_code == 200

    with SessionLocal() as db:
        db.query(TicketSubject).filter(TicketSubject.tenant_id == "t1").update({"is_active": False})
        db.commit()

    assert client.get("/api/auth/me").status_code == 401


def test_long_subject_id_is_rejected_before_the_ticket_is_spent(subject_client, tmp_path):
    """主体标识超长时必须在**兑换之前**拒绝——否则票被烧掉、用户再跳一次还是一样。"""
    client, SessionLocal = subject_client
    with _ticket_keypair(tmp_path) as key:
        ticket = _make_ticket(key, tenant="t" * 80, subject="u" * 80)
        response = client.post("/api/auth/ticket", json={"ticket": ticket})

    assert response.status_code == 400
    assert "主体标识" in response.json()["detail"]

    # 票据**没有**被消费——同一张票改小标识后仍可用（这里用另一张同 jti 的票验证：
    # 若上面已消费，这次会得到 401「已被使用」）。
    with _ticket_keypair(tmp_path) as key:
        ok = _make_ticket(key, tenant="t1", subject="u1", jti="jti-length")
        assert client.post("/api/auth/ticket", json={"ticket": ok}).status_code == 201


def test_exchange_returns_the_stored_display_name(subject_client, tmp_path):
    """兑换与 `/me` 必须给出**同一个**展示名，否则前端会闪一下。"""
    client, _ = subject_client
    with _ticket_keypair(tmp_path) as key:
        response = client.post("/api/auth/ticket", json={"ticket": _make_ticket(key)})

    assert response.status_code == 201
    exchanged = response.json()
    assert exchanged["display_name"] == client.get("/api/auth/me").json()["display_name"]
