"""健康数据同意：只追加记录、当前状态读取、上传闸门（GLOSSARY.md 的「健康数据同意」，#269）。

三件事必须由**服务端**持有，不能只活在界面上：

1. **记录只追加。** 同意与撤回都是新的一行，当前状态取每项最新一条。撤回不是
   「删掉上一条」—— 改写历史记录就无法举证「患者在什么时候、对哪一版文本表过什么态」。
2. **有效 = 最新一条是「同意」，且它的文本版本等于当前要求的版本。** 文本版本一变，
   此前所有表态同时失效，患者被重新征求。
3. **门槛在上传路径上执行。** 没有有效的「报告解读」同意就返回 403，且原因走
   `X-Report-Access-Reason`（具名，不让患者侧从 `detail` 那句人话里猜）。

HTTP 层用例覆盖以上三条；`test_the_new_reason_is_declared_by_its_owner` 钉住新原因
只在其所有者里声明一次（ADR 0019）。
"""

from __future__ import annotations

import io
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.data.models import Base, HealthDataConsent, MedicalReport, TicketSubject
from app.service.consent import record_consent, states
from app.service.consent import required_text_version as real_required_version
from app.service.consent_vocabulary import (
    CONSENT_DECISIONS_ORDERED,
    CONSENT_ITEMS_ORDERED,
    PRODUCT_RECOMMENDATION,
    REPORT_INTERPRETATION,
)
from app.service.recommendation_empty_reason import (
    REPORT_ACCESS_BY_STATUS,
    REPORT_ACCESS_REASONS,
)
from app.service.report_ownership import subject_storage_id
from app.service.sessions import SESSION_COOKIE, issue_session

CONSENT_URL = "/api/auth/consent"
UPLOAD_URL = "/api/health/report/upload"

#: 上传闸门拒绝时患者看到的那句话。它由服务端给出，患者侧不另写一句。
CONSENT_REQUIRED_DETAIL = "尚未同意「报告解读」授权，请先在商城模块完成同意后再上传报告"


@contextmanager
def _ticket_public_key():
    """走 lifespan 的 TestClient 需要一份可读的公钥文件（内容在这些用例里不被用到）。

    与 `tests/test_report_confirmation.py` 的同名夹具同形：票据链路本身由
    `tests/test_tickets.py` 覆盖，这里只是让服务能起来。
    """
    import tempfile
    from pathlib import Path

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    settings = get_settings()
    previous_path, previous_aud = settings.MALL_TICKET_PUBLIC_KEY_PATH, settings.MALL_TICKET_AUDIENCE
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ticket.pub"
        path.write_bytes(pem)
        settings.MALL_TICKET_PUBLIC_KEY_PATH = str(path)
        settings.MALL_TICKET_AUDIENCE = "health-flow-test"
        try:
            yield
        finally:
            settings.MALL_TICKET_PUBLIC_KEY_PATH = previous_path
            settings.MALL_TICKET_AUDIENCE = previous_aud


@pytest.fixture
def consent_client(monkeypatch):
    """真实内存库 + 真实会话解析（只把存储换成 SQLite）。"""
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

    settings = get_settings()
    previous = (
        settings.APP_ENV,
        settings.REPORT_ACCOUNT_REQUIRED,
        settings.HEALTHFLOW_BASIC_AUTH_ENABLED,
    )
    settings.APP_ENV = "development"
    settings.REPORT_ACCOUNT_REQUIRED = True
    settings.HEALTHFLOW_BASIC_AUTH_ENABLED = False
    monkeypatch.setattr("app.data.get_db", get_db)
    from app.main import app

    try:
        with _ticket_public_key(), TestClient(app) as client:
            yield client, SessionLocal
    finally:
        (
            settings.APP_ENV,
            settings.REPORT_ACCOUNT_REQUIRED,
            settings.HEALTHFLOW_BASIC_AUTH_ENABLED,
        ) = previous
        engine.dispose()


def _subject(SessionLocal, tenant: str = "consent-tenant", subject: str | None = None):
    """建一个票据主体与一条会话，返回 `(主体标识, token)`。"""
    external = subject or f"user-{uuid.uuid4().hex[:8]}"
    owner_id = subject_storage_id(tenant, external)
    token, row = issue_session(owner_id)
    with SessionLocal() as db:
        db.add(
            TicketSubject(
                id=str(uuid.uuid4()),
                tenant_id=tenant,
                external_subject=external,
                display_name="商城用户",
            )
        )
        db.add(row)
        db.commit()
    return owner_id, token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _status(client, token: str) -> dict[str, dict]:
    response = client.get(CONSENT_URL, headers=_bearer(token))
    assert response.status_code == 200, response.text
    return {item["item"]: item for item in response.json()["items"]}


