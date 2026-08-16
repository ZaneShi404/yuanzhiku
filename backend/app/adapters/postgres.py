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

from app.adapters.sqlite import BACKUP_TABLES, EXPORT_TABLES, SqliteRepository, identifier, now, redact_url_userinfo


_JSON_COLUMNS = {"domains_json", "genres_json", "tags_json", "snapshot_json", "locator_json", "payload_json", "metadata_json"}
_MIGRATION_ADVISORY_LOCK = 902807281


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

        self._engine = engine
        self._text = text
        try:
            self._assert_schema_ready()
            self._ensure_settings_defaults()
        except Exception:
            self._engine.dispose()
            self._engine = None
            self._text = None
            raise

    def _alembic_config(self):
        from alembic.config import Config

        project_root = self.migrations_directory.parents[1]
        configuration = Config(str(project_root / "alembic.ini"))
        configuration.set_main_option("script_location", str(project_root / "alembic"))
        configuration.set_main_option("sqlalchemy.url", self._sqlalchemy_url(self.database_url))
        return configuration

    def _assert_schema_ready(self) -> None:
        try:
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory

            configuration = self._alembic_config()
            expected_heads = set(ScriptDirectory.from_config(configuration).get_heads())
            assert self._engine is not None
            with self._engine.connect() as connection:
                actual_heads = set(MigrationContext.configure(connection).get_current_heads())
        except Exception as exc:
            raise RuntimeError("无法检查 PostgreSQL schema 状态；数据库未作为 SQLite 使用") from exc
        if actual_heads != expected_heads:
            raise RuntimeError("PostgreSQL schema 未就绪；请先运行专用 migrate 服务")

    def assert_empty_restore_target(self) -> None:
        """Reject a restore database that already contains user-visible tables."""
        try:
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 还原需要 SQLAlchemy、Alembic 和 psycopg") from exc

        engine: Any | None = None
        try:
            engine = create_engine(self._sqlalchemy_url(self.database_url), pool_pre_ping=True)
            with engine.connect() as connection:
                tables = connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = current_schema() "
                        "AND table_type = 'BASE TABLE'"
                    )
                ).scalars().all()
        except Exception as exc:
            raise RuntimeError("无法检查 PostgreSQL 还原目标；数据库未作为 SQLite 使用") from exc
        finally:
            if engine is not None:
                engine.dispose()
        if tables:
            raise ValueError("PostgreSQL 还原目标必须为空")

    def migrate_to_head(self) -> None:
        """Provision an explicit PostgreSQL target; API and workers never call this."""
        try:
            from alembic import command
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 迁移需要 SQLAlchemy、Alembic 和 psycopg") from exc
        engine: Any | None = None
        try:
            engine = create_engine(self._sqlalchemy_url(self.database_url), pool_pre_ping=True)
            with engine.connect() as connection:
                connection.execute(
                    text("SELECT pg_advisory_lock(:lock_id)"),
                    {"lock_id": _MIGRATION_ADVISORY_LOCK},
                )
                try:
                    command.upgrade(self._alembic_config(), "head")
                finally:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": _MIGRATION_ADVISORY_LOCK},
                    )
        except Exception as exc:
            raise RuntimeError("无法迁移 PostgreSQL schema；数据库未作为 SQLite 使用") from exc
        finally:
            if engine is not None:
                engine.dispose()

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
            "parser_memory_limit_mb": "2048",
            "parser_disk_limit_mb": "1024",
            "video_timeout_seconds": "3600",
            "video_memory_limit_mb": "2048",
            "video_disk_limit_mb": "1024",
            "video_max_frames": "12",
            "image_timeout_seconds": "3600",
            "image_memory_limit_mb": "2048",
            "image_disk_limit_mb": "1024",
            "job_lease_seconds": "300",
            "max_retry_attempts": "2",
            "download_timeout_seconds": "3600",
            "download_no_progress_seconds": "10",
            "download_disk_limit_mb": "2048",
            "ai_transcribe_provider": "off",
            "ai_transcribe_base_url": "",
            "ai_transcribe_model": "whisper-1",
            "ai_understand_provider": "off",
            "ai_understand_base_url": "",
            "ai_chat_model": "qwen-plus",
            "ai_vision_model": "",
            "ai_timeout_seconds": "300",
            "last_backup_date": "",
            "last_integrity_sample_date": "",
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
        with self.connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            rows = {table: self._rows(connection.execute(f"SELECT * FROM {table}").fetchall()) for table in EXPORT_TABLES}
        for card in rows["external_cards"]:
            card["url"] = redact_url_userinfo(card["url"])
        return rows

    def rows_for_backup(self) -> dict[str, list[dict[str, Any]]]:
        with self.connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            rows = {table: self._rows(connection.execute(f"SELECT * FROM {table}").fetchall()) for table in BACKUP_TABLES}
        for card in rows["external_cards"]:
            card["url"] = redact_url_userinfo(card["url"])
        return rows

    def insert_backup_rows(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        with self.connection() as connection:
            for table in BACKUP_TABLES:
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
            "artifacts", "sources", "source_metadata_revisions", "content_versions", "video_analyses", "video_frames", "source_relations",
            "representations", "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence",
            "jobs", "job_attempts", "audit_events", "external_cards", "topics", "topic_sources", "backups",
            "video_download_provenance",
        )
        with self.connection() as connection:
            return any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None for table in tables)

    def claim_next_job(self) -> dict[str, Any] | None:
        """Reuse fenced leasing with PostgreSQL row-level claim locks."""
        return super().claim_next_job()
