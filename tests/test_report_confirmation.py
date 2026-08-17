"""Regression tests for the report confirmation and evidence bridge boundary."""

import io
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base
from app.schema.report import MetricRecord
from app.service.evidence_bridge import metric_code_for_name
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


class _Embeddings:
    def embed(self, _: str):
        return [0.1] * 4


class _Milvus:
    def insert(self, **_):
        return None

    def flush(self):
        return None


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
    }
    assert {name: metric_code_for_name(name) for name in expected} == expected


def test_multi_file_confirmation_matches_published_card():
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

    with patch("app.data.get_db", override_get_db), \
        patch("app.data.mysql_client.get_mysql_client") as mysql, \
        patch("app.api.report.get_vision_encoder_service", return_value=vision), \
        patch("app.api.report.get_embedding_client", return_value=_Embeddings()), \
        patch("app.api.report.get_milvus_client", return_value=_Milvus()), \
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
            assert response.status_code == 200, response.text
            report = response.json()
            assert report["status"] == "pending_confirmation"
            assert [item["source_file_index"] for item in report["metrics"]] == [1, 2]
            metric_ids = [item["id"] for item in report["metrics"]]

            confirmed = client.post(
                f"/api/health/report/{report['id']}/confirm",
                json={
                    "subject_consistency": "same",
                    "observations": [
                        {"metric_id": metric_ids[0], "decision": "confirmed"},
                        {"metric_id": metric_ids[1], "decision": "excluded"},
                    ]
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            result = confirmed.json()
            assert result["status"] == "assessed"
            assert result["evidence_result"]["findings"][0]["card"]["version"] == "1.0.0"

    assert vision.calls == ["first.png", "second.png"]