def _upload(client, token: str, *, filename: str = "报告.png", content: bytes = b"\x89PNG\r\n\x1a\nx"):
    return client.post(
        UPLOAD_URL,
        headers=_bearer(token),
        files={"file": (filename, io.BytesIO(content), "image/png")},
    )


# ── 1. 当前状态由最新一条记录回答 ──────────────────────────────────────────


def test_no_record_means_no_item_is_valid_and_the_required_versions_are_published(
    consent_client,
):
    """一项都没表过态时：两项都无效，且响应用**当前要求的版本**回答，患者侧不自己记一份。"""
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)
    settings = get_settings()

    status = _status(client, token)

    assert set(status) == set(CONSENT_ITEMS_ORDERED)
    for state in status.values():
        assert state["decision"] is None
        assert state["text_version"] is None
        assert state["valid"] is False
    assert status[REPORT_INTERPRETATION]["required_text_version"] == (
        settings.CONSENT_REPORT_INTERPRETATION_VERSION
    )
    assert status[PRODUCT_RECOMMENDATION]["required_text_version"] == (
        settings.CONSENT_PRODUCT_RECOMMENDATION_VERSION
    )


def test_granting_report_interpretation_makes_it_valid_at_the_current_version(consent_client):
    """授予「报告解读」后，读取状态显示该项在当前版本下有效。"""
    client, SessionLocal = consent_client
    owner_id, token = _subject(SessionLocal)

    response = client.post(
        CONSENT_URL,
        headers=_bearer(token),
        json={"item": REPORT_INTERPRETATION, "decision": "granted"},
    )
    assert response.status_code == 200, response.text

    state = _status(client, token)[REPORT_INTERPRETATION]
    assert state["decision"] == "granted"
    assert state["valid"] is True
    assert state["text_version"] == get_settings().CONSENT_REPORT_INTERPRETATION_VERSION
    assert state["recorded_at"] is not None

    with SessionLocal() as db:
        rows = db.scalars(
            select(HealthDataConsent).where(HealthDataConsent.owner_id == owner_id)
        ).all()
    assert len(rows) == 1
    assert rows[0].consent_item == REPORT_INTERPRETATION
    assert rows[0].source == "mall_h5", "来源由服务端按会话通道判定，不由客户端声明"


def test_the_two_items_are_independent(consent_client):
    """两项彼此独立：同意报告解读不会让商品推荐变成已同意，反之亦然。"""
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)

    client.post(
        CONSENT_URL,
        headers=_bearer(token),
        json={"item": PRODUCT_RECOMMENDATION, "decision": "granted"},
    )

    status = _status(client, token)
    assert status[PRODUCT_RECOMMENDATION]["valid"] is True
    assert status[REPORT_INTERPRETATION]["valid"] is False


def test_revoking_appends_a_record_and_leaves_the_grant_untouched(consent_client):
    """撤回是一条新记录，不是删除或改写上一条 —— 历史记录一条都没有被改写。"""
    client, SessionLocal = consent_client
    owner_id, token = _subject(SessionLocal)

    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "granted"})
    with SessionLocal() as db:
        first = db.scalars(
            select(HealthDataConsent).where(HealthDataConsent.owner_id == owner_id)
        ).one()
    first_snapshot = (
        first.id,
        first.consent_item,
        first.decision,
        first.text_version,
        first.source,
        first.created_at,
    )

    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "revoked"})

    state = _status(client, token)[REPORT_INTERPRETATION]
    assert state["decision"] == "revoked"
    assert state["valid"] is False, "最新一条是撤回，当前状态就是无效"

    with SessionLocal() as db:
        rows = db.scalars(
            select(HealthDataConsent)
            .where(HealthDataConsent.owner_id == owner_id)
            .order_by(HealthDataConsent.id)
        ).all()
    assert len(rows) == 2
    assert (
        rows[0].id,
        rows[0].consent_item,
        rows[0].decision,
        rows[0].text_version,
        rows[0].source,
        rows[0].created_at,
    ) == first_snapshot, "撤回不得改写授予那一条"

    # 再授予一次：第三条记录，状态回到有效。
    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "granted"})
    assert _status(client, token)[REPORT_INTERPRETATION]["valid"] is True
    with SessionLocal() as db:
        assert len(db.scalars(select(HealthDataConsent).where(HealthDataConsent.owner_id == owner_id)).all()) == 3


def test_a_new_text_version_invalidates_the_previous_agreement(consent_client):
    """配置的文本版本变化后，此前的同意读取为无效。"""
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)
    settings = get_settings()
    previous = settings.CONSENT_REPORT_INTERPRETATION_VERSION

    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "granted"})
    assert _status(client, token)[REPORT_INTERPRETATION]["valid"] is True

    settings.CONSENT_REPORT_INTERPRETATION_VERSION = f"{previous}-next"
    try:
        state = _status(client, token)[REPORT_INTERPRETATION]
        assert state["decision"] == "granted", "旧表态本身仍在，只是对当前版本无效"
        assert state["text_version"] == previous
        assert state["required_text_version"] == f"{previous}-next"
        assert state["valid"] is False
    finally:
        settings.CONSENT_REPORT_INTERPRETATION_VERSION = previous


