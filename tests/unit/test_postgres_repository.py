from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from app.adapters.postgres import PostgresRepository
from app.adapters.sqlite import BACKUP_TABLE_COLUMNS, BACKUP_TABLES
from app.core.config import data_paths
from app.domain.models import PasteImportRequest
from app.main import ApplicationServices, create_app


RUN_ROOT = Path(__file__).resolve().parents[1] / "runtime" / "backupfix-20260729T113000Z"
MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations" / "postgresql"
POSTGRES_TEST_URL = os.environ.get("POSTGRES_TEST_URL")
POSTGRES_RESTORE_TEST_URL = os.environ.get("POSTGRES_RESTORE_TEST_URL")


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_postgres_repository_construction_and_application_dispatch(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "postgresql+psycopg://user:password@127.0.0.1:5432/yuanzhiku"
    repository = PostgresRepository(url, MIGRATIONS)
    assert repository.backend == "postgresql"
    assert repository.database_url == url
    assert repository._sqlalchemy_url("postgresql://user:password@db/knowledge") == "postgresql+psycopg://user:password@db/knowledge"
    assert repository._sqlalchemy_url("postgres://user:password@db/knowledge") == "postgresql+psycopg://user:password@db/knowledge"

    selected: list[PostgresRepository] = []

    def initialized(self: PostgresRepository) -> None:
        selected.append(self)

    monkeypatch.setenv("YUANZHIKU_DATABASE_URL", url)
    monkeypatch.setattr(PostgresRepository, "initialize", initialized)
    services = ApplicationServices(data_paths(runtime_root))

    assert isinstance(services.repository, PostgresRepository)
    assert services.database_backend == "postgresql"
    assert selected == [services.repository]
    assert not (runtime_root / "state" / "knowledge.db").exists()


class _RecordingConnection:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, list[Any] | tuple[Any, ...] | None]] = []

    def execute(self, statement: str, parameters: list[Any] | tuple[Any, ...] | None = None):
        self.statements.append((statement, parameters))

        class Result:
            def __init__(self, values: list[dict[str, Any]]) -> None:
                self.values = values

            def fetchall(self) -> list[dict[str, Any]]:
                return self.values

        table = statement.removeprefix("SELECT * FROM ").split()[0]
        return Result(self.rows.get(table, []))


