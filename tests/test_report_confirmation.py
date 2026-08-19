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

from app.data.models import Base
from app.data.models import MedicalReport as ReportModel
from app.data.models import MetricRecord as MetricModel
from app.schema.report import MetricRecord
from app.service.evidence_bridge import infer_abnormal_flag, metric_code_for_name
from app.service.vision_encoder import ParsedReport


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
        "Triglyceride": "triglycerides",
        "HDL-C": "hdl_c",
        "LDL-C": "ldl_c",
    }
    assert {name: metric_code_for_name(name) for name in expected} == expected


def test_report_owner_isolation_rejects_a_valid_token_for_another_owner():
    from app.api.report import _authorized_report, _token_hash

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
        access_token_hash=_token_hash("token"),
        owner_id="owner-a",
    )
    session.add(report)
    session.commit()
    with pytest.raises(HTTPException) as error:
        _authorized_report(session, report.id, "token", owner_id="owner-b")
    assert error.value.status_code == 404
    session.close()


def test_legacy_report_without_token_cannot_cross_the_owner_boundary():
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
        status="legacy_unclaimed",
    )
    session.add(report)
    session.commit()
    with pytest.raises(HTTPException) as error:
        _authorized_report(session, report.id, "any-token", owner_id="owner")
    assert error.value.status_code == 404
    session.close()


def test_legacy_report_with_a_valid_token_but_no_owner_is_sealed():
    from app.api.report import _authorized_report, _token_hash

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    report = ReportModel(
        patient_id="P-legacy-token",
        report_type="体检",
        status="legacy_unclaimed",
        access_token_hash=_token_hash("legacy-token"),
    )
    session.add(report)
    session.commit()
    with pytest.raises(HTTPException) as error:
        _authorized_report(session, report.id, "legacy-token", owner_id="owner")
    assert error.value.status_code == 404
    session.close()


def test_reference_range_deterministically_normalizes_abnormality():
    assert infer_abnormal_flag("5.5", "<5.2") == "H"
    assert infer_abnormal_flag("1.50", ">1.00") == "N"
    assert infer_abnormal_flag("<20", "<20") is None


def test_confirmed_unmapped_abnormal_is_reported_without_evidence_query():
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
        )
    )
    session.commit()
    with patch(
        "app.api.report.match_published_evidence",
        return_value=_evidence_result(),
    ) as match:
        response = asyncio.run(_assess_report(report, session))

    assert match.call_args.args[0] == []
    assert response.evidence_result.skipped[0].observation_id == "health-flow-metric-1"
    assert response.evidence_result.skipped[0].reason == "unknown_metric_code"
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

    assert match.call_args.args[0][0]["metric_code"] == "fasting_glucose"
    assert response.evidence_result.unmatched == []
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

        with TestClient(app) as client:
            response = client.post(
                "/api/health/report/upload",
                data={"patient_id": "P001"},
                files=[
                    ("files", ("first.png", io.BytesIO(b"one"), "image/png")),
                    ("files", ("second.png", io.BytesIO(b"two"), "image/png")),
                ],
            )
            assert response.status_code == 202, response.text
            uploaded = response.json()
            report_id = uploaded["id"]
            headers = {"X-Report-Token": uploaded["access_token"]}
            from app.service.report_worker import run_next_job

            assert run_next_job(fake_db.SessionLocal) is not None
            report = client.get(
                f"/api/health/report/{report_id}", headers=headers
            ).json()
            assert report["status"] == "pending_confirmation"
            assert [item["source_file_index"] for item in report["metrics"]] == [1, 2]
            assert [item["original_filename"] for item in report["files"]] == [
                "first.png",
                "second.png",
            ]
            metric_ids = [item["id"] for item in report["metrics"]]

            confirmed = client.post(
                f"/api/health/report/{report['id']}/confirm",
                headers=headers,
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
            assert (
                result["evidence_result"]["findings"][0]["card"]["version"] == "1.0.0"
            )
            assert (
                evidence_match.call_args.args[0][0]["metric_code"] == "custom_glucose"
            )

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
        patch(
            "app.api.report.get_vision_encoder_service", return_value=PartialVision()
        ),
        patch(
            "app.api.report.get_settings",
            return_value=SimpleNamespace(REPORT_PARSE_WORKERS=2),
        ),
    ):
        _parse_report(report_id, accepted_files, session_factory)

    session = session_factory()
    saved = session.get(ReportModel, report_id)
    assert saved is not None
    assert saved.status == "pending_confirmation"
    assert len(saved.metrics) == 1
    assert saved.metrics[0].source_file_index == 1
    assert saved.parsed_content["warnings"] == ["bad.png: provider timeout"]
    assert saved.provider_run_id == "provider-good"
    assert json.loads(saved.provider_run_ids) == ["provider-good", "provider-bad"]
    session.close()
