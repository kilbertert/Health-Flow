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
        """Add report/account fields when reusing a pre-account database."""
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
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
            indexes = {index["name"] for index in inspect(connection).get_indexes("medical_reports")}
            if "ix_medical_reports_owner_id" not in indexes:
                connection.execute(text("CREATE INDEX ix_medical_reports_owner_id ON medical_reports (owner_id)"))
            self._upgrade_session_owner_column(connection)
            # **只在真正无主的旧行上打标记。** 这条原先按 `access_token_hash IS NULL`
            # 判定，而 #172 之后新上传的报告**本来就不再有令牌**——于是每次启动都会把
            # 新报告改写成 `legacy_unclaimed`（`confirmed`/`assessed` 会被抹掉，
            # 之后 assess 返回 409）。加 `owner_id` 条件把范围收回「旧的无主行」。
            connection.execute(
                text(
                    "UPDATE medical_reports SET status = 'legacy_unclaimed' "
                    "WHERE access_token_hash IS NULL "
                    "AND (owner_id IS NULL OR owner_id = :unowned) "
                    "AND status <> 'legacy_unclaimed'"
                ),
                {"unowned": "anonymous"},
            )

    @staticmethod
    def _upgrade_session_owner_column(connection) -> None:
        """把 `user_sessions.account_id` 从「账号外键」升级为「主体标识」。

        #172 之后这一列装的是主体标识（`account:<tenant>:<sub>`），而既有库里它是
        `VARCHAR(36)` **且带指向 `user_accounts.id` 的外键**。两处都会挡住票据会话：
        外键让插入失败（主体不是账号行），长度让较长的主体标识被截断或拒收。

        `create_all` **不会**改既有表——这就是为什么这段必须显式执行。它只做两件
        幂等的事：丢外键（若存在）、把列宽扩到 128（若还窄）。在不带外键的 SQLite
        上，第一件自然跳过。
        """
        inspector = inspect(connection)
        if "user_sessions" not in inspector.get_table_names():
            return
        columns = {column["name"]: column for column in inspector.get_columns("user_sessions")}
        owner_column = columns.get("account_id")
        if owner_column is None:
            return

        # 方言不同，DDL 也不同：MySQL 用 `DROP FOREIGN KEY` / `MODIFY`，SQLite 不支持
        # 改列（只能重建表）。这里只处理**需要升级的那种部署**（生产是 MySQL）；
        # SQLite 上是开发库，`create_all` 建出来的表本来就没有外键，无需升级。
        dialect = connection.dialect.name
        if dialect == "mysql":
            for foreign_key in inspector.get_foreign_keys("user_sessions"):
                if foreign_key.get("constrained_columns") == ["account_id"]:
                    name = foreign_key.get("name")
                    if name:
                        connection.execute(text(f"ALTER TABLE user_sessions DROP FOREIGN KEY {name}"))
            length = getattr(owner_column["type"], "length", None)
            if length is not None and length < 128:
                connection.execute(text("ALTER TABLE user_sessions MODIFY account_id VARCHAR(128) NOT NULL"))

            # `patient_id` 同样承载主体标识（上传时从它复制），旧库是 VARCHAR(64)。
            report_columns = {column["name"]: column for column in inspector.get_columns("medical_reports")}
            patient_column = report_columns.get("patient_id")
            if patient_column is not None:
                patient_length = getattr(patient_column["type"], "length", None)
                if patient_length is not None and patient_length < 128:
                    connection.execute(
                        text("ALTER TABLE medical_reports MODIFY patient_id VARCHAR(128) NOT NULL")
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
