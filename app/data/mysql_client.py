"""Database client with SQLite development fallback and production URLs."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.data.models import Base


class MySQLClient:
    """Historical name retained for API compatibility; supports any SQLAlchemy URL."""

    def __init__(self) -> None:
        self.settings = get_settings()
        database_url = self.settings.database_url
        engine_kwargs: dict = {"pool_pre_ping": True}
        if database_url.startswith("sqlite"):
            Path("data").mkdir(parents=True, exist_ok=True)
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs.update({"pool_size": 10, "max_overflow": 20})

        self.engine = create_engine(database_url, **engine_kwargs)
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )
        self._tables_initialized = False

    def create_tables(self) -> None:
        Base.metadata.create_all(bind=self.engine)
        self._add_report_columns()
        self._tables_initialized = True

    def _add_report_columns(self) -> None:
        """Add phase-3 report fields when reusing a pre-bridge database."""
        additions = {
            "medical_reports": {
                "status": "VARCHAR(32) NOT NULL DEFAULT 'pending_confirmation'",
                "subject_consistency": "VARCHAR(16) DEFAULT 'same'",
                "evidence_result": "JSON",
                "access_token_hash": "VARCHAR(64)",
                "owner_id": "VARCHAR(128)",
                "extraction_provider": "VARCHAR(128)",
                "extraction_model": "VARCHAR(128)",
                "extraction_prompt_version": "VARCHAR(128)",
                "extraction_prompt_hash": "VARCHAR(128)",
                "extraction_run_id": "VARCHAR(128)",
                "provider_run_id": "VARCHAR(256)",
                "provider_run_ids": "TEXT",
                "evidence_correlation_id": "VARCHAR(64)",
                "updated_at": "DATETIME",
            },
            "metric_records": {
                "source_file_index": "INTEGER NOT NULL DEFAULT 1",
                "metric_code": "VARCHAR(64)",
                "confirmation_status": "VARCHAR(16) NOT NULL DEFAULT 'pending'",
                "confirmed_value": "VARCHAR(64)",
                "confirmed_unit": "VARCHAR(32)",
                "confirmed_reference_range": "VARCHAR(64)",
                "confirmed_evidence_text": "TEXT",
                "confirmed_at": "DATETIME",
            },
        }
        with self.engine.begin() as connection:
            inspector = inspect(connection)
            for table, columns in additions.items():
                existing = {column["name"] for column in inspector.get_columns(table)}
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(
                            text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                        )
            indexes = {
                index["name"]
                for index in inspect(connection).get_indexes("medical_reports")
            }
            if "ix_medical_reports_owner_id" not in indexes:
                connection.execute(
                    text(
                        "CREATE INDEX ix_medical_reports_owner_id ON medical_reports (owner_id)"
                    )
                )
            connection.execute(
                text(
                    "UPDATE medical_reports SET status = 'legacy_unclaimed' "
                    "WHERE access_token_hash IS NULL AND status <> 'legacy_unclaimed'"
                )
            )

    def drop_tables(self) -> None:
        Base.metadata.drop_all(bind=self.engine)

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()


_mysql_client: MySQLClient | None = None


def _enable_sqlite_foreign_keys(connection, _connection_record) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def get_mysql_client() -> MySQLClient:
    global _mysql_client
    if _mysql_client is None:
        _mysql_client = MySQLClient()
    return _mysql_client


def get_db() -> Generator[Session, None, None]:
    client = get_mysql_client()
    if not client._tables_initialized:
        client.create_tables()
    with client.get_session() as session:
        yield session