def test_status_is_scoped_to_the_subject(consent_client):
    """同意按主体隔离：另一个主体读到的是自己的一项都没有。"""
    client, SessionLocal = consent_client
    _, token_a = _subject(SessionLocal, tenant="tenant-a", subject="alice")
    _, token_b = _subject(SessionLocal, tenant="tenant-a", subject="bob")

    client.post(CONSENT_URL, headers=_bearer(token_a), json={"item": REPORT_INTERPRETATION, "decision": "granted"})

    assert _status(client, token_a)[REPORT_INTERPRETATION]["valid"] is True
    assert _status(client, token_b)[REPORT_INTERPRETATION]["valid"] is False


def test_the_consent_endpoints_require_a_session(consent_client):
    client, _ = consent_client
    assert client.get(CONSENT_URL).status_code == 401
    assert client.post(CONSENT_URL, json={"item": REPORT_INTERPRETATION, "decision": "granted"}).status_code == 401


def test_an_unknown_item_or_decision_is_rejected(consent_client):
    """词表之外的取值不是一种表态：契约层当场拒绝，而不是落一行读不懂的记录。"""
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)

    assert (
        client.post(CONSENT_URL, headers=_bearer(token), json={"item": "marketing", "decision": "granted"}).status_code
        == 422
    )
    assert (
        client.post(
            CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "maybe"}
        ).status_code
        == 422
    )


def test_a_cookie_session_records_the_legacy_source(consent_client):
    """来源由会话通道判定：Cookie 是旧前端，Bearer 是商城 H5。"""
    client, SessionLocal = consent_client
    owner_id, token = _subject(SessionLocal)

    client.post(
        CONSENT_URL,
        cookies={SESSION_COOKIE: token},
        json={"item": REPORT_INTERPRETATION, "decision": "granted"},
    )

    with SessionLocal() as db:
        row = db.scalars(select(HealthDataConsent).where(HealthDataConsent.owner_id == owner_id)).one()
    assert row.source == "legacy_web"


# ── 2. 上传闸门 ────────────────────────────────────────────────────────────


def _report_ready(settings_dir: str):
    return SimpleNamespace(
        MAX_UPLOAD_FILES=20,
        MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        MAX_UPLOAD_TOTAL_BYTES=50 * 1024 * 1024,
        REPORT_PARSE_WORKERS=4,
        REPORT_FILES_DIR=settings_dir,
    )


def test_upload_without_consent_is_refused_with_a_named_reason(consent_client, tmp_path):
    """没有有效「报告解读」同意时上传返回 403，且原因经响应头具名出域。

    闸门在**读取文件之前**：连格式都不合法的文件也得到 403，而不是 415 —— 否则
    「不接收上传」就成了「先收下再挑毛病」。
    """
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)

    with patch("app.api.report.get_settings", return_value=_report_ready(str(tmp_path))):
        response = _upload(client, token, filename="报告.txt", content=b"hello")

    assert response.status_code == 403, response.text
    assert response.headers["X-Report-Access-Reason"] == "report_interpretation_consent_required"
    assert response.json()["detail"] == CONSENT_REQUIRED_DETAIL
    with SessionLocal() as db:
        assert db.query(MedicalReport).count() == 0, "被拒的上传不得留下任何报告行"
    assert not any(tmp_path.iterdir()), "被拒的上传不得落盘任何文件"


def test_a_revocation_landing_between_the_gate_and_the_write_leaves_no_new_report(
    consent_client, tmp_path
):
    """撤回落在「闸门通过」与「上传提交」之间时，不得多出一份新报告（评审在 PR #273 指出）。

    请求进来时同意是有效的，所以第一道闸门放行；随后撤回提交、删掉了当时的全部报告
    （那份上传自己的行**还没提交，删不到**）。若不再问一次，这次写入就会在一份已经
    撤回的授权下落地。

    这里用「第一次 `has_valid` 之后再把撤回记录写进去」来造出那个窗口：之后的那次判定
    必须看到它，回滚掉新报告并删掉刚落的原始文件。
    """
    from app.service import consent as consent_service

    client, SessionLocal = consent_client
    owner_id, token = _subject(SessionLocal)
    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "granted"})

    real_has_valid = consent_service.has_valid
    calls = {"n": 0}

    def has_valid_then_revoke_then_answer(db, owner_id_arg, item):
        calls["n"] += 1
        answer = real_has_valid(db, owner_id_arg, item)
        if calls["n"] == 1:
            with SessionLocal() as other:
                other.add(
                    HealthDataConsent(
                        owner_id=owner_id_arg,
                        consent_item=REPORT_INTERPRETATION,
                        decision="revoked",
                        text_version=real_required_version(REPORT_INTERPRETATION),
                        source="seed",
                    )
                )
                other.commit()
        return answer

    with (
        patch("app.api.report.has_valid", side_effect=has_valid_then_revoke_then_answer),
        patch("app.api.report.get_settings", return_value=_report_ready(str(tmp_path))),
    ):
        response = _upload(client, token)

    assert response.status_code == 403, response.text
    assert response.headers["X-Report-Access-Reason"] == "report_interpretation_consent_required"
    with SessionLocal() as db:
        assert db.query(MedicalReport).count() == 0, "撤回之后不得留下新报告"
    assert not any(tmp_path.iterdir()), "回滚的上传不得留下原始文件"


