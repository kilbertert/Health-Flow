"""撤回同意：撤回报告解读即删除该主体全部报告（PRD #266 子单 #272）。

撤回不只是一行记录 —— 它的后果在服务端兑现：

1. 撤回「报告解读」删除该主体的**全部报告**：报告行连同级联的指标、页图行、审计事件与
   抽取任务，以及磁盘上的原始文件。**审计事件一并删除**：`ReportAuditEvent.report_id`
   是对报告的非空外键、随报告级联，而审计行能追溯到主体、属于个人信息；PRD 原文写的
   「保留审计事件」已按票面的订正改为删除，留证由撤回记录承担（ADR 0029）。
2. 撤回记录里写着这次**删掉了多少份报告**，而此前的同意记录一条都没有被改写。
3. 撤回「商品推荐」只停止推荐：推荐端点返回「未同意」，报告一份都不动。

删除的顺序是「先删库、后删文件」（与单份删除同一条路径）。反过来时，一次失败的提交会
留下指向不存在文件的报告行。最后一条用例把中途失败钉住：数据库回滚、文件还在、撤回记录
也没有落下 —— 不会出现「记录说删了、报告还在」或「报告行指向不存在的文件」。

全部用例都打在 HTTP 层（`POST /api/auth/consent`），数据库与磁盘是支撑断言。
"""

from __future__ import annotations

import io
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.data.models import (
    Base,
    HealthDataConsent,
    MedicalReport,
    MetricRecord,
    ReportAuditEvent,
    ReportExtractionJob,
    ReportFile,
    TicketSubject,
)
from app.service.consent_vocabulary import (
    PRODUCT_RECOMMENDATION,
    REPORT_INTERPRETATION,
)
from app.service.report_ownership import subject_storage_id
from app.service.sessions import issue_session

CONSENT_URL = "/api/auth/consent"
UPLOAD_URL = "/api/health/report/upload"


@pytest.fixture
def revocation_client(monkeypatch):
    """真实内存库 + 真实会话解析（只把存储换成 SQLite），与 `test_consent.py` 同形。"""
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
        # 不需要 lifespan 的票据验签器：本文件的端点只读会话语义。
        yield TestClient(app), SessionLocal
    finally:
        (
            settings.APP_ENV,
            settings.REPORT_ACCOUNT_REQUIRED,
            settings.HEALTHFLOW_BASIC_AUTH_ENABLED,
        ) = previous
        engine.dispose()


def _subject(SessionLocal, tenant: str, subject: str) -> tuple[str, str]:
    """建一个票据主体与一条会话，返回 `(主体标识, token)`。"""
    owner_id = subject_storage_id(tenant, subject)
    token, row = issue_session(owner_id)
    with SessionLocal() as db:
        db.add(
            TicketSubject(
                id=f"subject-{tenant}-{subject}",
                tenant_id=tenant,
                external_subject=subject,
                display_name="商城用户",
            )
        )
        db.add(row)
        db.commit()
    return owner_id, token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post_consent(client, token: str, item: str, decision: str) -> dict:
    response = client.post(
        CONSENT_URL,
        headers=_bearer(token),
        json={"item": item, "decision": decision},
    )
    assert response.status_code == 200, response.text
    return {entry["item"]: entry for entry in response.json()["items"]}


