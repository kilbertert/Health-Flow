"""Tests for FastAPI main application."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import _valid_basic_auth, app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert data["version"] == "0.1.0"


def test_root(client):
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "HealthFlow" in data["message"]
    assert data["version"] == "0.1.0"
    assert data["docs"] == "/docs"


def test_basic_auth_validation():
    import base64

    valid = base64.b64encode(b"reviewer:secret").decode()
    assert _valid_basic_auth(f"Basic {valid}", "reviewer", "secret") is True
    assert _valid_basic_auth(f"Basic {valid}", "reviewer", "wrong") is False
    assert _valid_basic_auth("Bearer token", "reviewer", "secret") is False
    assert _valid_basic_auth("", "reviewer", "") is True


def test_basic_auth_middleware_challenges_and_accepts(client):
    settings = get_settings()
    previous_user = settings.HEALTHFLOW_BASIC_USER
    previous_password = settings.HEALTHFLOW_BASIC_PASSWORD
    settings.HEALTHFLOW_BASIC_USER = "reviewer"
    settings.HEALTHFLOW_BASIC_PASSWORD = "secret"
    try:
        denied = client.get("/")
        assert denied.status_code == 401
        assert denied.headers["www-authenticate"].startswith("Basic ")
        assert client.get("/", auth=("reviewer", "secret")).status_code == 200
        assert client.get("/health").status_code == 200
    finally:
        settings.HEALTHFLOW_BASIC_USER = previous_user
        settings.HEALTHFLOW_BASIC_PASSWORD = previous_password


def test_ready_requires_a_full_evidence_api_key(client):
    settings = get_settings()
    previous_url = settings.GENESIS_EVIDENCE_API_URL
    previous_key = settings.GENESIS_EVIDENCE_API_KEY
    previous_vllm_key = settings.VLLM_API_KEY
    previous_openai_key = settings.OPENAI_API_KEY
    previous_model = settings.VLLM_MODEL
    settings.GENESIS_EVIDENCE_API_URL = "http://127.0.0.1:8125/api/evidence/matches"
    settings.VLLM_API_KEY = ""
    settings.OPENAI_API_KEY = ""
    settings.VLLM_MODEL = "gpt-5.6-sol"
    try:
        settings.GENESIS_EVIDENCE_API_KEY = "short"
        degraded = client.get("/ready")
        assert degraded.status_code == 200
        assert degraded.json()["status"] == "degraded"
        assert degraded.json()["evidence_service"] == "unconfigured"
        assert degraded.json()["report_provider"] == "unconfigured"

        settings.GENESIS_EVIDENCE_API_KEY = "a" * 24
        still_degraded = client.get("/ready")
        assert still_degraded.status_code == 200
        assert still_degraded.json()["status"] == "degraded"
        assert still_degraded.json()["evidence_service"] == "configured"

        settings.OPENAI_API_KEY = "provider-key"
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["report_provider"] == "configured"
    finally:
        settings.GENESIS_EVIDENCE_API_URL = previous_url
        settings.GENESIS_EVIDENCE_API_KEY = previous_key
        settings.VLLM_API_KEY = previous_vllm_key
        settings.OPENAI_API_KEY = previous_openai_key
        settings.VLLM_MODEL = previous_model
