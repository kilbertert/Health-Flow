"""Regression tests for the report confirmation and evidence bridge boundary."""

import asyncio
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.data.models import Base
from app.data.models import MedicalReport as ReportModel
from app.data.models import MetricRecord as MetricModel
from app.schema.report import MetricRecord
from app.service.evidence_bridge import metric_code_for_name
from app.service.report_ownership import UNOWNED_SENTINEL
from app.service.vision_encoder import ParsedReport


@contextmanager
def _ticket_public_key():
    """给走 lifespan 的用例提供一份临时 RSA 公钥（内容不被该用例使用）。"""
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


@contextmanager
def _subject_session(session_factory):
    """建一个票据主体与一条会话，返回可直接用于请求的 cookie。

    #172 之后没有账号与密码，上传/确认都必须带主体会话；这里构造的正是
    「票据已兑换」之后的状态。
    """
    import uuid as _uuid

    from app.data.models import TicketSubject
    from app.service.report_ownership import subject_storage_id
    from app.service.sessions import SESSION_COOKIE, issue_session

    with session_factory() as session:
        owner_id = subject_storage_id("test-tenant", "test-user")
        session.add(
            TicketSubject(
                id=str(_uuid.uuid4()),
                tenant_id="test-tenant",
                external_subject="test-user",
                display_name="商城用户",
            )
        )
        token, row = issue_session(owner_id)
        session.add(row)
        session.commit()
    yield {SESSION_COOKIE: token}


def _evidence_result(*, findings=None, unmatched=None, skipped=None):
    return {
        "schema_version": "2",
        "sorting_version": "published-card-reference-range-v1",
        "correlation_id": "00000000-0000-0000-0000-000000000001",
        "findings": findings or [],
        "unmatched": unmatched or [],
        "skipped": skipped or [],
        "message": "",
        "patient_reply": {
            "title": "体检报告解读与健康风险提示",
            "summary": "",
            "findings": [],
            "unmatched_count": len(unmatched or []),
            "disclaimer": "仅供健康信息参考。",
        },
    }


class _Vision:
    def __init__(self) -> None:
        self.calls = []

    def parse(self, content: bytes, filename: str) -> ParsedReport:
        self.calls.append(filename)
        return ParsedReport(
            report_type="image",
            raw_text="空腹血糖 6.8 mmol/L 3.9-6.1",
            metrics=[
                MetricRecord(
                    metric_name="空腹血糖",
                    metric_value="6.8",
                    unit="mmol/L",
                    reference_range="3.9-6.1",
                    abnormal_flag="H",
                    page_number=1,
                    evidence_text="空腹血糖 6.8 mmol/L 3.9-6.1 H",
                    source_id=f"{filename}/page-1",
                )
            ],
            page_count=1,
            success=True,
        )


class _DB:
    def __init__(self, engine):
        self.SessionLocal = sessionmaker(bind=engine)

    @contextmanager
    def get_session(self):
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        finally:
            session.close()


def test_metric_names_with_report_abbreviations_are_canonicalized():
    expected = {
        "身体质量指数（BMI）": "bmi",
        "谷丙转氨酶（ALT）": "alt",
        "谷草转氨酶（AST）": "ast",
        "估算肾小球滤过率（eGFR）": "egfr",
        "空腹血糖（FPG）": "fasting_glucose",
        "高密度脂蛋白胆固醇（HDL-C）": "hdl_c",
        "血钙（Ca）": "calcium",
        "⾎钙 Ca": "calcium",
        "空腹血糖 FPG": "fasting_glucose",
        "身体质量指数 / BMI": "bmi",
        "收缩压 / Systolic Blood Pressure": "systolic_blood_pressure",
        "Total Chol": "total_cholesterol",
        "Non-HDL": "non_hdl_c",
        "Non-HDL-C": "non_hdl_c",
        "非高密度脂蛋白胆固醇": "non_hdl_c",
        "Triglyceride": "triglycerides",
        "HDL-C": "hdl_c",
        "LDL-C": "ldl_c",
        "低脂蛋白（坏）胆固醇（LDL-C）": "ldl_c",
    }
    assert {name: metric_code_for_name(name) for name in expected} == expected


