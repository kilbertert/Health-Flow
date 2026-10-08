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
            self._upgrade_report_file_page_count(connection)
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
    def _upgrade_report_file_page_count(connection) -> None:
        """把 `report_files.page_count` 从 `NOT NULL DEFAULT 1` 放开为可空。

        #170 之后 `NULL` 表示「读不出页数」（未知），与「共 1 页」是两件事。
        既有库的这一列是 `NOT NULL DEFAULT 1`，**而 SQLAlchemy 的显式 `None` 仍会
        撞上它** —— 于是「未知」在旧库上根本写不进去，会话直接失败。这不是理论的：
        服务宿主与开发库都是 `create_all` 之前建的老表。

        `create_all` 不改既有表（同 `_upgrade_session_owner_column` 的理由），
        所以这段必须显式执行。SQLite 不支持改列的 NOT NULL 约束，只能重建；
        MySQL 用 `MODIFY`。两边的重建都必须**按列名对齐**搬行，否则历史列会被
        静默丢掉——与 `_sqlite_rebuild_without_account_fk` 同一个教训。
        """
        inspector = inspect(connection)
        if "report_files" not in inspector.get_table_names():
            return
        columns = {column["name"]: column for column in inspector.get_columns("report_files")}
        page_column = columns.get("page_count")
        if page_column is None or page_column.get("nullable", True):
            return
        if connection.dialect.name == "mysql":
            connection.execute(text("ALTER TABLE report_files MODIFY page_count INTEGER NULL"))
            return
        _sqlite_make_page_count_nullable(connection)

    @staticmethod
    def _upgrade_session_owner_column(connection) -> None:
        """把 `user_sessions.account_id` 从「账号外键」升级为「主体标识」。

        #172 之后这一列装的是主体标识（`account:<tenant>:<sub>`），而既有库里它是
        `VARCHAR(36)` **且带指向 `user_accounts.id` 的外键**。两处都会挡住票据会话：
        外键让插入失败（主体不是账号行），长度让较长的主体标识被截断或拒收。

        `create_all` **不会**改既有表——这就是为什么这段必须显式执行。它只做两件
        幂等的事：丢外键（若存在）、把列宽扩到 128（若还窄）。

        **SQLite 也要做，不是只有 MySQL。** 早先这里写的是「SQLite 上是开发库，
        `create_all` 建出来的表本来就没有外键，无需升级」——但**既有库不是
        `create_all` 建的**：它是账号时代建的，带着那个外键。实测服务宿主
        （`/opt/health-flow/var/healthflow.db`）就是这种库：`account_id VARCHAR(36)`
        + `FOREIGN KEY(account_id) REFERENCES user_accounts(id)`，而 SQLAlchemy 的
        `connect` 钩子把 `PRAGMA foreign_keys=ON` 打开，于是**票据登录必定
        `IntegrityError`**（主体不是账号行），且只在真的兑换票据时才炸。
        """
        inspector = inspect(connection)
        if "user_sessions" not in inspector.get_table_names():
            return
        columns = {column["name"]: column for column in inspector.get_columns("user_sessions")}
        owner_column = columns.get("account_id")
        if owner_column is None:
            return
        has_account_fk = any(
            foreign_key.get("constrained_columns") == ["account_id"]
            for foreign_key in inspector.get_foreign_keys("user_sessions")
        )

        # 方言不同，DDL 也不同：MySQL 用 `DROP FOREIGN KEY` / `MODIFY`；SQLite 不支持
        # 改列，只能重建表——而且要**在旧 schema 上重建**，这正是既有部署的形状。
        dialect = connection.dialect.name
        if dialect == "mysql":
            if has_account_fk:
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
                    connection.execute(text("ALTER TABLE medical_reports MODIFY patient_id VARCHAR(128) NOT NULL"))
        elif dialect == "sqlite":
            if has_account_fk:
                _sqlite_rebuild_without_account_fk(connection)

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


