"""SQLAlchemy/Alembic PostgreSQL bootstrap for the container deployment path.

The feature-complete local repository remains SQLite. This adapter deliberately
fails closed when PostgreSQL is selected before the repository behavior is
implemented, rather than silently writing SQLite data while Compose claims to
run PostgreSQL.
"""

from __future__ import annotations

from pathlib import Path


class PostgresMigrationAdapter:
    def __init__(self, migrations_directory: Path) -> None:
        self.migrations_directory = migrations_directory

    def migration_files(self) -> list[Path]:
        return sorted(self.migrations_directory.glob("*.sql"))


class PostgresRepository:
    """PostgreSQL URL bootstrap with Alembic-managed schema migration."""

    def __init__(self, database_url: str, migrations_directory: Path) -> None:
        self.database_url = database_url
        self.migrations_directory = migrations_directory

    def initialize(self) -> None:
        try:
            from alembic import command
            from alembic.config import Config
            from sqlalchemy import create_engine, text
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 运行时需要 SQLAlchemy、Alembic 和 psycopg") from exc
        try:
            engine = create_engine(self.database_url, pool_pre_ping=True)
        except Exception as exc:
            raise RuntimeError(
                "无法初始化 PostgreSQL URL；请使用已安装 SQLAlchemy driver 的 URL，"
                "例如 postgresql+psycopg://..."
            ) from exc
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise RuntimeError(
                "无法连接配置的 PostgreSQL 数据库；请检查 "
                "YUANZHIKU_DATABASE_URL、PostgreSQL 服务和凭据"
            ) from exc
        finally:
            engine.dispose()
        config = Config()
        config.set_main_option("script_location", str(self.migrations_directory.parent / "alembic"))
        config.set_main_option("sqlalchemy.url", self.database_url)
        command.upgrade(config, "head")
        raise RuntimeError(
            "PostgreSQL schema 已迁移，但应用 PostgreSQL repository 尚未实现；"
            "拒绝回退到 SQLite。请显式配置 SQLite URL 以运行本地模式。"
        )
