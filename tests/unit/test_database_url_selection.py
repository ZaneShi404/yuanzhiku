from __future__ import annotations

import os
import re
import shutil
import sys
import types
import uuid
from pathlib import Path

import pytest

from app.adapters.postgres import PostgresRepository
from app.adapters.sqlite import SqliteRepository
from app.core.config import DatabaseUrlConfigurationError, data_paths, database_backend
from app.main import ApplicationServices


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "database-url-selection"
COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"
DOCKERFILE_PATH = Path(__file__).resolve().parents[2] / "Dockerfile"
DOCKERIGNORE_PATH = Path(__file__).resolve().parents[2] / ".dockerignore"


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
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


def test_postgres_initialize_checks_schema_without_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = PostgresRepository(
        "postgresql+psycopg://user:password@127.0.0.1:5432/yuanzhiku",
        Path(__file__).resolve().parents[2] / "backend" / "migrations" / "postgresql",
    )
    calls: list[str] = []

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, _statement: object) -> None:
            return None

    class FakeEngine:
        def connect(self) -> FakeConnection:
            return FakeConnection()

        def dispose(self) -> None:
            calls.append("dispose")

    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.create_engine = lambda *_args, **_kwargs: FakeEngine()
    sqlalchemy.text = lambda statement: statement
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy)
    monkeypatch.setattr(repository, "_assert_schema_ready", lambda: calls.append("schema"))
    monkeypatch.setattr(repository, "_ensure_settings_defaults", lambda: calls.append("settings"))
    monkeypatch.setattr(
        repository,
        "migrate_to_head",
        lambda: (_ for _ in ()).throw(AssertionError("initialize must not migrate")),
    )

    repository.initialize()

    assert calls == ["schema", "settings"]


def test_compose_assigns_migrations_to_one_shot_service_and_web_uses_built_output() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    dockerignore = DOCKERIGNORE_PATH.read_text(encoding="utf-8")

    assert 'image: yuanzhiku-application:local' in compose
    assert compose.count('image: yuanzhiku-application:local') == 4
    assert 'command: ["python", "-m", "app.migrate"]' in compose
    assert compose.count("condition: service_completed_successfully") == 5
    assert "frontend/dist:/usr/share/nginx/html" not in compose
    assert "target: web" in compose
    assert "COPY frontend/package.json frontend/package-lock.json ./" in dockerfile
    assert "RUN npm ci --ignore-scripts" in dockerfile
    assert "COPY --from=web-build /workspace/frontend/dist /usr/share/nginx/html" in dockerfile
    assert "COPY --from=web-build /workspace/frontend/dist ./frontend/dist" not in dockerfile
    assert "frontend/dist" in dockerignore


def test_default_database_url_retains_local_sqlite(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YUANZHIKU_DATABASE_URL", raising=False)

    services = ApplicationServices(data_paths(runtime_root))

    assert isinstance(services.repository, SqliteRepository)
    assert (runtime_root / "state" / "knowledge.db").is_file()


def test_compose_hardening_static_contract() -> None:
    """Task 14（加固计划）：Compose 服务与数据库权限收紧的静态契约。"""
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    # 代码中没有任何 Redis 消费者：base Compose 不得再包含 Redis。
    assert "redis" not in compose.lower()
    # PostgreSQL 主机端口只存在于显式 debug 文件；base 不发布数据库端口。
    assert "54329" not in compose
    assert "56379" not in compose
    assert "6379" not in compose
    # 禁止固定密码：口令一律经必填环境变量注入。
    assert "yuanzhiku_local_only" not in compose
    assert "YUANZHIKU_DB_ADMIN_PASSWORD" in compose
    assert "YUANZHIKU_DB_APP_PASSWORD" in compose
    # admin/app 角色分离：migrate 用 admin URL，api/worker 用 app URL。
    assert "yuanzhiku_admin" in compose and "yuanzhiku_app" in compose
    assert "grant-postgres-app-role.py" in compose
    debug_path = COMPOSE_PATH.parent / "docker-compose.debug.yml"
    assert debug_path.is_file(), "需要显式的 debug 端口覆盖文件"
    debug = debug_path.read_text(encoding="utf-8")
    assert "127.0.0.1:54329:5432" in debug
    grant = (COMPOSE_PATH.parent / "scripts" / "grant-postgres-app-role.py").read_text(encoding="utf-8")
    assert "sql.Identifier" in grant, "角色/对象名必须经 Identifier 参数化"
    assert "CREATE ROLE" in grant and "GRANT" in grant
    assert (COMPOSE_PATH.parent / "scripts" / "new-compose-secrets.ps1").is_file()
    assert "/.env" in (COMPOSE_PATH.parent / ".gitignore").read_text(encoding="utf-8")