def _sqlite_rebuild_without_account_fk(connection) -> None:
    """在 SQLite 上把 `user_sessions` 重建为不带账号外键的形状。

    SQLite 没有 `ALTER TABLE ... DROP CONSTRAINT`，所以重建是**唯一**的做法：
    建一张目标形状的表、把行搬过去、换名。两件事必须做对，否则会丢数据：

    - **搬行时按列名对齐**，不是按位置——旧表与模型的列序不保证一致。列集合从
      旧表自身读出，所以历史列不会被静默丢掉。
    - **重建索引**：索引不随数据搬过去。名字取自旧表自己的 `sqlite_master`，不猜
      SQLAlchemy 会给什么名字。

    `PRAGMA foreign_keys` 不需要动：外键定义在 `user_sessions` 自己身上（它引用
    `user_accounts`），没有任何表引用它，所以「先删行再删表」不会违反任何约束。

    事务由调用方的 `engine.begin()` 提供；这里不自己 commit。`PRAGMA foreign_keys`
    在事务内本就是 no-op，所以也没法在这里关——见上一段，不需要关。
    """
    columns = [row[1] for row in connection.execute(text("PRAGMA table_info(user_sessions)"))]
    index_sql = [
        row[0]
        for row in connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='user_sessions' AND sql IS NOT NULL")
        )
    ]
    quoted = ", ".join(f'"{name}"' for name in columns)
    connection.execute(text("DROP TABLE IF EXISTS user_sessions_migrating"))
    connection.execute(
        text(
            "CREATE TABLE user_sessions_migrating ("
            " id INTEGER NOT NULL,"
            " account_id VARCHAR(128) NOT NULL,"
            " token_hash VARCHAR(64) NOT NULL,"
            " created_at DATETIME NOT NULL,"
            " expires_at DATETIME NOT NULL,"
            " last_seen_at DATETIME NOT NULL,"
            " revoked_at DATETIME,"
            " PRIMARY KEY (id)"
            ")"
        )
    )
    connection.execute(text(f"INSERT INTO user_sessions_migrating ({quoted}) SELECT {quoted} FROM user_sessions"))
    connection.execute(text("DROP TABLE user_sessions"))
    connection.execute(text("ALTER TABLE user_sessions_migrating RENAME TO user_sessions"))
    for statement in index_sql:
        connection.execute(text(statement))


def _sqlite_make_page_count_nullable(connection) -> None:
    """在 SQLite 上把 `report_files` 重建为 `page_count` 可空的形状。

    与 `_sqlite_rebuild_without_account_fk` 同一套做法与同一条教训：**按列名对齐
    搬行**（旧表与模型的列序不保证一致，按位搬会静默错列），并从 `sqlite_master`
    重建它自己的索引（索引不随数据走）。事务由调用方的 `engine.begin()` 提供。

    `report_files` 被 `report_id` 外键指向 `medical_reports` —— 外键定义在它**自己**
    身上，没有表引用它，所以「先删行再删表」不违反任何约束，无需动
    `PRAGMA foreign_keys`。
    """
    columns = [row[1] for row in connection.execute(text("PRAGMA table_info(report_files)"))]
    index_sql = [
        row[0]
        for row in connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='report_files' AND sql IS NOT NULL")
        )
    ]
    quoted = ", ".join(f'"{name}"' for name in columns)
    connection.execute(text("DROP TABLE IF EXISTS report_files_migrating"))
    connection.execute(
        text(
            "CREATE TABLE report_files_migrating ("
            " id INTEGER NOT NULL,"
            " report_id INTEGER NOT NULL,"
            " file_index INTEGER NOT NULL,"
            " original_filename VARCHAR(255) NOT NULL,"
            " media_type VARCHAR(128) NOT NULL,"
            " stored_path VARCHAR(1024) NOT NULL,"
            " page_count INTEGER,"
            " created_at DATETIME,"
            " PRIMARY KEY (id),"
            " FOREIGN KEY(report_id) REFERENCES medical_reports (id)"
            ")"
        )
    )
    connection.execute(text(f"INSERT INTO report_files_migrating ({quoted}) SELECT {quoted} FROM report_files"))
    connection.execute(text("DROP TABLE report_files"))
    connection.execute(text("ALTER TABLE report_files_migrating RENAME TO report_files"))
    for statement in index_sql:
        connection.execute(text(statement))


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
