"""Tests for MySQL data access layer."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.data.models import Base, ChatMessage, ChatSession, MedicalReport


@pytest.fixture
def test_db():
    """Create a test database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def test_create_medical_report(test_db):
    """Test creating a medical report."""
    report = MedicalReport(patient_id="P001", report_type="体检", department="内分泌科")
    test_db.add(report)
    test_db.commit()
    test_db.refresh(report)

    assert report.id is not None
    assert report.patient_id == "P001"
    assert report.report_type == "体检"
    assert report.department == "内分泌科"


def test_create_chat_session(test_db):
    """Test creating a chat session."""
    session = ChatSession(patient_id="P001", current_department="内分泌科")
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)

    assert session.id is not None
    assert session.patient_id == "P001"
    assert session.conversation_summary is None  # default


def test_chat_session_with_messages(test_db):
    """Test chat session with messages."""
    session = ChatSession(patient_id="P001", current_department="内分泌科")
    test_db.add(session)
    test_db.commit()

    msg1 = ChatMessage(session_id=session.id, role="user", content="我空腹血糖有点高")
    msg2 = ChatMessage(
        session_id=session.id, role="assistant", content="您的空腹血糖为6.5mmol/L..."
    )
    test_db.add_all([msg1, msg2])
    test_db.commit()

    # Refresh session to load messages
    test_db.refresh(session)
    assert len(session.messages) == 2
    assert session.messages[0].role == "user"
    assert session.messages[1].role == "assistant"


def test_legacy_reports_are_sealed_when_owner_columns_are_added(tmp_path, monkeypatch):
    from app.data.mysql_client import MySQLClient

    database_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE medical_reports (
                    id INTEGER PRIMARY KEY,
                    patient_id VARCHAR(64) NOT NULL,
                    report_type VARCHAR(32),
                    file_url VARCHAR(512),
                    parsed_content JSON,
                    exam_date DATETIME,
                    department VARCHAR(64),
                    status VARCHAR(32) NOT NULL DEFAULT 'pending_confirmation',
                    subject_consistency VARCHAR(16) DEFAULT 'same',
                    evidence_result JSON,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO medical_reports(id, patient_id, status) "
                "VALUES (1, 'legacy-patient', 'assessed')"
            )
        )
    engine.dispose()
    monkeypatch.setattr(
        "app.data.mysql_client.get_settings",
        lambda: SimpleNamespace(database_url=f"sqlite:///{database_path}"),
    )

    client = MySQLClient()
    client.create_tables()
    with client.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT status, access_token_hash, owner_id "
                    "FROM medical_reports WHERE id = 1"
                )
            )
            .mappings()
            .one()
        )
        index_names = {
            item["name"] for item in inspect(connection).get_indexes("medical_reports")
        }
    client.close()

    assert dict(row) == {
        "status": "legacy_unclaimed",
        "access_token_hash": None,
        "owner_id": None,
    }
    assert "ix_medical_reports_owner_id" in index_names
