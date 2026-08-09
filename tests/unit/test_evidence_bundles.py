from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.adapters.sqlite import SCHEMA, SqliteRepository
from app.core.config import data_paths
from app.domain.models import ManualRepresentationCreate, PasteImportRequest
from app.main import create_app
from app.services.documents import DocumentService
from app.services.transfers import ARCHIVE_SCHEMA_VERSION


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "evidence-bundles"


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _imported_service(runtime_root: Path):
    services = create_app(runtime_root, acquire_lock=False).state.services
    imported = services.imports.paste(PasteImportRequest(
        title="evidence bundle", text="# Heading\n\nEvidence bundle content.", rights="owned"
    ))
    return services, imported


def _bundle(documents: DocumentService, text: str):
    chunks, evidence = documents.parsed_bundle(text, "config", "md")
    return chunks, evidence


def test_bundle_write_rolls_back_when_evidence_insert_fails(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    services, imported = _imported_service(runtime_root)
    documents = services.documents
    chunks, evidence = _bundle(documents, "Atomic evidence bundle")
    original = SqliteRepository._locator_json_and_hash

    def broken_locator_hash(locator: dict):
        if locator == evidence[0]["locator"]:
            raise RuntimeError("injected evidence failure")
        return original(locator)

    monkeypatch.setattr(SqliteRepository, "_locator_json_and_hash", staticmethod(broken_locator_hash))

    with pytest.raises(RuntimeError, match="injected evidence failure"):
        services.repository.persist_representation_bundle(
            version_id=imported["content_version"]["id"],
            artifact_sha256=imported["artifact"]["sha256"],
            kind="extraction",
            parser_name="unit-parser",
            config_hash="config",
            text="Atomic evidence bundle",
            parent_id=None,
            chunks=chunks,
            evidence=evidence,
        )

    version_id = imported["content_version"]["id"]
    assert services.repository.representations_for_version(version_id) == []
    with services.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM search_chunks").fetchone()["count"] == 0
        assert connection.execute("SELECT COUNT(*) AS count FROM evidence").fetchone()["count"] == 0
        assert connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()["count"] == 0


def test_extraction_bundle_is_idempotent_and_generated_citation_is_unique(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    version_id = imported["content_version"]["id"]
    artifact_sha256 = imported["artifact"]["sha256"]

    first = services.documents.record_parsed(
        version_id, artifact_sha256, "Repeatable extraction", "unit-parser", "config", "txt"
    )
    second = services.documents.record_parsed(
        version_id, artifact_sha256, "Repeatable extraction", "unit-parser", "config", "txt"
    )

    assert second["representation"]["id"] == first["representation"]["id"]
    assert second["evidence"]["id"] == first["evidence"]["id"]
    assert second["citation"]["id"] == first["citation"]["id"]
    assert services.repository.create_citation(first["evidence"]["id"])["id"] == first["citation"]["id"]
    with services.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM representations").fetchone()["count"] == 1
        assert connection.execute("SELECT COUNT(*) AS count FROM search_chunks").fetchone()["count"] == 1
        assert connection.execute("SELECT COUNT(*) AS count FROM evidence").fetchone()["count"] == 1
        assert connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()["count"] == 1


def test_existing_extraction_with_missing_citation_is_completed_without_duplicate_representation(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    version_id = imported["content_version"]["id"]
    artifact_sha256 = imported["artifact"]["sha256"]
    created = services.documents.record_parsed(
        version_id, artifact_sha256, "Recover partial bundle", "unit-parser", "config", "txt"
    )
    with services.repository.connection() as connection:
        connection.execute("DELETE FROM citations WHERE evidence_id=?", (created["evidence"]["id"],))

    recovered = services.documents.record_parsed(
        version_id, artifact_sha256, "Recover partial bundle", "unit-parser", "config", "txt"
    )

    assert recovered["representation"]["id"] == created["representation"]["id"]
    assert recovered["evidence"]["id"] == created["evidence"]["id"]
    assert recovered["citation"]["evidence_id"] == created["evidence"]["id"]
    with services.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM representations").fetchone()["count"] == 1
        assert connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()["count"] == 1


def test_manual_representation_writes_a_full_bundle(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    result = services.documents.create_manual_representation(
        imported["content_version"]["id"], ManualRepresentationCreate(text="Manual full bundle")
    )

    representation = result["representation"]
    assert services.repository.representation_bundle_complete(
        representation["id"],
        version_id=imported["content_version"]["id"],
        artifact_sha256=imported["artifact"]["sha256"],
        kind="manual",
        parser_name="human-revised",
        config_hash=representation["config_hash"],
        text="Manual full bundle",
        chunks=services.documents.search_chunk_pairs("Manual full bundle"),
        evidence=DocumentService._evidence_payloads("Manual full bundle", representation["config_hash"], "txt"),
    )


def test_archive_validation_rejects_incomplete_derived_evidence_chain(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Archive evidence bundle", "unit-parser", "config", "txt",
    )
    services.repository.set_version_completeness(imported["content_version"]["id"], "complete")
    rows = services.repository.rows_for_export()
    rows["citations"] = []

    with pytest.raises(ValueError, match="派生证据链无效"):
        services.transfers._export_records({"schema_version": ARCHIVE_SCHEMA_VERSION, "records": rows})


def test_archive_validation_rejects_complete_version_without_extraction_bundle(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    services.repository.set_version_completeness(imported["content_version"]["id"], "complete")

    with pytest.raises(ValueError, match="派生证据链无效"):
        services.transfers._export_records({
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "records": services.repository.rows_for_export(),
        })


def test_backup_rejects_sqlite_snapshot_that_differs_from_validated_records(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Snapshot consistency evidence", "unit-parser", "config", "txt",
    )
    services.repository.set_version_completeness(imported["content_version"]["id"], "complete")
    backup = services.transfers.create_backup()

    with ZipFile(backup["archive_path"]) as archive:
        members = {entry.filename: archive.read(entry.filename) for entry in archive.infolist()}
    snapshot = runtime_root / "tampered-knowledge.db"
    snapshot.write_bytes(members["state/knowledge.db"])
    with sqlite3.connect(snapshot) as connection:
        connection.execute(
            "UPDATE settings SET value=? WHERE key=?", ("301", "job_lease_seconds")
        )
    members["state/knowledge.db"] = snapshot.read_bytes()
    manifest = json.loads(members.pop("manifest.json"))
    for entry in manifest["entries"]:
        if entry["path"] == "state/knowledge.db":
            entry["sha256"] = hashlib.sha256(members["state/knowledge.db"]).hexdigest()
            entry["byte_size"] = len(members["state/knowledge.db"])
            break
    tampered = runtime_root / "tampered-snapshot.zip"
    with ZipFile(tampered, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))

    verification = services.transfers.verify_archive(tampered)

    assert verification == {"valid": False, "errors": ["SQLite 状态快照与逻辑记录不一致"]}
    target = runtime_root / "tampered-restore"
    with pytest.raises(ValueError, match="SQLite 状态快照与逻辑记录不一致"):
        services.transfers._restore_archive(tampered, str(target))
    assert not target.exists()


def test_pre_v5_sqlite_migration_enforces_non_null_unique_locator_identity(runtime_root: Path) -> None:
    paths = data_paths(runtime_root)
    paths.create()
    legacy_schema = SCHEMA.replace("locator_hash TEXT NOT NULL, ", "locator_hash TEXT, ")
    assert legacy_schema != SCHEMA
    with sqlite3.connect(paths.database) as connection:
        connection.executescript(legacy_schema)
        connection.executemany(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            [(version, "2026-07-29T00:00:00+00:00") for version in range(1, 5)],
        )

    services = create_app(runtime_root, acquire_lock=False).state.services
    with sqlite3.connect(paths.database) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(evidence)")}
    assert columns["locator_hash"][3] == 1

    imported = services.imports.paste(PasteImportRequest(
        title="legacy locator", text="Legacy locator migration", rights="owned"
    ))
    created = services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Legacy locator migration", "unit-parser", "config", "txt",
    )["evidence"]
    values = (
        uuid.uuid4().hex,
        created["content_version_id"],
        created["artifact_sha256"],
        created["representation_id"],
        created["parser_config_hash"],
        created["locator_json"],
        created["excerpt"],
        created["excerpt_hash"],
        created["is_validated"],
        created["created_at"],
    )
    statement = (
        "INSERT INTO evidence(id,content_version_id,artifact_sha256,representation_id,parser_config_hash,"
        "locator_json,locator_hash,excerpt,excerpt_hash,is_validated,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        with services.repository.connection() as connection:
            connection.execute(statement, values[:6] + (None,) + values[6:])
    with pytest.raises(sqlite3.IntegrityError):
        with services.repository.connection() as connection:
            connection.execute(statement, values[:6] + (created["locator_hash"],) + values[6:])


def test_complete_version_requires_validated_evidence_chain(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    output = services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Validated extraction evidence", "unit-parser", "config", "txt",
    )
    services.repository.set_version_completeness(imported["content_version"]["id"], "complete")
    with services.repository.connection() as connection:
        connection.execute("UPDATE evidence SET is_validated=0 WHERE id=?", (output["evidence"]["id"],))

    with pytest.raises(ValueError, match="派生证据链无效"):
        services.transfers._export_records({
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "records": services.repository.rows_for_export(),
        })


def test_incomplete_version_with_partial_extraction_remains_exportable(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    services.repository.create_representation(
        imported["content_version"]["id"], "extraction", "partial-parser", "partial-config", "Partial extraction"
    )

    records = services.transfers._export_records({
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "records": services.repository.rows_for_export(),
    })

    assert len(records["representations"]) == 1
    assert records["content_versions"][0]["completeness"] == "pending"


def test_incomplete_version_with_partial_evidence_and_no_citation_remains_exportable(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    representation = services.repository.create_representation(
        imported["content_version"]["id"], "extraction", "partial-parser", "partial-config", "Partial evidence"
    )
    services.repository.create_evidence(
        version_id=imported["content_version"]["id"],
        artifact_sha256=imported["artifact"]["sha256"],
        representation_id=representation["id"],
        parser_config_hash=representation["config_hash"],
        locator={"type": "text_range", "char_range": [0, 8]},
        excerpt="Partial",
        excerpt_hash=hashlib.sha256(b"Partial").hexdigest(),
        is_validated=False,
    )

    records = services.transfers._export_records({
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "records": services.repository.rows_for_export(),
    })

    assert len(records["evidence"]) == 1
    assert records["citations"] == []


def test_backup_sanitizes_legacy_url_userinfo_only_in_copied_snapshot(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Legacy URL backup evidence", "unit-parser", "config", "txt",
    )
    services.repository.set_version_completeness(imported["content_version"]["id"], "complete")
    card = services.repository.create_external_card(
        "general", "https://alice:old-secret@example.test/path", "legacy", None, None, []
    )

    backup = services.transfers.create_backup()

    with services.repository.connection() as connection:
        original = connection.execute("SELECT url FROM external_cards WHERE id=?", (card["id"],)).fetchone()
    assert original["url"] == "https://alice:old-secret@example.test/path"
    with ZipFile(backup["archive_path"]) as archive:
        snapshot = runtime_root / "sanitized-knowledge.db"
        snapshot.write_bytes(archive.read("state/knowledge.db"))
    with sqlite3.connect(snapshot) as connection:
        archived = connection.execute("SELECT url FROM external_cards WHERE id=?", (card["id"],)).fetchone()
    assert archived[0] == "https://example.test/path"
    assert services.transfers.verify_archive(Path(backup["archive_path"]))["valid"] is True


def test_backup_sanitizes_legacy_ipv6_url_without_corrupting_the_authority(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Legacy IPv6 backup evidence", "unit-parser", "config", "txt",
    )
    services.repository.set_version_completeness(imported["content_version"]["id"], "complete")
    raw_url = "https://alice:old-secret@[2001:db8::1]:8443/path"
    with services.repository.connection() as connection:
        connection.execute(
            "INSERT INTO external_cards(id,card_type,url,title,author,notes,tags_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("legacy-ipv6", "general", raw_url, "legacy", None, None, "[]", "2026-07-30T00:00:00+00:00"),
        )

    backup = services.transfers.create_backup()
    target = runtime_root / "ipv6-restored"
    services.transfers.restore_backup(backup["id"], str(target))

    with services.repository.connection() as connection:
        original = connection.execute("SELECT url FROM external_cards WHERE id='legacy-ipv6'").fetchone()
    restored_repository = SqliteRepository(target / "state" / "knowledge.db")
    restored_repository.initialize()
    restored = next(
        card for card in restored_repository.rows_for_backup()["external_cards"] if card["id"] == "legacy-ipv6"
    )
    assert original["url"] == raw_url
    assert restored["url"] == "https://[2001:db8::1]:8443/path"


def test_backup_sanitizes_legacy_url_with_non_numeric_port(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Legacy port backup evidence", "unit-parser", "config", "txt",
    )
    services.repository.set_version_completeness(imported["content_version"]["id"], "complete")
    with services.repository.connection() as connection:
        connection.execute(
            "INSERT INTO external_cards(id,card_type,url,title,author,notes,tags_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("legacy-port", "general", "https://alice:old-secret@example.test:not-a-port/path", "legacy", None, None, "[]", "2026-07-30T00:00:00+00:00"),
        )

    backup = services.transfers.create_backup()

    assert services.transfers.verify_archive(Path(backup["archive_path"]))["valid"] is True
    services, imported = _imported_service(runtime_root)
    services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "URL snapshot evidence", "unit-parser", "config", "txt",
    )
    services.repository.set_version_completeness(imported["content_version"]["id"], "complete")
    backup = services.transfers.create_backup()

    with ZipFile(backup["archive_path"]) as archive:
        members = {entry.filename: archive.read(entry.filename) for entry in archive.infolist()}
    snapshot = runtime_root / "userinfo-knowledge.db"
    snapshot.write_bytes(members["state/knowledge.db"])
    with sqlite3.connect(snapshot) as connection:
        connection.execute(
            "INSERT INTO external_cards(id,card_type,url,title,author,notes,tags_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, "general", "https://mallory:new-secret@example.test/path", "legacy", None, None, "[]", "2026-07-29T00:00:00+00:00"),
        )
    members["state/knowledge.db"] = snapshot.read_bytes()
    manifest = json.loads(members.pop("manifest.json"))
    for entry in manifest["entries"]:
        if entry["path"] == "state/knowledge.db":
            entry["sha256"] = hashlib.sha256(members["state/knowledge.db"]).hexdigest()
            entry["byte_size"] = len(members["state/knowledge.db"])
            break
    tampered = runtime_root / "userinfo-snapshot.zip"
    with ZipFile(tampered, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))

    assert services.transfers.verify_archive(tampered) == {
        "valid": False,
        "errors": ["SQLite 状态快照无效"],
    }


def test_pre_v5_sqlite_migration_preserves_duplicate_evidence_and_citations(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    output = services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Duplicate preservation", "unit-parser", "config", "txt",
    )
    evidence = output["evidence"]
    citation = output["citation"]
    database = services.paths.database
    services = None
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP INDEX idx_evidence_representation_locator_excerpt")
        connection.execute("ALTER TABLE citations RENAME TO citations_current")
        connection.execute(
            "CREATE TABLE citations ("
            "id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id), created_at TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "INSERT INTO citations(id,evidence_id,created_at) "
            "SELECT id,evidence_id,created_at FROM citations_current"
        )
        connection.execute("DROP TABLE citations_current")
        connection.execute("ALTER TABLE evidence RENAME TO evidence_current")
        connection.execute(
            "CREATE TABLE evidence ("
            "id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), "
            "artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256), "
            "representation_id TEXT NOT NULL REFERENCES representations(id), parser_config_hash TEXT NOT NULL, "
            "locator_json TEXT NOT NULL, locator_hash TEXT, excerpt TEXT NOT NULL, excerpt_hash TEXT NOT NULL, "
            "is_validated INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "INSERT INTO evidence(id,content_version_id,artifact_sha256,representation_id,parser_config_hash,"
            "locator_json,locator_hash,excerpt,excerpt_hash,is_validated,created_at) "
            "SELECT id,content_version_id,artifact_sha256,representation_id,parser_config_hash,locator_json,"
            "locator_hash,excerpt,excerpt_hash,is_validated,created_at FROM evidence_current"
        )
        connection.execute("DROP TABLE evidence_current")
        connection.execute("DELETE FROM schema_migrations WHERE version=5")
        duplicate_evidence = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO evidence(id,content_version_id,artifact_sha256,representation_id,parser_config_hash,"
            "locator_json,locator_hash,excerpt,excerpt_hash,is_validated,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                duplicate_evidence,
                evidence["content_version_id"],
                evidence["artifact_sha256"],
                evidence["representation_id"],
                evidence["parser_config_hash"],
                evidence["locator_json"],
                evidence["locator_hash"],
                evidence["excerpt"],
                evidence["excerpt_hash"],
                evidence["is_validated"],
                evidence["created_at"],
            ),
        )
        connection.execute("INSERT INTO knowledge(id,kind,statement,status,created_at,published_at) VALUES(?,?,?,?,?,NULL)", (
            "knowledge-duplicate-evidence", "fact", "preserve duplicate", "draft", "2026-07-29T00:00:00+00:00",
        ))
        connection.execute("INSERT INTO knowledge_evidence(knowledge_id,evidence_id) VALUES(?,?)", (
            "knowledge-duplicate-evidence", duplicate_evidence,
        ))
        connection.execute("INSERT INTO citations(id,evidence_id,created_at) VALUES(?,?,?)", (
            "citation-duplicate", citation["evidence_id"], "2026-07-29T00:00:00+00:00",
        ))

    upgraded = create_app(runtime_root, acquire_lock=False).state.services
    with upgraded.repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM evidence").fetchone()["count"] == 3
        assert connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()["count"] == 3
        retained = connection.execute(
            "SELECT evidence_id FROM knowledge_evidence WHERE knowledge_id=?", ("knowledge-duplicate-evidence",)
        ).fetchone()
        assert retained is not None and retained["evidence_id"] == duplicate_evidence
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM representations WHERE kind='extraction_legacy'"
        ).fetchone()["count"] >= 2


def test_legacy_evidence_archive_rows_gain_canonical_locator_hash(runtime_root: Path) -> None:
    services, imported = _imported_service(runtime_root)
    services.documents.record_parsed(
        imported["content_version"]["id"], imported["artifact"]["sha256"],
        "Legacy evidence bundle", "unit-parser", "config", "txt",
    )
    rows = services.repository.rows_for_export()
    evidence = rows["evidence"][0]
    locator = json.loads(evidence["locator_json"])
    legacy_columns = [column for column in evidence if column != "locator_hash"]
    rows["evidence"] = [{column: evidence[column] for column in legacy_columns}]

    normalized = services.transfers._export_records({"schema_version": 3, "records": rows})

    expected_locator_json = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert normalized["evidence"][0]["locator_json"] == expected_locator_json
    assert normalized["evidence"][0]["locator_hash"] == hashlib.sha256(expected_locator_json.encode("utf-8")).hexdigest()
