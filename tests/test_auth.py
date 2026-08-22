"""Account/session and report ownership acceptance tests."""

import io
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.models import Base, MedicalReport
from app.main import app


@pytest.fixture
def account_client(monkeypatch):
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


def test_register_login_profile_logout(account_client):
    client, SessionLocal = account_client
    response = client.post(
        "/api/auth/register",
        json={
            "email": " Person@Example.com ",
            "password": "a-very-long-password",
            "display_name": "小明",
        },
    )
    assert response.status_code == 201, response.text
    account = response.json()
    assert account["email"] == "person@example.com"
    assert client.cookies.get("healthflow_session")
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    with SessionLocal() as db:
        from app.data.models import UserAccount

        saved = db.get(UserAccount, account["id"])
        assert saved.password_hash != "a-very-long-password"

    assert client.get("/api/auth/me").json()["id"] == account["id"]
    updated = client.patch("/api/auth/profile", json={"display_name": "新昵称"})
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "新昵称"

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"email": "person@example.com", "password": "a-very-long-password"},
    )
    assert login.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_report_history_is_account_scoped(account_client):
    client, SessionLocal = account_client
    account = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "password-123"},
    ).json()
    with SessionLocal() as db:
        db.add_all(
            [
                MedicalReport(
                    patient_id=account["id"],
                    owner_id=account["id"],
                    status="assessed",
                    exam_date=datetime.now(),
                ),
                MedicalReport(
                    patient_id="other",
                    owner_id="another-account",
                    status="assessed",
                    exam_date=datetime.now(),
                ),
            ]
        )
        db.commit()
    history = client.get("/api/auth/reports")
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["status"] == "assessed"


def test_report_endpoints_require_account_when_enabled(account_client):
    client, _ = account_client
    assert client.get("/api/health/report/1").status_code == 401
    assert client.post("/api/health/report/upload").status_code in {401, 422}


def test_upload_uses_account_owner_and_isolated_from_second_account(
    account_client, tmp_path
):
    client, SessionLocal = account_client
    from app.config import get_settings

    settings = get_settings()
    previous_dir = settings.REPORT_FILES_DIR
    settings.REPORT_FILES_DIR = str(tmp_path)
    try:
        client.post(
            "/api/auth/register",
            json={"email": "first@example.com", "password": "password-123"},
        )
        with patch("app.api.report.get_vision_encoder_service"):
            response = client.post(
                "/api/health/report/upload",
                files={"file": ("report.png", io.BytesIO(b"image"), "image/png")},
            )
        assert response.status_code == 202, response.text
        report_id = response.json()["id"]
        with SessionLocal() as db:
            report = db.get(MedicalReport, report_id)
            assert report.owner_id is not None
            first_owner = report.owner_id

        client.post("/api/auth/logout")
        client.post(
            "/api/auth/register",
            json={"email": "second@example.com", "password": "password-123"},
        )
        assert client.get(f"/api/health/report/{report_id}").status_code == 404
        assert first_owner != "anonymous"
    finally:
        settings.REPORT_FILES_DIR = previous_dir