def test_report_owner_isolation_rejects_another_owner():
    """另一个主体的存储标识不能读到这份报告。

    原来这条带令牌（「持有效令牌但主体不同仍被拒」）。#172 移除令牌机制后，
    被测的那条**契约**没变——**归属只认主体**——所以用例保留、令牌部分删掉。
    """
    from app.api.report import _authorized_report

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    report = ReportModel(
        patient_id="P-owner",
        report_type="体检",
        status="uploaded",
        owner_id="account:tenant-a:user-a",
    )
    session.add(report)
    session.commit()

    with pytest.raises(HTTPException) as error:
        _authorized_report(session, report.id, owner_id="account:tenant-a:user-b")
    assert error.value.status_code == 404
    session.close()


def test_legacy_report_without_an_owner_stays_sealed():
    """无主历史行（`owner_id` 为哨兵值）对任何主体都不可见。

    它以前靠令牌访问；令牌机制退役后这一类**没有访问路径**——这正是票面接受的
    「既有无主报告不再可访问」。用例保留，断言的是那条契约：**它保持封闭**。
    """
    from app.api.report import _authorized_report

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    report = ReportModel(
        patient_id="P-legacy",
        report_type="体检",
        status="assessed",
        owner_id=UNOWNED_SENTINEL,
    )
    session.add(report)
    session.commit()

    for candidate in ("account:tenant-a:user-a", UNOWNED_SENTINEL):
        with pytest.raises(HTTPException) as error:
            _authorized_report(session, report.id, owner_id=candidate)
        assert error.value.status_code == 404
    session.close()


def test_confirmed_non_hdl_abnormal_is_sent_to_evidence_service():
    from app.api.report import _assess_report

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    report = ReportModel(
        patient_id="P-unmatched",
        report_type="体检",
        status="confirmed",
        subject_consistency="same",
    )
    session.add(report)
    session.flush()
    session.add(
        MetricModel(
            report_id=report.id,
            metric_name="Non-HDL",
            metric_value="4.00",
            unit="mmol/L",
            reference_range="<3.40",
            abnormal_flag="H",
            page_number=1,
            evidence_text="Non-HDL 4.00 mmol/L (<3.40)",
            source_file_index=1,
            confirmation_status="confirmed",
            # 确认时落定的编码(收敛后评估只消费它)。
            metric_code="non_hdl_c",
        )
    )
    session.commit()
    with patch(
        "app.api.report.match_published_evidence",
        return_value=_evidence_result(
            unmatched=[
                {
                    "observation_id": "health-flow-metric-1",
                    "metric_code": "non_hdl_c",
                    "metric_label": "非高密度脂蛋白胆固醇",
                    "condition_codes": ["COND_DYSLIPIDEMIA"],
                    "reason": "no_published_knowledge_card",
                    "source_observation": None,
                }
            ]
        ),
    ) as match:
        response = asyncio.run(_assess_report(report, session))

    assert match.call_args.args[0][0]["metric_code"] == "non_hdl_c"
    assert response.evidence_result.unmatched[0].observation_id == "health-flow-metric-1"
    assert response.evidence_result.unmatched[0].reason == "no_published_knowledge_card"
    assert response.evidence_result.unmatched[0].metric_label == "非高密度脂蛋白胆固醇"
    assert response.evidence_result.unmatched[0].source_observation.evidence_text == "Non-HDL 4.00 mmol/L (<3.40)"
    session.close()


