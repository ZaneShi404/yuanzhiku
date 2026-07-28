"""PostgreSQL repository for the Compose deployment path.

The V1 services operate on plain dictionaries through ``RepositoryPort``. The
SQLite implementation remains the local default; this adapter provides the
same operations through SQLAlchemy connections without falling back to SQLite.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from app.adapters.sqlite import SqliteRepository, identifier, now, redact_url_userinfo


_JSON_COLUMNS = {"categories_json", "tags_json", "snapshot_json", "locator_json", "payload_json"}


class PostgresMigrationAdapter:
    """Small migration inventory retained for operational diagnostics/tests."""

    def __init__(self, migrations_directory: Path) -> None:
        self.migrations_directory = migrations_directory

    def migration_files(self) -> list[Path]:
        return sorted(self.migrations_directory.glob("*.sql"))


class _PostgresResult:
    """Expose the subset of the sqlite cursor interface used by shared methods."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.rowcount = result.rowcount

    @staticmethod
    def _mapping(row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        for column in _JSON_COLUMNS:
            if isinstance(value.get(column), (dict, list)):
                value[column] = json.dumps(value[column], ensure_ascii=False)
        for column, item in value.items():
            if isinstance(item, (datetime, date)):
                value[column] = item.isoformat()
        return value

    def fetchone(self) -> dict[str, Any] | None:
        return self._mapping(self._result.mappings().fetchone())

    def fetchall(self) -> list[dict[str, Any]]:
        return [self._mapping(row) or {} for row in self._result.mappings().fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _PostgresConnection:
    """Translate the small SQLite SQL subset used by ``SqliteRepository``."""

    def __init__(self, connection: Any, text: Any) -> None:
        self._connection = connection
        self._text = text

    @staticmethod
    def _translated_sql(statement: str, parameter_count: int) -> str:
        statement = statement.replace("title COLLATE NOCASE", "LOWER(title)")
        statement = statement.replace("name COLLATE NOCASE", "LOWER(name)")
        index = 0

        def replace_placeholder(_: re.Match[str]) -> str:
            nonlocal index
            name = f"p{index}"
            index += 1
            return f":{name}"

        translated = re.sub(r"\?", replace_placeholder, statement)
        if index != parameter_count:
            raise ValueError("PostgreSQL SQL parameter count mismatch")
        return translated

    def execute(self, statement: str, parameters: tuple[Any, ...] | list[Any] | None = None) -> _PostgresResult:
        values = list(parameters or [])
        evidence_columns = re.search(r"INSERT(?: OR IGNORE)? INTO evidence\(([^)]+)\)", statement)
        if evidence_columns is not None:
            columns = [column.strip() for column in evidence_columns.group(1).split(",")]
            if "is_validated" in columns:
                index = columns.index("is_validated")
                if isinstance(values[index], int):
                    values[index] = bool(values[index])
        ignore_conflicts = "INSERT OR IGNORE INTO" in statement
        statement = statement.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        statement = self._translated_sql(statement, len(values))
        if ignore_conflicts:
            statement = f"{statement} ON CONFLICT DO NOTHING"
        bound = {f"p{index}": value for index, value in enumerate(values)}
        return _PostgresResult(self._connection.execute(self._text(statement), bound))


class PostgresRepository(SqliteRepository):
    """Feature-complete V1 repository backed by PostgreSQL and Alembic."""

    backend = "postgresql"

    def __init__(self, database_url: str, migrations_directory: Path) -> None:
        self.database_url = database_url
        self.migrations_directory = migrations_directory
        self._engine: Any | None = None
        self._text: Any | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _sqlalchemy_url(database_url: str) -> str:
        """Use the locked psycopg driver for accepted bare PostgreSQL URLs."""
        lowered = database_url.lower()
        if lowered.startswith("postgresql://"):
            return f"postgresql+psycopg://{database_url[len('postgresql://') :]}"
        if lowered.startswith("postgres://"):
            return f"postgresql+psycopg://{database_url[len('postgres://') :]}"
        return database_url

    def initialize(self) -> None:
        try:
            from alembic import command
            from alembic.config import Config
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 运行时需要 SQLAlchemy、Alembic 和 psycopg") from exc

        sqlalchemy_url = self._sqlalchemy_url(self.database_url)
        engine: Any | None = None
        try:
            engine = create_engine(sqlalchemy_url, pool_pre_ping=True)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            if engine is not None:
                engine.dispose()
            raise RuntimeError(
                "无法连接配置的 PostgreSQL 数据库；请检查 "
                "YUANZHIKU_DATABASE_URL、PostgreSQL 服务、凭据和 psycopg driver"
            ) from exc

        project_root = self.migrations_directory.parents[1]
        configuration = Config(str(project_root / "alembic.ini"))
        configuration.set_main_option("script_location", str(project_root / "alembic"))
        configuration.set_main_option("sqlalchemy.url", sqlalchemy_url)
        try:
            # API and worker can start together in Compose. A transaction-scoped
            # advisory lock ensures only one process upgrades the shared schema.
            with engine.begin() as connection:
                connection.execute(text("SELECT pg_advisory_xact_lock(902807281)"))
                command.upgrade(configuration, "head")
        except Exception as exc:
            engine.dispose()
            raise RuntimeError("无法迁移 PostgreSQL schema；数据库未作为 SQLite 使用") from exc

        self._engine = engine
        self._text = text
        try:
            self._ensure_settings_defaults()
        except Exception:
            self._engine.dispose()
            self._engine = None
            self._text = None
            raise

    @contextmanager
    def connection(self) -> Iterator[_PostgresConnection]:
        if self._engine is None or self._text is None:
            raise RuntimeError("PostgreSQL repository 尚未初始化")
        with self._engine.begin() as connection:
            yield _PostgresConnection(connection, self._text)

    def _ensure_settings_defaults(self) -> None:
        defaults = {
            "parser_timeout_seconds": "86400",
            "parser_no_progress_seconds": "86400",
            "max_retry_attempts": "2",
            "last_backup_date": "",
        }
        with self.connection() as connection:
            for key, value in defaults.items():
                connection.execute(
                    "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) ON CONFLICT (key) DO NOTHING",
                    (key, value, now()),
                )

    def list_external_cards(self) -> list[dict[str, Any]]:
        rows = super().list_external_cards()
        for row in rows:
            row["url"] = redact_url_userinfo(row["url"])
        return rows

    def rows_for_export(self) -> dict[str, list[dict[str, Any]]]:
        tables = [
            "artifacts", "sources", "source_metadata_revisions", "content_versions", "source_relations",
            "representations", "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence",
            "external_cards", "topics", "topic_sources",
        ]
        with self.connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            rows = {table: self._rows(connection.execute(f"SELECT * FROM {table}").fetchall()) for table in tables}
        for card in rows["external_cards"]:
            card["url"] = redact_url_userinfo(card["url"])
        return rows

    def rows_for_backup(self) -> dict[str, list[dict[str, Any]]]:
        tables = [
            "settings", "artifacts", "sources", "source_metadata_revisions", "content_versions", "source_relations",
            "representations", "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence", "jobs",
            "job_attempts", "audit_events", "external_cards", "topics", "topic_sources",
        ]
        with self.connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            rows = {table: self._rows(connection.execute(f"SELECT * FROM {table}").fetchall()) for table in tables}
        for card in rows["external_cards"]:
            card["url"] = redact_url_userinfo(card["url"])
        return rows

    def insert_backup_rows(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        tables = [
            "settings", "artifacts", "sources", "source_metadata_revisions", "content_versions", "source_relations",
            "representations", "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence", "jobs",
            "job_attempts", "audit_events", "external_cards", "topics", "topic_sources",
        ]
        with self.connection() as connection:
            for table in tables:
                for row in rows_by_table.get(table, []):
                    columns = list(row)
                    placeholders = ",".join("?" for _ in columns)
                    connection.execute(
                        f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
                        [row[column] for column in columns],
                    )

    def prepare_backup_restore(self) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM settings")

    def has_user_records(self) -> bool:
        tables = (
            "artifacts", "sources", "source_metadata_revisions", "content_versions", "source_relations",
            "representations", "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence",
            "jobs", "job_attempts", "audit_events", "external_cards", "topics", "topic_sources", "backups",
        )
        with self.connection() as connection:
            return any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None for table in tables)

    def claim_next_job(self) -> dict[str, Any] | None:
        """Claim one job atomically across the separate Compose API/worker processes."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT id FROM jobs WHERE state IN ('queued','retry_wait') "
                "ORDER BY priority DESC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
            ).fetchone()
            if row is None:
                return None
            job_id = row["id"]
            stamp = now()
            updated = connection.execute(
                "UPDATE jobs SET state='running', attempt_count=attempt_count+1, started_at=?, heartbeat_at=?, updated_at=? "
                "WHERE id=? AND state IN ('queued','retry_wait')",
                (stamp, stamp, stamp, job_id),
            ).rowcount
            if not updated:
                return None
            current = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            assert current is not None
            connection.execute(
                "INSERT INTO job_attempts(id,job_id,attempt_number,state,started_at) VALUES(?,?,?,?,?)",
                (identifier(), job_id, current["attempt_count"], "running", stamp),
            )
            return current