def _seed_report(
    SessionLocal,
    owner_id: str,
    files_dir,
    *,
    status: str = "assessed",
    file_count: int = 1,
    metric_count: int = 2,
    audit_count: int = 1,
    with_job: bool = False,
) -> tuple[int, list]:
    """一份带原始文件、指标、审计事件（可选抽取任务）的报告，返回 `(id, 文件路径)`。"""
    with SessionLocal() as db:
        report = MedicalReport(
            patient_id=owner_id,
            owner_id=owner_id,
            status=status,
            report_type="体检报告",
            department="健康管理中心",
            exam_date=datetime.now(),
        )
        db.add(report)
        db.flush()
        directory = files_dir / str(report.id)
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(1, file_count + 1):
            path = directory / f"{index}.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            paths.append(path)
            db.add(
                ReportFile(
                    report_id=report.id,
                    file_index=index,
                    original_filename=f"报告-{index}.png",
                    media_type="image/png",
                    stored_path=str(path),
                    page_count=1,
                )
            )
        for index in range(metric_count):
            db.add(
                MetricRecord(
                    report_id=report.id,
                    metric_name=f"指标{index}",
                    metric_value="6.5",
                    unit="mmol/L",
                )
            )
        for index in range(audit_count):
            db.add(
                ReportAuditEvent(
                    report_id=report.id,
                    action="uploaded",
                    actor="seed",
                    detail={"file_count": file_count, "index": index},
                )
            )
        if with_job:
            db.add(ReportExtractionJob(report_id=report.id, status="completed"))
        db.commit()
        return report.id, paths


def _counts(SessionLocal, owner_id: str) -> dict[str, int]:
    """该主体的报告、指标、页图行、审计事件与抽取任务的条数（只按报告归属过滤）。"""
    with SessionLocal() as db:
        report_ids = [
            report_id
            for (report_id,) in db.execute(
                select(MedicalReport.id).where(MedicalReport.owner_id == owner_id)
            )
        ]
        if not report_ids:
            return {
                "reports": 0,
                "metrics": 0,
                "files": 0,
                "audit_events": 0,
                "jobs": 0,
            }
        return {
            "reports": len(report_ids),
            "metrics": db.query(MetricRecord).filter(MetricRecord.report_id.in_(report_ids)).count(),
            "files": db.query(ReportFile).filter(ReportFile.report_id.in_(report_ids)).count(),
            "audit_events": db.query(ReportAuditEvent)
            .filter(ReportAuditEvent.report_id.in_(report_ids))
            .count(),
            "jobs": db.query(ReportExtractionJob)
            .filter(ReportExtractionJob.report_id.in_(report_ids))
            .count(),
        }


def _consent_rows(SessionLocal, owner_id: str) -> list[HealthDataConsent]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(HealthDataConsent)
                .where(HealthDataConsent.owner_id == owner_id)
                .order_by(HealthDataConsent.id)
            )
        )


def _snapshot(row: HealthDataConsent) -> tuple:
    return (
        row.id,
        row.consent_item,
        row.decision,
        row.text_version,
        row.source,
        row.created_at,
        row.deleted_report_count,
    )


# ── 1. 撤回报告解读：全部报告与文件消失，其他主体不受影响 ────────────────────