def test_non_hdl_metric_builds_auditable_observation():
    from app.service.evidence_bridge import build_observations_with_unmatched

    metric = MetricModel(
        id=1,
        report_id=1,
        metric_name="Non-HDL",
        metric_value="4.00",
        unit="mmol/L",
        reference_range="<3.40",
        abnormal_flag="H",
        page_number=1,
        evidence_text="Non-HDL 4.00 mmol/L (<3.40)",
        source_file_index=1,
        confirmation_status="confirmed",
        # 确认时落定的编码。收敛后评估**只**消费它,不再从名字重新推导。
        metric_code="non_hdl_c",
    )
    observations, skipped, unmatched = build_observations_with_unmatched([metric])

    assert observations[0]["metric_code"] == "non_hdl_c"
    assert observations[0]["evidence_text"] == "Non-HDL 4.00 mmol/L (<3.40)"
    assert skipped == []
    assert unmatched == []


def test_json_encoded_bboxes_are_normalized_before_evidence_api_call():
    from app.service.evidence_bridge import build_observations_with_unmatched

    metric = MetricModel(
        id=42,
        report_id=7,
        metric_name="LDL-C",
        metric_value="3.63",
        unit="mmol/L",
        reference_range="<2.60",
        abnormal_flag="H",
        page_number=1,
        evidence_text="LDL-C 3.63 mmol/L (<2.60)",
        source_file_index=5,
        source_id="file-5/p1-m4",
        metric_code="ldl_c",
        confirmation_status="confirmed",
        bbox="[1196.0, 348.0, 1259.0, 383.0]",
        bbox_normalized="[584.0, 283.0, 615.0, 312.0]",
    )

    observations, skipped, unmatched = build_observations_with_unmatched([metric])

    assert skipped == []
    assert unmatched == []
    assert observations[0]["bbox"] == [1196.0, 348.0, 1259.0, 383.0]
    assert observations[0]["bbox_normalized"] == [584.0, 283.0, 615.0, 312.0]


def test_external_unmatched_keeps_report_source_observation():
    from app.api.report import _assess_report

    session, report = _assessment_fixture(
        metric_code="total_cholesterol",
        metric_name="总胆固醇",
        metric_value="5.5",
        reference_range="<5.2",
        evidence_text="总胆固醇 5.5 mmol/L (<5.2)",
        abnormal_flag="H",
    )
    with patch(
        "app.api.report.match_published_evidence",
        return_value=_evidence_result(
            unmatched=[
                {
                    "observation_id": "health-flow-metric-1",
                    "metric_code": "total_cholesterol",
                    "metric_label": "总胆固醇",
                    "condition_codes": ["COND_DYSLIPIDEMIA"],
                    "reason": "no_published_knowledge_card",
                    "source_observation": None,
                }
            ]
        ),
    ):
        response = asyncio.run(_assess_report(report, session))

    source = response.evidence_result.unmatched[0].source_observation
    assert source is not None
    assert source.evidence_text == "总胆固醇 5.5 mmol/L (<5.2)"
    assert source.source_page == 1
    session.close()


def _assessment_fixture(**metric_values):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    report = ReportModel(
        patient_id="P-deterministic",
        report_type="体检",
        status="confirmed",
        subject_consistency="same",
    )
    session.add(report)
    session.flush()
    values = {
        "report_id": report.id,
        "metric_name": "空腹血糖",
        "metric_value": "5.2",
        "unit": "mmol/L",
        "reference_range": "3.9-6.1",
        "abnormal_flag": "H",
        "page_number": 1,
        "evidence_text": "空腹血糖 5.2 mmol/L 3.9-6.1",
        "source_file_index": 1,
        "confirmation_status": "confirmed",
    }
    values.update(metric_values)
    session.add(MetricModel(**values))
    session.commit()
    return session, report


def test_assessment_uses_confirmed_range_instead_of_model_flag():
    from app.api.report import _assess_report

    session, report = _assessment_fixture()
    with patch(
        "app.api.report.match_published_evidence",
        return_value=_evidence_result(),
    ) as match:
        response = asyncio.run(_assess_report(report, session))

    assert match.call_args.args[0] == []
    assert response.evidence_result.unmatched == []
    assert response.evidence_result.skipped[0].reason == "within_reference_range"
    session.close()


