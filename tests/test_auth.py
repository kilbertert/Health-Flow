"""会话与报告归属的验收用例。

#172 之前这里叫「账号/会话」，覆盖注册、登录、改昵称、退出。**注册、登录、密码、
改昵称整体退役**，所以那些用例不是被删掉，而是**换了主体**：它们断言的契约
（会话建立、报告按主体隔离、上传归属主体）一条没变，变的是身份从哪来。

保留两条票面点名要求的用例：报告历史按主体隔离、上传归属主体且与第二主体互不可见。
"""

import io
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.data.models import Base, MedicalReport, MetricRecord, ReportAuditEvent
from app.main import app
from app.service.report_ownership import subject_storage_id
from app.service.sessions import SESSION_COOKIE, issue_session


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
    client, SessionLocal = subject_client
    owner_id, cookie = _issue_subject_session(SessionLocal, "t1", "u1")
    with SessionLocal() as db:
        owned = MedicalReport(patient_id=owner_id, owner_id=owner_id, status="assessed")
        other = MedicalReport(patient_id="other", owner_id="account:t2:u2", status="assessed")
        db.add_all([owned, other])
        db.flush()
        db.add_all(
            [
                MetricRecord(report_id=owned.id, metric_name="偏高", metric_value="1", abnormal_flag="H"),
                MetricRecord(report_id=owned.id, metric_name="偏低", metric_value="1", abnormal_flag="L"),
                MetricRecord(report_id=owned.id, metric_name="异常未分类", metric_value="1", abnormal_flag="A"),
                MetricRecord(report_id=owned.id, metric_name="正常", metric_value="1", abnormal_flag="N"),
                MetricRecord(report_id=owned.id, metric_name="未标记", metric_value="1"),
                MetricRecord(report_id=other.id, metric_name="另一主体异常", metric_value="1", abnormal_flag="H"),
            ]
        )
        db.commit()
    client.cookies.update(cookie)

    items = client.get("/api/auth/reports").json()
    assert len(items) == 1
    assert items[0]["metric_count"] == 5
    assert items[0]["abnormal_count"] == 3


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