class _FakePostgresRepository:
    backend = "postgresql"
    initialized = 0
    recorded_rows: dict[str, list[dict[str, Any]]] | None = None
    export_rows: dict[str, list[dict[str, Any]]] | None = None
    nonempty = False

    def __init__(self, database_url: str, migrations_directory: Path) -> None:
        self.database_url = database_url
        self.migrations_directory = migrations_directory

    def initialize(self) -> None:
        type(self).initialized += 1

    def has_user_records(self) -> bool:
        return type(self).nonempty

    def prepare_backup_restore(self) -> None:
        return None

    def insert_backup_rows(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        type(self).recorded_rows = rows

    def insert_export_rows(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        type(self).export_rows = rows


@pytest.fixture(autouse=True)
def reset_fake_postgres_repository() -> None:
    _FakePostgresRepository.initialized = 0
    _FakePostgresRepository.recorded_rows = None
    _FakePostgresRepository.export_rows = None
    _FakePostgresRepository.nonempty = False


def _complete_backup_records() -> dict[str, list[dict[str, Any]]]:
    return {
        table: [{column: None for column in columns}]
        for table, columns in BACKUP_TABLE_COLUMNS.items()
    }


def _logical_backup_archive(path: Path, records: dict[str, list[dict[str, Any]]]) -> None:
    payload = json.dumps({"schema_version": 1, "records": records}, separators=(",", ":")).encode()
    manifest = {
        "schema_version": 1,
        "archive_type": "backup",
        "entries": [{"path": "records.json", "sha256": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload)}],
    }
    with ZipFile(path, "w") as archive:
        archive.writestr("records.json", payload)
        archive.writestr("manifest.json", json.dumps(manifest))


def test_postgres_logical_backup_archive_includes_catalog(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(runtime_root, acquire_lock=False)
    records = _complete_backup_records()
    records["backups"][0].update({
        "id": "backup-id", "archive_name": "backup.zip", "manifest_sha256": "digest", "state": "succeeded", "created_at": "2026-07-29T00:00:00+00:00",
    })

    class LogicalBackupSource:
        backend = "postgresql"

        def rows_for_backup(self) -> dict[str, list[dict[str, Any]]]:
            return records

    monkeypatch.setattr(app.state.services.transfers, "repository", LogicalBackupSource())
    archive_path = runtime_root / "logical-backup.zip"
    app.state.services.transfers._build_archive(archive_path, "backup")

    with ZipFile(archive_path) as archive:
        payload = json.loads(archive.read("records.json"))
    assert payload["records"]["backups"] == records["backups"]


def test_postgres_logical_backup_inventory_restores_catalog_and_export_excludes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    source_rows = {table: [] for table in BACKUP_TABLES}
    source_rows["backups"] = [{"id": "backup-id", "archive_name": "backup.zip", "manifest_sha256": "digest", "state": "succeeded", "created_at": "2026-07-29T00:00:00+00:00"}]
    connection = _RecordingConnection(source_rows)
    repository = PostgresRepository("postgresql://example", MIGRATIONS)

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(repository, "connection", fake_connection)
    logical_backup = repository.rows_for_backup()
    repository.insert_backup_rows(logical_backup)

    assert logical_backup["backups"] == source_rows["backups"]
    assert set(repository.rows_for_export()) == set(BACKUP_TABLES) - {"settings", "jobs", "job_attempts", "audit_events", "backups"}
    assert any(statement.startswith("INSERT INTO backups(") for statement, _ in connection.statements)


def test_postgres_logical_restore_preserves_catalog_and_rejects_invalid_target_state(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    archive_path = runtime_root / "logical-backup.zip"
    records = _complete_backup_records()
    records["backups"][0].update({
        "id": "backup-id", "archive_name": "backup.zip", "manifest_sha256": "digest", "state": "succeeded", "created_at": "2026-07-29T00:00:00+00:00",
    })
    _logical_backup_archive(archive_path, records)
    monkeypatch.setattr("app.adapters.postgres.PostgresRepository", _FakePostgresRepository)

    result = services.transfers._restore_archive(archive_path, str(runtime_root / "restored"), "postgresql://target")

    assert result["archive_type"] == "backup"
    assert _FakePostgresRepository.recorded_rows == records
    assert _FakePostgresRepository.recorded_rows["backups"][0]["id"] == "backup-id"
    assert not (runtime_root / "restored" / "state" / "knowledge.db").exists()

    _FakePostgresRepository.nonempty = True
    with pytest.raises(ValueError, match="PostgreSQL 还原目标必须为空"):
        services.transfers._restore_archive(archive_path, str(runtime_root / "nonempty"), "postgresql://target")


def test_postgres_logical_restore_rejects_missing_or_malformed_catalog_before_target_initialization(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    monkeypatch.setattr("app.adapters.postgres.PostgresRepository", _FakePostgresRepository)

    missing_catalog = _complete_backup_records()
    missing_catalog.pop("backups")
    missing_path = runtime_root / "missing-catalog.zip"
    _logical_backup_archive(missing_path, missing_catalog)
    with pytest.raises(ValueError, match="逻辑记录不完整"):
        services.transfers._restore_archive(missing_path, str(runtime_root / "missing-target"), "postgresql://target")

    malformed = _complete_backup_records()
    malformed["backups"] = [{"id": "only-id"}]
    malformed_path = runtime_root / "malformed-catalog.zip"
    _logical_backup_archive(malformed_path, malformed)
    with pytest.raises(ValueError, match="逻辑记录无效"):
        services.transfers._restore_archive(malformed_path, str(runtime_root / "malformed-target"), "postgresql://target")

    assert _FakePostgresRepository.initialized == 0
    assert not (runtime_root / "missing-target").exists()
    assert not (runtime_root / "malformed-target").exists()


@pytest.mark.skipif(
    not POSTGRES_TEST_URL or not POSTGRES_RESTORE_TEST_URL,
    reason="POSTGRES_TEST_URL and empty POSTGRES_RESTORE_TEST_URL are required for PostgreSQL backup restore integration",
)
def test_postgres_logical_backup_restore_preserves_catalog_in_empty_target(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requires separate disposable source and empty target PostgreSQL databases."""
    monkeypatch.setenv("YUANZHIKU_DATABASE_URL", POSTGRES_TEST_URL or "")
    services = ApplicationServices(data_paths(runtime_root))
    prior = services.repository.create_backup_record(f"prior-{uuid.uuid4().hex}.zip", "prior-digest")
    backup = services.transfers.create_backup()

    with ZipFile(backup["archive_path"]) as archive:
        payload = json.loads(archive.read("records.json"))
    assert payload["records"]["backups"]
    assert prior["id"] in {row["id"] for row in payload["records"]["backups"]}

    restored = services.transfers.restore_backup(
        backup["id"], str(runtime_root / "restored"), POSTGRES_RESTORE_TEST_URL
    )
    target = PostgresRepository(POSTGRES_RESTORE_TEST_URL or "", MIGRATIONS)
    target.initialize()
    restored_catalog = {row["id"]: row for row in target.list_backups()}

    assert restored["archive_type"] == "backup"
    assert restored_catalog[prior["id"]]["archive_name"] == prior["archive_name"]
    assert not (runtime_root / "restored" / "state" / "knowledge.db").exists()


@pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not configured for PostgreSQL integration")
def test_postgres_repository_normal_api_worker_workflow(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requires an isolated disposable PostgreSQL database URL supplied by the runner."""
    monkeypatch.setenv("YUANZHIKU_DATABASE_URL", POSTGRES_TEST_URL or "")
    services = ApplicationServices(data_paths(runtime_root))

    imported = services.imports.paste(
        PasteImportRequest(
            title="PostgreSQL integration source",
            text="# PostgreSQL workflow\n\nEvidence content for the worker.",
            rights="owned",
            categories=["technical"],
            tags=["postgresql", "integration"],
        )
    )
    job = services.jobs.run_once()
    assert job is not None and job["state"] == "succeeded"

    source_id = imported["source"]["id"]
    version_id = imported["content_version"]["id"]
    source = services.repository.get_source(source_id)
    assert source is not None and source["processing_state"] == "succeeded"
    representation = services.repository.representations_for_version(version_id)[0]
    evidence = services.repository.evidence_for_representation(representation["id"])[0]
    citation = services.repository.create_citation(evidence["id"])
    assert services.repository.citation_details(citation["id"]) is not None

    knowledge = services.repository.create_knowledge("fact", "PostgreSQL adapter workflow", [evidence["id"]])
    assert services.repository.publish_knowledge(knowledge["id"])["status"] == "published"
    assert services.search.search("PostgreSQL")

    topic = services.repository.create_topic("PostgreSQL topic", [source_id])
    assert services.repository.add_source_to_topic(topic["id"], source_id)
    external = services.repository.create_external_card(
        "general", "https://example.test/postgresql", "PostgreSQL card", None, None, ["postgresql"]
    )
    assert external["id"]

    backup = services.transfers.create_backup()
    assert services.transfers.verify_archive(Path(backup["archive_path"]))["valid"] is True
    exported = services.transfers.create_export(True)
    assert services.transfers.verify_archive(Path(exported["archive_path"]))["valid"] is True
    reimported = services.transfers.reimport(exported["archive_path"])
    assert reimported["report"]["inserted_records"] == 0

    assert services.lifecycle.delete(source_id)["deleted_at"]
    assert services.lifecycle.restore(source_id)["deleted_at"] is None
    services.lifecycle.delete(source_id)
    purged = services.lifecycle.purge(source_id)
    assert purged["purged"] is True