def test_assessment_keeps_confirmed_rows_without_source_evidence_visible_as_skipped():
    from app.api.report import _assess_report

    session, report = _assessment_fixture(
        metric_code="fasting_glucose",
        evidence_text=None,
        abnormal_flag="H",
    )
    with patch(
        "app.api.report.match_published_evidence",
        return_value=_evidence_result(),
    ) as match:
        response = asyncio.run(_assess_report(report, session))

    assert match.call_args.args[0] == []
    assert response.evidence_result.skipped[0].reason == "missing_source_evidence"
    session.close()


def test_model_only_abnormal_without_reference_range_never_crosses_evidence_boundary():
    from app.api.report import _assess_report

    session, report = _assessment_fixture(
        reference_range=None,
        evidence_text="空腹血糖 5.2 mmol/L",
        abnormal_flag="H",
    )
    with patch(
        "app.api.report.match_published_evidence",
        return_value=_evidence_result(),
    ) as match:
        response = asyncio.run(_assess_report(report, session))

    assert match.call_args.args[0] == []
    assert response.evidence_result.skipped[0].reason == "missing_reference_range"
    session.close()


def test_report_metrics_are_returned_in_source_order():
    from app.api.report import _ordered_metrics

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    report = ReportModel(patient_id="P-order", status="pending_confirmation")
    session.add(report)
    session.flush()
    session.add_all(
        [
            MetricModel(report_id=report.id, source_file_index=2, page_number=1, metric_name="C"),
            MetricModel(report_id=report.id, source_file_index=1, page_number=2, metric_name="B"),
            MetricModel(report_id=report.id, source_file_index=1, page_number=1, metric_name="A"),
        ]
    )
    session.commit()

    assert [item.metric_name for item in _ordered_metrics(session, report.id).all()] == [
        "A",
        "B",
        "C",
    ]
    session.close()


