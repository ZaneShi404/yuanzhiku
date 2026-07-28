from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from app.adapters.postgres import PostgresRepository
from app.core.config import data_paths
from app.domain.models import PasteImportRequest
from app.main import ApplicationServices


RUN_ROOT = Path(__file__).resolve().parents[1] / "runtime" / "postgres-repair-20260728T181807Z"
MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations" / "postgresql"
POSTGRES_TEST_URL = os.environ.get("POSTGRES_TEST_URL")


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
