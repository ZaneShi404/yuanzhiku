from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from app.adapters.postgres import PostgresRepository
from app.adapters.sqlite import SqliteRepository
from app.core.config import DatabaseUrlConfigurationError, data_paths, database_backend
from app.main import ApplicationServices


RUN_ROOT = Path(__file__).resolve().parents[1] / "runtime" / "pgfix-20260728T173841Z"
COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / "database-url-selection"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def compose_database_urls() -> list[str]:
    return re.findall(
        r"^\s*YUANZHIKU_DATABASE_URL:\s*([^\s#]+)",
        COMPOSE_PATH.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:password@localhost:5432/yuanzhiku",
        "postgres://user:password@localhost:5432/yuanzhiku",
        "postgresql+psycopg://user:password@localhost:5432/yuanzhiku",
        "postgresql+asyncpg://user:password@localhost:5432/yuanzhiku",
    ],
)
def test_postgresql_url_variants_select_postgresql_backend(url: str) -> None:
    assert database_backend(url) == "postgresql"


def test_compose_driver_url_reaches_postgres_adapter_without_sqlite_fallback(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = compose_database_urls()
    assert urls
    assert all(database_backend(url) == "postgresql" for url in urls)
    selected_urls: list[str] = []

    def selected_postgres_adapter(self: PostgresRepository) -> None:
        selected_urls.append(self.database_url)
        raise RuntimeError("selected PostgreSQL adapter")

    monkeypatch.setenv("YUANZHIKU_DATABASE_URL", urls[0])
    monkeypatch.setattr(PostgresRepository, "initialize", selected_postgres_adapter)

    with pytest.raises(RuntimeError, match="selected PostgreSQL adapter"):
        ApplicationServices(data_paths(runtime_root))

    assert selected_urls == [urls[0]]
    assert not (runtime_root / "state" / "knowledge.db").exists()


def test_postgresql_driver_url_fails_closed_without_sqlite(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "YUANZHIKU_DATABASE_URL",
        "postgresql+psycopg://invalid:invalid@127.0.0.1:1/invalid",
    )

    with pytest.raises(
        RuntimeError,
        match="PostgreSQL 运行时需要 SQLAlchemy、Alembic 和 psycopg|无法连接配置的 PostgreSQL 数据库",
    ):
        ApplicationServices(data_paths(runtime_root))

    assert not (runtime_root / "state" / "knowledge.db").exists()


def test_unsupported_database_url_fails_before_sqlite_creation(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YUANZHIKU_DATABASE_URL", "mysql+pymysql://user:password@localhost/database")

    with pytest.raises(DatabaseUrlConfigurationError, match="YUANZHIKU_DATABASE_URL"):
        ApplicationServices(data_paths(runtime_root))

    assert not (runtime_root / "state" / "knowledge.db").exists()


def test_default_database_url_retains_local_sqlite(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YUANZHIKU_DATABASE_URL", raising=False)

    services = ApplicationServices(data_paths(runtime_root))

    assert isinstance(services.repository, SqliteRepository)
    assert (runtime_root / "state" / "knowledge.db").is_file()