def test_revoking_interpretation_deletes_all_reports_of_that_subject_only(
    revocation_client, tmp_path
):
    client, SessionLocal = revocation_client
    owner_id, token = _subject(SessionLocal, "revoke-tenant", "alice")
    other_id, _ = _subject(SessionLocal, "revoke-tenant", "bob")
    files_dir = tmp_path / "files"
    files_dir.mkdir()

    _post_consent(client, token, REPORT_INTERPRETATION, "granted")
    alice_a, alice_a_files = _seed_report(
        SessionLocal, owner_id, files_dir, file_count=2, metric_count=3, audit_count=2, with_job=True
    )
    alice_b, alice_b_files = _seed_report(
        SessionLocal, owner_id, files_dir, status="pending_confirmation", file_count=1
    )
    bob_report, bob_files = _seed_report(SessionLocal, other_id, files_dir, file_count=1)
    grant_snapshot = _snapshot(_consent_rows(SessionLocal, owner_id)[0])

    state = _post_consent(client, token, REPORT_INTERPRETATION, "revoked")

    assert state[REPORT_INTERPRETATION]["decision"] == "revoked"
    assert state[REPORT_INTERPRETATION]["valid"] is False
    # 撤回记录自己回答「这次删了什么」——报告行已经不在了。
    assert state[REPORT_INTERPRETATION]["deleted_report_count"] == 2

    assert _counts(SessionLocal, owner_id) == {
        "reports": 0,
        "metrics": 0,
        "files": 0,
        "audit_events": 0,
        "jobs": 0,
    }, "报告连同级联的指标、页图、审计事件与抽取任务一起消失"
    for path in [*alice_a_files, *alice_b_files]:
        assert not path.exists(), f"磁盘上的原始文件必须被删除：{path}"
    assert not (files_dir / str(alice_a)).exists()
    assert not (files_dir / str(alice_b)).exists()

    # 另一个主体：数据与磁盘文件都不受影响。
    assert _counts(SessionLocal, other_id)["reports"] == 1
    assert _counts(SessionLocal, other_id)["files"] == 1
    with SessionLocal() as db:
        assert db.get(MedicalReport, bob_report) is not None
    assert all(path.exists() for path in bob_files)

    # 同意记录只追加：授予那一条一个字段都没有被改写，撤回是第二条。
    rows = _consent_rows(SessionLocal, owner_id)
    assert [_snapshot(row)[:6] for row in rows] == [grant_snapshot[:6], _snapshot(rows[1])[:6]]
    assert rows[0].decision == "granted"
    assert rows[0].deleted_report_count is None
    assert rows[1].decision == "revoked"
    assert rows[1].deleted_report_count == 2
    assert rows[1].text_version == get_settings().CONSENT_REPORT_INTERPRETATION_VERSION
    assert rows[1].source == "mall_h5", "来源由服务端按会话通道判定"


def test_revoking_interpretation_with_no_reports_records_zero(revocation_client):
    """没有报告时撤回照样成立，记录写 0（「这次一份也没删」），而不是留空。"""
    client, SessionLocal = revocation_client
    owner_id, token = _subject(SessionLocal, "revoke-tenant", "empty")
    _post_consent(client, token, REPORT_INTERPRETATION, "granted")

    state = _post_consent(client, token, REPORT_INTERPRETATION, "revoked")

    assert state[REPORT_INTERPRETATION]["deleted_report_count"] == 0
    assert [row.deleted_report_count for row in _consent_rows(SessionLocal, owner_id)] == [None, 0]


# ── 2. 撤回后的闸门：上传被拒，重新同意后放行 ──────────────────────────────


def _report_ready(directory) -> SimpleNamespace:
    return SimpleNamespace(
        MAX_UPLOAD_FILES=20,
        MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        MAX_UPLOAD_TOTAL_BYTES=50 * 1024 * 1024,
        REPORT_PARSE_WORKERS=4,
        REPORT_FILES_DIR=str(directory),
    )


def _upload(client, token: str, settings):
    with patch("app.api.report.get_settings", return_value=settings):
        return client.post(
            UPLOAD_URL,
            headers=_bearer(token),
            files={"file": ("报告.png", io.BytesIO(b"\x89PNG\r\n\x1a\nx"), "image/png")},
        )


def test_upload_is_refused_after_revocation_and_accepted_after_regranting(
    revocation_client, tmp_path
):
    client, SessionLocal = revocation_client
    _, token = _subject(SessionLocal, "revoke-tenant", "carol")
    settings = _report_ready(tmp_path / "uploaded")
    settings.REPORT_FILES_DIR = tmp_path / "uploaded"
    (tmp_path / "uploaded").mkdir()
    _post_consent(client, token, REPORT_INTERPRETATION, "granted")

    assert _upload(client, token, settings).status_code == 202
    _post_consent(client, token, REPORT_INTERPRETATION, "revoked")

    denied = _upload(client, token, settings)
    assert denied.status_code == 403, denied.text
    assert denied.headers["X-Report-Access-Reason"] == "report_interpretation_consent_required"

    _post_consent(client, token, REPORT_INTERPRETATION, "granted")
    assert _upload(client, token, settings).status_code == 202


