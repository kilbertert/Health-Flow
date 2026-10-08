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
    msg2 = ChatMessage(session_id=session.id, role="assistant", content="您的空腹血糖为6.5mmol/L...")
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
            text("INSERT INTO medical_reports(id, patient_id, status) VALUES (1, 'legacy-patient', 'assessed')")
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
            connection.execute(text("SELECT status, access_token_hash, owner_id FROM medical_reports WHERE id = 1"))
            .mappings()
            .one()
        )
        index_names = {item["name"] for item in inspect(connection).get_indexes("medical_reports")}
    client.close()

    assert dict(row) == {
        "status": "legacy_unclaimed",
        "access_token_hash": None,
        "owner_id": None,
    }
    assert "ix_medical_reports_owner_id" in index_names


def test_create_tables_makes_a_legacy_page_count_column_nullable(tmp_path, monkeypatch):
    """旧库的 `report_files.page_count` 是 `NOT NULL DEFAULT 1`，会挡住「未知」。

    #170 之后 `NULL` 表示「读不出页数」。SQLAlchemy 的列默认值会在显式传 `None`
    时也生效 —— 所以只要这一列还是 `NOT NULL`，「未知」要么写不进去（会话直接
    `IntegrityError`），要么被悄悄改回 1。两种都不是我们能接受的，而既有部署
    （服务宿主与开发库）的表正是 `create_all` 之前建的。

    这条在**由旧 schema 建起的库**上测，并且断言三件事：约束真的放开了、历史行
    的取值原样保留、迁移之后 `None` 真的能落库。
    """
    from sqlalchemy import inspect

    from app.data.mysql_client import MySQLClient

    database_path = tmp_path / "legacy-page-count.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE medical_reports (id INTEGER PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE report_files ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " report_id INTEGER NOT NULL,"
                " file_index INTEGER NOT NULL,"
                " original_filename VARCHAR(255) NOT NULL,"
                " media_type VARCHAR(128) NOT NULL,"
                " stored_path VARCHAR(1024) NOT NULL,"
                " page_count INTEGER NOT NULL DEFAULT 1,"
                " created_at DATETIME,"
                " FOREIGN KEY(report_id) REFERENCES medical_reports (id)"
                ")"
            )
        )
        connection.execute(text("CREATE INDEX ix_report_files_report_id ON report_files (report_id)"))
        connection.execute(text("INSERT INTO medical_reports (id) VALUES (7)"))
        connection.execute(
            text(
                "INSERT INTO report_files (report_id, file_index, original_filename, media_type,"
                " stored_path, page_count) VALUES (7, 1, 'legacy.pdf', 'application/pdf', '/tmp/x', 3)"
            )
        )
    engine.dispose()

    client = MySQLClient.__new__(MySQLClient)
    client.engine = create_engine(f"sqlite:///{database_path}")
    client.create_tables()

    with client.engine.begin() as connection:
        column = next(c for c in inspect(connection).get_columns("report_files") if c["name"] == "page_count")
        kept = connection.execute(text("SELECT page_count FROM report_files WHERE id = 1")).scalar_one()
        indexes = {item["name"] for item in inspect(connection).get_indexes("report_files")}
        # 迁移之后，「未知」真的写得进去。
        connection.execute(text("UPDATE report_files SET page_count = NULL WHERE id = 1"))
        written = connection.execute(text("SELECT page_count FROM report_files WHERE id = 1")).scalar_one()
    client.engine.dispose()

    assert column["nullable"] is True
    assert kept == 3, "历史行的页数必须原样保留，不能被重建搬丢或改写"
    # 重建表时不重建索引就会静默丢掉它们 —— 外键列上的索引一丢，报告删除的
    # 级联清理就退化成全表扫描。
    assert "ix_report_files_report_id" in indexes
    assert written is None


def test_page_count_upgrade_is_idempotent(tmp_path, monkeypatch):
    """第二次启动不能重复重建（重建两次是两次搬数据的风险）。"""
    from sqlalchemy import inspect

    from app.data.mysql_client import MySQLClient

    database_path = tmp_path / "idempotent.db"
    client = MySQLClient.__new__(MySQLClient)
    client.engine = create_engine(f"sqlite:///{database_path}")
    client.create_tables()
    client.create_tables()

    with client.engine.connect() as connection:
        column = next(c for c in inspect(connection).get_columns("report_files") if c["name"] == "page_count")
    client.engine.dispose()

    assert column["nullable"] is True


def test_sqlite_foreign_keys_are_enabled_for_report_cleanup(tmp_path, monkeypatch):
    from app.data.mysql_client import MySQLClient

    database_path = tmp_path / "foreign-keys.db"
    monkeypatch.setattr(
        "app.data.mysql_client.get_settings",
        lambda: type("Settings", (), {"database_url": f"sqlite:///{database_path}"})(),
    )
    client = MySQLClient()
    client.create_tables()
    with client.engine.begin() as connection:
        report_id = connection.execute(
            text(
                "INSERT INTO medical_reports(patient_id, status) "
                "VALUES ('patient', 'pending_confirmation') RETURNING id"
            )
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO report_extraction_jobs(report_id, status, attempt_count) VALUES (:report_id, 'queued', 0)"
            ),
            {"report_id": report_id},
        )
        connection.execute(
            text("DELETE FROM medical_reports WHERE id = :report_id"),
            {"report_id": report_id},
        )
        remaining = connection.execute(
            text("SELECT count(*) FROM report_extraction_jobs WHERE report_id = :report_id"),
            {"report_id": report_id},
        ).scalar_one()
    client.close()
    assert remaining == 0