def test_multi_file_confirmation_matches_published_card(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    fake_db = _DB(engine)
    vision = _Vision()

    def override_get_db():
        with fake_db.get_session() as session:
            yield session

    settings = SimpleNamespace(
        MAX_UPLOAD_FILES=20,
        MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        MAX_UPLOAD_TOTAL_BYTES=50 * 1024 * 1024,
        REPORT_PARSE_WORKERS=4,
        REPORT_FILES_DIR=str(tmp_path),
    )
    with (
        patch("app.data.get_db", override_get_db),
        patch("app.data.mysql_client.get_mysql_client") as mysql,
        patch("app.api.report.get_settings", return_value=settings),
        patch("app.api.report.get_vision_encoder_service", return_value=vision),
        patch(
            "app.api.report.fetch_metric_catalog",
            return_value=[{"code": "custom_glucose", "label": "自定义血糖"}],
        ),
        patch(
            "app.api.report.match_published_evidence",
            return_value=_evidence_result(
                findings=[
                    {
                        "condition_code": "COND_PREDIABETES",
                        "condition_name": "糖尿病前期 / 糖代谢异常",
                        "source_observation_ids": ["health-flow-metric-1"],
                        "urgency": "routine",
                        "abnormality_severity": 1,
                        "evidence_strength": "moderate",
                        "needs_recheck": True,
                        "department": "内分泌科",
                        "recheck_direction": "复查空腹血糖",
                        "epidemiology_background": "",
                        "source_observations": [],
                        "sorting": {
                            "urgency": "routine",
                            "abnormality_severity": 1,
                            "evidence_strength": "moderate",
                            "needs_recheck": True,
                            "department": "内分泌科",
                            "epidemiology_background": "",
                        },
                        "card": {
                            "id": "card-1",
                            "condition_code": "COND_PREDIABETES",
                            "scope_key": "metric:custom_glucose",
                            "version": "1.0.0",
                            "status": "published",
                            "grade": "moderate",
                            "published_at": "2026-08-19T00:00:00Z",
                            "evidence_profile_id": "profile-1",
                            "patient_visible_body": "正式知识卡内容",
                            "sources": [
                                {
                                    "claim_id": "claim-1",
                                    "paper_id": "paper-1",
                                    "paper_title": "Test paper",
                                    "doi": "10.1000/test",
                                    "evidence": "Test evidence",
                                    "locator": "p. 1",
                                }
                            ],
                        },
                    }
                ]
            ),
        ) as evidence_match,
    ):
        mysql_client = mysql.return_value
        mysql_client.create_tables.return_value = None
        mysql_client.close.return_value = None
        from app.main import app

        # 该用例走真实 lifespan（它会建票据验签器）。票据配置与这条链路无关，
        # 用自造密钥对把它补上，避免用例依赖本机是否配了商城公钥。
        with _ticket_public_key(), TestClient(app) as client:
            # #172 之后上传必须带主体会话（报告令牌旁路已移除）。这里直接用种子
            # 会话的 cookie，等价于「用户刚从商城跳进来兑换过一张票」。
            with _subject_session(fake_db.SessionLocal) as cookie:
                response = client.post(
                    "/api/health/report/upload",
                    data={"patient_id": "P001"},
                    cookies=cookie,
                    files=[
                        ("files", ("first.png", io.BytesIO(b"one"), "image/png")),
                        ("files", ("second.png", io.BytesIO(b"two"), "image/png")),
                    ],
                )
            assert response.status_code == 202, response.text
            uploaded = response.json()
            report_id = uploaded["id"]
            from app.service.report_worker import run_next_job

            assert run_next_job(fake_db.SessionLocal) is not None
            report = client.get(f"/api/health/report/{report_id}", cookies=cookie).json()
            assert report["status"] == "pending_confirmation"
            assert [item["source_file_index"] for item in report["metrics"]] == [1, 2]
            assert [item["original_filename"] for item in report["files"]] == [
                "first.png",
                "second.png",
            ]
            metric_ids = [item["id"] for item in report["metrics"]]

            confirmed = client.post(
                f"/api/health/report/{report['id']}/confirm",
                cookies=cookie,
                json={
                    "subject_consistency": "same",
                    "observations": [
                        {
                            "metric_id": metric_ids[0],
                            "decision": "confirmed",
                            "metric_code": "custom_glucose",
                        },
                        {"metric_id": metric_ids[1], "decision": "excluded"},
                    ],
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            result = confirmed.json()
            assert result["status"] == "assessed"
            assert result["evidence_result"]["findings"][0]["card"]["version"] == "1.0.0"
            assert evidence_match.call_args.args[0][0]["metric_code"] == "custom_glucose"

    assert sorted(vision.calls) == ["first.png", "second.png"]


def test_report_parse_keeps_successful_files_when_one_file_fails():
    from app.api.report import _parse_report

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    report = ReportModel(
        patient_id="P-partial",
        report_type="体检",
        status="processing",
        subject_consistency="uncertain",
    )
    session.add(report)
    session.commit()
    report_id = report.id
    session.close()

    class PartialVision:
        def parse(self, content: bytes, filename: str) -> ParsedReport:
            if filename == "bad.png":
                return ParsedReport(
                    report_type="image",
                    raw_text="",
                    metrics=[],
                    page_count=1,
                    success=False,
                    error="provider timeout",
                    provider_run_ids=("provider-bad",),
                )
            return ParsedReport(
                report_type="image",
                raw_text="空腹血糖 6.8 mmol/L",
                metrics=[
                    MetricRecord(
                        metric_name="空腹血糖",
                        metric_value="6.8",
                        unit="mmol/L",
                        reference_range="3.9-6.1",
                        page_number=1,
                        evidence_text="空腹血糖 6.8 mmol/L 3.9-6.1",
                    )
                ],
                page_count=1,
                success=True,
                provider_run_ids=("provider-good",),
            )

    accepted_files = [
        (1, "good.png", "image/png", b"good"),
        (2, "bad.png", "image/png", b"bad"),
    ]
    with (
        patch("app.api.report.get_vision_encoder_service", return_value=PartialVision()),
        patch(
            "app.api.report.get_settings",
            return_value=SimpleNamespace(REPORT_PARSE_WORKERS=2),
        ),
    ):
        assert _parse_report(report_id, accepted_files, session_factory) is False

    session = session_factory()
    saved = session.get(ReportModel, report_id)
    assert saved is not None
    assert saved.status == "processing"
    assert len(saved.metrics) == 1
    assert saved.metrics[0].source_file_index == 1
    assert saved.parsed_content["warnings"] == ["bad.png: provider timeout"]
    assert saved.provider_run_id == "provider-good"
    assert json.loads(saved.provider_run_ids) == ["provider-good", "provider-bad"]
    session.close()


def test_confirmation_survives_a_catalog_outage():
    """目录不可用时，患者核对好的决策**仍然落库**。

    收敛前 `fetch_metric_catalog()` 位于确认的主事务前部，一次 503 就让全部决策
    一条都不保存（保存发生在 `db.commit()`，在目录读取之后）。患者核对了二十项
    指标，因为目录抖动全部丢失。

    现在降级保存：编码留作「待目录恢复后由评估路径重新裁决」，决策本身不丢。
    这是 ARCHITECTURE.md 既定降级哲学在指标目录上的延伸。
    """
    from app.api.report import confirm_report
    from app.schema.report import MetricConfirmation, ReportConfirmationRequest
    from app.service.evidence_bridge import EvidenceBridgeError

    session, report = _assessment_fixture(confirmation_status="pending", metric_code=None)
    report.owner_id = "account:t:u"
    session.commit()
    request = ReportConfirmationRequest(
        observations=[MetricConfirmation(metric_id=1, decision="confirmed")],
        subject_consistency="same",
    )

    with (
        patch("app.api.report.fetch_metric_catalog", side_effect=EvidenceBridgeError("目录暂不可用")),
        patch("app.api.report._assess_report", side_effect=EvidenceBridgeError("证据服务暂不可用")),
        patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id="account:t:u", subject="account:t:u"),
        ),
    ):
        import asyncio

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(confirm_report(report.id, SimpleNamespace(), request, session, owner_id=None))
        # 目录不可用不再让确认本身失败：失败的是紧随其后的评估。
        assert excinfo.value.status_code == 503
        assert "证据服务" in str(excinfo.value.detail)

    session.refresh(report)
    assert report.status == "confirmed", "目录不可用不该挡住确认落库"
    assert report.metrics[0].confirmation_status == "confirmed"


def test_stale_selected_code_falls_back_to_the_metric_name():
    """患者选定的编码已下架时，确认回落到**按目录验证过的指标名**。

    评审在 #149 指出：只试编码会让一个下架的旧编码把本来能解析的名称一起挡掉，
    一个可识别的异常指标因此变成 unmatched。
    """
    from app.api.report import confirm_report
    from app.schema.report import MetricConfirmation, ReportConfirmationRequest
    from app.service.evidence_bridge import EvidenceBridgeError

    session, report = _assessment_fixture(
        metric_name="LDL-C",
        metric_code="ldl_c_removed",  # 目录里没有这个编码了
        confirmation_status="pending",
        abnormal_flag="H",
        metric_value="3.63",
        reference_range="<2.60",
        evidence_text="LDL-C 3.63 mmol/L (<2.60)",
    )
    report.owner_id = "account:t:u"
    session.commit()
    request = ReportConfirmationRequest(
        observations=[MetricConfirmation(metric_id=1, decision="confirmed", metric_code="ldl_c_removed")],
        subject_consistency="same",
    )

    with (
        patch("app.api.report.fetch_metric_catalog", return_value=[{"code": "ldl_c", "label": "LDL-C"}]),
        patch("app.api.report._assess_report", side_effect=EvidenceBridgeError("证据服务暂不可用")),
        patch(
            "app.api.report.resolve_owner",
            return_value=SimpleNamespace(storage_id="account:t:u", subject="account:t:u"),
        ),
    ):
        import asyncio

        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            asyncio.run(confirm_report(report.id, SimpleNamespace(), request, session, owner_id=None))

    session.refresh(report)
    # 下架的编码挡不住名称解析：落定的是目录验证过的 ldl_c。
    assert report.metrics[0].metric_code == "ldl_c"