# ── 3. 撤回商品推荐：只停止推荐，报告保留 ─────────────────────────────────


def test_revoking_product_recommendation_stops_recommendations_and_keeps_reports(
    revocation_client, tmp_path
):
    client, SessionLocal = revocation_client
    owner_id, token = _subject(SessionLocal, "revoke-tenant", "dave")
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    _post_consent(client, token, REPORT_INTERPRETATION, "granted")
    _post_consent(client, token, PRODUCT_RECOMMENDATION, "granted")
    report_id, paths = _seed_report(SessionLocal, owner_id, files_dir, file_count=2)
    with SessionLocal() as db:
        report = db.get(MedicalReport, report_id)
        report.evidence_result = {
            "findings": [{"condition_code": "COND_DYSLIPIDEMIA", "condition_name": "血脂异常"}]
        }
        db.commit()

    state = _post_consent(client, token, PRODUCT_RECOMMENDATION, "revoked")

    assert state[PRODUCT_RECOMMENDATION]["decision"] == "revoked"
    assert state[PRODUCT_RECOMMENDATION]["valid"] is False
    # 没有删除任何报告，所以这一条不带一个会被读成「删了 0 份」的数。
    assert state[PRODUCT_RECOMMENDATION]["deleted_report_count"] is None
    assert state[REPORT_INTERPRETATION]["valid"] is True, "两项互不牵连"

    recommendations = client.get(
        f"/api/health/report/{report_id}/recommendations", headers=_bearer(token)
    )
    assert recommendations.status_code == 200, recommendations.text
    body = recommendations.json()
    assert body["reason"] == "product_recommendation_consent_required"
    assert body["items"] == [] and body["groups"] == []

    assert _counts(SessionLocal, owner_id)["reports"] == 1
    assert _counts(SessionLocal, owner_id)["files"] == 2
    assert all(path.exists() for path in paths)


# ── 4. 中途失败：不留半删状态 ─────────────────────────────────────────────


def test_a_failure_midway_rolls_back_and_leaves_no_half_state(revocation_client, tmp_path):
    """删除在提交之前失败：报告与同意记录一起回滚，磁盘文件一个都没动。

    这是「先删库、后删文件」换来的性质。反过来（先删文件）时，同一个失败会让报告行
    指向不存在的文件 —— 患者看到一份打不开、也删不掉的报告。
    """
    client, SessionLocal = revocation_client
    owner_id, token = _subject(SessionLocal, "revoke-tenant", "erin")
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    _post_consent(client, token, REPORT_INTERPRETATION, "granted")
    report_id, paths = _seed_report(SessionLocal, owner_id, files_dir, file_count=2)

    from app.service import report_deletion

    original = report_deletion.delete_reports

    def explode(db, reports):
        # 数据库里的删除已经发出去了，但事务不会提交 —— 模拟删除中途失败。
        original(db, reports)
        raise RuntimeError("模拟删除中途失败")

    with (
        patch("app.service.consent.delete_reports", side_effect=explode),
        pytest.raises(RuntimeError),
    ):
        client.post(
            CONSENT_URL,
            headers=_bearer(token),
            json={"item": REPORT_INTERPRETATION, "decision": "revoked"},
        )

    assert _counts(SessionLocal, owner_id)["reports"] == 1, "失败的删除必须回滚"
    assert _counts(SessionLocal, owner_id)["audit_events"] == 1
    assert all(path.exists() for path in paths), "提交之前不得删文件"
    # 撤回记录也没有落下：状态仍是已同意，患者重试即可。
    rows = _consent_rows(SessionLocal, owner_id)
    assert [row.decision for row in rows] == ["granted"]
    state = client.get(CONSENT_URL, headers=_bearer(token)).json()
    granted = next(entry for entry in state["items"] if entry["item"] == REPORT_INTERPRETATION)
    assert granted["valid"] is True