def test_upload_is_accepted_once_consent_is_valid(consent_client, tmp_path):
    """有同意时上传行为与现在完全一致（这里到 202 为止；解析由队列负责）。"""
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)
    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "granted"})

    with patch("app.api.report.get_settings", return_value=_report_ready(str(tmp_path))):
        response = _upload(client, token)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "processing"
    assert body["extraction_job"]["status"] == "queued"
    assert (tmp_path / str(body["id"]) / "1.png").is_file()


def test_a_revoked_consent_closes_the_gate_again(consent_client, tmp_path):
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)
    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "granted"})
    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "revoked"})

    with patch("app.api.report.get_settings", return_value=_report_ready(str(tmp_path))):
        response = _upload(client, token)

    assert response.status_code == 403
    assert response.headers["X-Report-Access-Reason"] == "report_interpretation_consent_required"


def test_a_consent_for_the_old_text_version_does_not_open_the_gate(consent_client, tmp_path):
    """文本版本一变，旧同意不再有效 —— 闸门与状态读取用的是同一份判据。"""
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)
    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "granted"})
    settings = get_settings()
    previous = settings.CONSENT_REPORT_INTERPRETATION_VERSION
    settings.CONSENT_REPORT_INTERPRETATION_VERSION = f"{previous}-next"
    try:
        with patch("app.api.report.get_settings", return_value=_report_ready(str(tmp_path))):
            response = _upload(client, token)
    finally:
        settings.CONSENT_REPORT_INTERPRETATION_VERSION = previous

    assert response.status_code == 403
    assert response.headers["X-Report-Access-Reason"] == "report_interpretation_consent_required"


def test_the_product_recommendation_consent_does_not_open_the_upload_gate(consent_client, tmp_path):
    """推荐同意与解读同意是两件事：前者不能代替后者。"""
    client, SessionLocal = consent_client
    _, token = _subject(SessionLocal)
    client.post(CONSENT_URL, headers=_bearer(token), json={"item": PRODUCT_RECOMMENDATION, "decision": "granted"})

    with patch("app.api.report.get_settings", return_value=_report_ready(str(tmp_path))):
        response = _upload(client, token)

    assert response.status_code == 403


# ── 3. 词表的所有者 ────────────────────────────────────────────────────────


def test_the_new_reason_is_declared_by_its_owner():
    """403 的新具名原因只住在 `recommendation_empty_reason`（ADR 0019 的那一组）。"""
    assert REPORT_ACCESS_BY_STATUS[403] == "report_interpretation_consent_required"
    assert REPORT_ACCESS_BY_STATUS[403] in REPORT_ACCESS_REASONS


def test_the_service_refuses_to_record_a_value_outside_the_vocabulary():
    """词表之外的取值一律当场报错（与「指标确认决策」同一取向，不 fail open）。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        with pytest.raises(ValueError):
            record_consent(db, "account:t:u", "marketing", "granted", source="seed")
        with pytest.raises(ValueError):
            record_consent(db, "account:t:u", REPORT_INTERPRETATION, "maybe", source="seed")
    engine.dispose()


def test_the_current_state_is_the_latest_record_for_each_item(consent_client):
    """`states()` 与 HTTP 读取给出同一份结论（单点判定，不各算一遍）。"""
    client, SessionLocal = consent_client
    owner_id, token = _subject(SessionLocal)
    client.post(CONSENT_URL, headers=_bearer(token), json={"item": REPORT_INTERPRETATION, "decision": "granted"})

    with SessionLocal() as db:
        by_item = {state.item: state for state in states(db, owner_id)}
    http_status = _status(client, token)

    assert by_item[REPORT_INTERPRETATION].valid is True
    assert by_item[REPORT_INTERPRETATION].decision == http_status[REPORT_INTERPRETATION]["decision"]
    assert by_item[PRODUCT_RECOMMENDATION].valid is False
    assert set(CONSENT_DECISIONS_ORDERED) == {"granted", "revoked"}
