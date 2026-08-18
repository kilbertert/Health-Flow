"""Tests for Report API."""

import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base
from app.schema.report import MetricRecord


class MockVisionService:
    """Mock VisionEncoder service."""

    def parse(self, content, filename):
        from app.service.vision_encoder import ParsedReport

        return ParsedReport(
            report_type="text_pdf",
            raw_text="空腹血糖: 6.5 mmol/L",
            metrics=[
                MetricRecord(
                    metric_name="空腹血糖",
                    metric_value="6.5",
                    unit="mmol/L",
                    evidence_text="空腹血糖 6.5 mmol/L",
                )
            ],
            page_count=1,
            success=True,
        )


@pytest.fixture
def client():
    """Create test client with mocked dependencies."""
    with (
        patch("app.data.mysql_client.get_mysql_client") as mock_mysql,
        patch(
            "app.service.vision_encoder.get_vision_encoder_service",
            return_value=MockVisionService(),
        ),
    ):
        # Mock MySQL
        mock_client = MagicMock()
        mock_mysql.return_value = mock_client

        from app.main import app

        yield TestClient(app)


def test_upload_report_endpoint(client, tmp_path):
    """Test report upload endpoint against a real in-memory SQLite database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        with SessionLocal() as session:
            yield session

    settings = SimpleNamespace(
        MAX_UPLOAD_FILES=20,
        MAX_UPLOAD_BYTES=20 * 1024 * 1024,
        MAX_UPLOAD_TOTAL_BYTES=50 * 1024 * 1024,
        REPORT_PARSE_WORKERS=4,
        REPORT_FILES_DIR=str(tmp_path),
    )
    with (
        patch(
            "app.api.report.get_vision_encoder_service",
            return_value=MockVisionService(),
        ),
        patch("app.api.report.get_settings", return_value=settings),
        patch("app.data.get_db", override_get_db),
    ):
        fake_image = b"fake png content"

        response = client.post(
            "/api/health/report/upload",
            data={"patient_id": "P001", "department": "内分泌科"},
            files={"file": ("test.png", io.BytesIO(fake_image), "text/html")},
        )

        assert response.status_code == 202, response.text
        data = response.json()
        assert data["id"] is not None
        assert data["patient_id"] == "P001"
        assert data["department"] == "内分泌科"
        assert data["report_type"] == "体检"
        assert data["status"] == "processing"
        assert isinstance(data["metrics"], list)
        token = data["access_token"]
        headers = {"X-Report-Token": token}
        parsed_response = client.get(
            f"/api/health/report/{data['id']}", headers=headers
        )
        parsed = parsed_response.json()
        assert (
            client.get(
                f"/api/health/report/{data['id']}",
                headers={"X-Report-Token": "wrong-token"},
            ).status_code
            == 404
        )
        assert parsed["status"] == "pending_confirmation"
        assert len(parsed["metrics"]) == 1

        assert parsed["files"] == [
            {
                "file_index": 1,
                "original_filename": "test.png",
                "media_type": "image/png",
                "page_count": 1,
                "source_url": f"/api/health/report/{data['id']}/files/1/pages/1",
            }
        ]
        source = client.get(parsed["files"][0]["source_url"], headers=headers)
        assert source.status_code == 200
        assert source.content == fake_image
        stored = tmp_path / str(data["id"]) / "1.png"
        assert stored.is_file()
        assert (
            client.delete(
                f"/api/health/report/{data['id']}", headers=headers
            ).status_code
            == 200
        )
        assert not stored.exists()


def test_metric_catalog_proxy_returns_evidence_service_catalog(client):
    catalog = [{"code": "fasting_glucose", "label": "空腹血糖"}]
    with patch("app.api.report.fetch_metric_catalog", return_value=catalog):
        response = client.get("/api/health/metric-catalog")

    assert response.status_code == 200
    assert response.json() == catalog


def test_get_report_endpoint_not_found(client):
    """Test getting non-existent report."""
    with patch("app.data.get_db") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.get("/api/health/report/999")

        # Should return 404
        assert response.status_code == 404


def test_upload_report_rejects_total_size_limit(client):
    settings = SimpleNamespace(
        MAX_UPLOAD_FILES=20,
        MAX_UPLOAD_BYTES=10,
        MAX_UPLOAD_TOTAL_BYTES=3,
    )
    with patch("app.api.report.get_settings", return_value=settings):
        response = client.post(
            "/api/health/report/upload",
            data={"patient_id": "P001"},
            files={"file": ("test.pdf", io.BytesIO(b"four"), "application/pdf")},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "报告文件总大小超过限制"


def test_list_reports_endpoint(client):
    """The old cross-report listing route is intentionally frozen."""
    response = client.get("/api/health/reports")
    assert response.status_code == 404


def test_delete_report_not_found(client):
    """Test deleting non-existent report."""
    with patch("app.data.get_db") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None

        response = client.delete("/api/health/report/999")

        assert response.status_code == 404
