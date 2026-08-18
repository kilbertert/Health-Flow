"""Regression tests for the report confirmation and evidence bridge boundary."""

import io
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base
from app.schema.report import MetricRecord
from app.service.evidence_bridge import infer_abnormal_flag, metric_code_for_name
from app.service.vision_encoder import ParsedReport


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


def test_reference_range_deterministically_normalizes_abnormality():
    assert infer_abnormal_flag("5.5", "<5.2") == "H"
    assert infer_abnormal_flag("1.50", ">1.00") == "N"
    assert infer_abnormal_flag("<20", "<20") is None


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
    with patch("app.data.get_db", override_get_db), \
        patch("app.data.mysql_client.get_mysql_client") as mysql, \
        patch("app.api.report.get_settings", return_value=settings), \
        patch("app.api.report.get_vision_encoder_service", return_value=vision), \
        patch(
            "app.api.report.fetch_metric_catalog",
            return_value=[{"code": "custom_glucose", "label": "自定义血糖"}],
        ), \
        patch(
            "app.api.report.match_published_evidence",
            return_value={
                "schema_version": "1",
                "findings": [
                    {
                        "condition_name": "糖尿病前期 / 糖代谢异常",
                        "source_observation_ids": ["health-flow-metric-1"],
                        "card": {"version": "1.0.0", "grade": "moderate", "sources": []},
                    }
                ],
                "unmatched": [],
                "skipped": [],
                "message": "",
            },
        ) as evidence_match:
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
            report_id = response.json()["id"]
            report = client.get(f"/api/health/report/{report_id}").json()
            assert report["status"] == "pending_confirmation"
            assert [item["source_file_index"] for item in report["metrics"]] == [1, 2]
            assert [item["original_filename"] for item in report["files"]] == [
                "first.png",
                "second.png",
            ]
            metric_ids = [item["id"] for item in report["metrics"]]

            confirmed = client.post(
                f"/api/health/report/{report['id']}/confirm",
                json={
                    "subject_consistency": "same",
                    "observations": [
                        {
                            "metric_id": metric_ids[0],
                            "decision": "confirmed",
                            "metric_code": "custom_glucose",
                        },
                        {"metric_id": metric_ids[1], "decision": "excluded"},
                    ]
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            result = confirmed.json()
            assert result["status"] == "assessed"
            assert result["evidence_result"]["findings"][0]["card"]["version"] == "1.0.0"
            assert evidence_match.call_args.args[0][0]["metric_code"] == "custom_glucose"

    assert sorted(vision.calls) == ["first.png", "second.png"]
