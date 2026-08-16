"""SQLite adapter. SQL is kept at this boundary, not in domain services."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from app.domain.models import split_legacy_categories


def now() -> str:
    return datetime.now(UTC).isoformat()


def identifier() -> str:
    return uuid.uuid4().hex


def redact_url_userinfo(value: str) -> str:
    """Return a URL without userinfo for legacy records and portable exports."""
    parsed = urlsplit(value)
    if "@" not in parsed.netloc:
        return value
    # Reuse the literal authority after userinfo. Reconstructing it from
    # hostname/port loses IPv6 brackets and may reject a legacy invalid port.
    return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[1], parsed.path, parsed.query, parsed.fragment))


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
    sha256 TEXT PRIMARY KEY, byte_size INTEGER NOT NULL, stored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, source_type TEXT NOT NULL, title TEXT NOT NULL, author TEXT,
    language TEXT NOT NULL, notes TEXT, source_date TEXT, rights TEXT, domains_json TEXT NOT NULL,
    genres_json TEXT NOT NULL, tags_json TEXT NOT NULL, processing_state TEXT NOT NULL, imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS source_metadata_revisions (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), ordinal INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_metadata_revisions_source ON source_metadata_revisions(source_id, ordinal DESC);
CREATE TABLE IF NOT EXISTS content_versions (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
    ordinal INTEGER NOT NULL, original_name TEXT NOT NULL, media_type TEXT, completeness TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_versions_source ON content_versions(source_id);
CREATE TABLE IF NOT EXISTS video_analyses (
    id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), analyzer_name TEXT NOT NULL,
    config_hash TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(content_version_id, analyzer_name, config_hash)
);
CREATE INDEX IF NOT EXISTS idx_video_analyses_version ON video_analyses(content_version_id, created_at DESC);
CREATE TABLE IF NOT EXISTS video_frames (
    id TEXT PRIMARY KEY, video_analysis_id TEXT NOT NULL REFERENCES video_analyses(id),
    artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256), ordinal INTEGER NOT NULL, time_ms INTEGER NOT NULL,
    width INTEGER, height INTEGER, reason TEXT NOT NULL DEFAULT 'even', created_at TEXT NOT NULL, UNIQUE(video_analysis_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_video_frames_analysis ON video_frames(video_analysis_id, ordinal);
CREATE TABLE IF NOT EXISTS source_relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), related_source_id TEXT NOT NULL REFERENCES sources(id),
    relation_type TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(source_id, related_source_id, relation_type)
);
CREATE TABLE IF NOT EXISTS representations (
    id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), kind TEXT NOT NULL,
    parser_name TEXT NOT NULL, config_hash TEXT NOT NULL, parent_representation_id TEXT REFERENCES representations(id),
    text_content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_representations_version ON representations(content_version_id);
CREATE TABLE IF NOT EXISTS search_chunks (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), content_version_id TEXT NOT NULL REFERENCES content_versions(id),
    representation_id TEXT NOT NULL REFERENCES representations(id), ordinal INTEGER NOT NULL, text_content TEXT NOT NULL,
    text_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(representation_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_search_chunks_version ON search_chunks(content_version_id);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id),
    artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256), representation_id TEXT NOT NULL REFERENCES representations(id),
    parser_config_hash TEXT NOT NULL, locator_json TEXT NOT NULL, locator_hash TEXT NOT NULL, excerpt TEXT NOT NULL, excerpt_hash TEXT NOT NULL,
    is_validated INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
    UNIQUE(representation_id, locator_hash, excerpt_hash)
);
CREATE INDEX IF NOT EXISTS idx_evidence_version ON evidence(content_version_id);
CREATE TABLE IF NOT EXISTS citations (
    id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id), created_at TEXT NOT NULL,
    UNIQUE(evidence_id)
);
CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, statement TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, published_at TEXT
);
CREATE TABLE IF NOT EXISTS knowledge_evidence (
    knowledge_id TEXT NOT NULL REFERENCES knowledge(id), evidence_id TEXT NOT NULL REFERENCES evidence(id),
    PRIMARY KEY(knowledge_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT REFERENCES sources(id), content_version_id TEXT REFERENCES content_versions(id),
    artifact_sha256 TEXT REFERENCES artifacts(sha256), config_hash TEXT, payload_json TEXT NOT NULL, priority INTEGER NOT NULL,
    state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, message TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT,
    lease_token TEXT, lease_expires_at TEXT, started_at TEXT, completed_at TEXT, cancel_requested_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(state, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_jobs_running_lease ON jobs(state, lease_expires_at);
CREATE TABLE IF NOT EXISTS job_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), attempt_number INTEGER NOT NULL,
    lease_token TEXT, state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, outcome TEXT, UNIQUE(job_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY, event_type TEXT NOT NULL, entity_id TEXT, result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_events_type_created_at ON audit_events(event_type, created_at);
CREATE TABLE IF NOT EXISTS external_cards (
    id TEXT PRIMARY KEY, card_type TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, author TEXT,
    notes TEXT, tags_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(card_type, url)
);
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_sources (
    topic_id TEXT NOT NULL REFERENCES topics(id), source_id TEXT NOT NULL REFERENCES sources(id),
    PRIMARY KEY(topic_id, source_id)
);
CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY, archive_name TEXT NOT NULL UNIQUE, manifest_sha256 TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

EXPORT_TABLES = (
    "artifacts", "sources", "source_metadata_revisions", "content_versions", "video_analyses", "video_frames", "source_relations", "representations",
    "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence", "external_cards", "topics",
    "topic_sources", "video_download_provenance",
)

BACKUP_TABLES = (
    "settings", "artifacts", "sources", "source_metadata_revisions", "content_versions", "video_analyses", "video_frames", "source_relations",
    "representations", "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence", "jobs",
    "job_attempts", "audit_events", "external_cards", "topics", "topic_sources", "backups", "video_download_provenance",
)

BACKUP_TABLE_COLUMNS = {
    "settings": ("key", "value", "updated_at"),
    "artifacts": ("sha256", "byte_size", "stored_at"),
    "sources": ("id", "source_type", "title", "author", "language", "notes", "source_date", "rights", "domains_json", "genres_json", "tags_json", "processing_state", "imported_at", "updated_at", "deleted_at"),
    "source_metadata_revisions": ("id", "source_id", "ordinal", "snapshot_json", "created_at"),
    "content_versions": ("id", "source_id", "artifact_sha256", "ordinal", "original_name", "media_type", "completeness", "created_at"),
    "video_analyses": ("id", "content_version_id", "analyzer_name", "config_hash", "metadata_json", "created_at"),
    "video_frames": ("id", "video_analysis_id", "artifact_sha256", "ordinal", "time_ms", "width", "height", "reason", "created_at"),
    "source_relations": ("id", "source_id", "related_source_id", "relation_type", "created_at"),
    "representations": ("id", "content_version_id", "kind", "parser_name", "config_hash", "parent_representation_id", "text_content", "created_at"),
    "search_chunks": ("id", "source_id", "content_version_id", "representation_id", "ordinal", "text_content", "text_hash", "created_at"),
    "evidence": ("id", "content_version_id", "artifact_sha256", "representation_id", "parser_config_hash", "locator_json", "locator_hash", "excerpt", "excerpt_hash", "is_validated", "created_at"),
    "citations": ("id", "evidence_id", "created_at"),
    "knowledge": ("id", "kind", "statement", "status", "created_at", "published_at"),
    "knowledge_evidence": ("knowledge_id", "evidence_id"),
    "jobs": ("id", "kind", "source_id", "content_version_id", "artifact_sha256", "config_hash", "payload_json", "priority", "state", "progress", "message", "attempt_count", "retry_count", "max_attempts", "created_at", "updated_at", "heartbeat_at", "lease_token", "lease_expires_at", "started_at", "completed_at", "cancel_requested_at"),
    "job_attempts": ("id", "job_id", "attempt_number", "lease_token", "state", "started_at", "ended_at", "outcome"),
    "audit_events": ("id", "event_type", "entity_id", "result", "created_at"),
    "external_cards": ("id", "card_type", "url", "title", "author", "notes", "tags_json", "created_at"),
    "topics": ("id", "name", "created_at"),
    "topic_sources": ("topic_id", "source_id"),
    "backups": ("id", "archive_name", "manifest_sha256", "state", "created_at"),
    "video_download_provenance": ("id", "source_id", "platform", "url_sanitized", "yt_dlp_version", "format_profile", "cookie_used", "config_hash", "created_at"),
}


class SqliteRepository:
    backend = "sqlite"

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.database_path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _locator_json_and_hash(locator: dict[str, Any]) -> tuple[str, str]:
        encoded = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _search_chunk_pairs(text: str, chunk_size: int = 1200) -> list[tuple[str, str]]:
        chunks = [text[offset:offset + chunk_size] for offset in range(0, len(text), chunk_size)] or [""]
        return [(chunk, hashlib.sha256(chunk.encode("utf-8")).hexdigest()) for chunk in chunks]

    def _evidence_table_needs_rebuild(self) -> bool:
        """Detect pre-v5 SQLite evidence tables that cannot enforce NOT NULL."""
        with self._lock:
            connection = sqlite3.connect(self.database_path, timeout=30)
            try:
                columns = {row[1]: row for row in connection.execute("PRAGMA table_info(evidence)")}
            finally:
                connection.close()
        locator_hash = columns.get("locator_hash")
        return locator_hash is None or not bool(locator_hash[3])

    def _rebuild_nullable_evidence_table(self) -> None:
        """Rebuild pre-v5 evidence tables so locator identity is a database invariant."""
        with self._lock:
            connection = sqlite3.connect(self.database_path, timeout=30)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_derived_bundle_constraints(connection)
                connection.execute(
                    "CREATE TABLE evidence_replacement ("
                    "id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), "
                    "artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256), "
                    "representation_id TEXT NOT NULL REFERENCES representations(id), parser_config_hash TEXT NOT NULL, "
                    "locator_json TEXT NOT NULL, locator_hash TEXT NOT NULL, excerpt TEXT NOT NULL, "
                    "excerpt_hash TEXT NOT NULL, is_validated INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, "
                    "UNIQUE(representation_id, locator_hash, excerpt_hash)"
                    ")"
                )
                connection.execute(
                    "CREATE TABLE citations_replacement ("
                    "id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence_replacement(id), "
                    "created_at TEXT NOT NULL, UNIQUE(evidence_id)"
                    ")"
                )
                connection.execute(
                    "CREATE TABLE knowledge_evidence_replacement ("
                    "knowledge_id TEXT NOT NULL REFERENCES knowledge(id), "
                    "evidence_id TEXT NOT NULL REFERENCES evidence_replacement(id), "
                    "PRIMARY KEY(knowledge_id, evidence_id)"
                    ")"
                )
                connection.execute(
                    "INSERT INTO evidence_replacement("
                    "id,content_version_id,artifact_sha256,representation_id,parser_config_hash,locator_json,"
                    "locator_hash,excerpt,excerpt_hash,is_validated,created_at"
                    ") SELECT id,content_version_id,artifact_sha256,representation_id,parser_config_hash,locator_json,"
                    "locator_hash,excerpt,excerpt_hash,is_validated,created_at FROM evidence"
                )
                connection.execute(
                    "INSERT INTO citations_replacement(id,evidence_id,created_at) "
                    "SELECT id,evidence_id,created_at FROM citations"
                )
                connection.execute(
                    "INSERT INTO knowledge_evidence_replacement(knowledge_id,evidence_id) "
                    "SELECT knowledge_id,evidence_id FROM knowledge_evidence"
                )
                connection.execute("DROP TABLE knowledge_evidence")
                connection.execute("DROP TABLE citations")
                connection.execute("DROP TABLE evidence")
                connection.execute("ALTER TABLE evidence_replacement RENAME TO evidence")
                connection.execute("ALTER TABLE citations_replacement RENAME TO citations")
                connection.execute("ALTER TABLE knowledge_evidence_replacement RENAME TO knowledge_evidence")
                connection.execute("CREATE INDEX idx_evidence_version ON evidence(content_version_id)")
                connection.execute(
                    "CREATE UNIQUE INDEX idx_evidence_representation_locator_excerpt "
                    "ON evidence(representation_id, locator_hash, excerpt_hash)"
                )
                connection.execute("CREATE UNIQUE INDEX idx_citations_evidence ON citations(evidence_id)")
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("历史 evidence 外键无效")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _clone_legacy_representation(self, connection: sqlite3.Connection, representation_id: str) -> str:
        representation = connection.execute(
            "SELECT * FROM representations WHERE id=?", (representation_id,)
        ).fetchone()
        if representation is None:
            raise ValueError("历史 representation 不存在")
        legacy_id = identifier()
        connection.execute(
            "INSERT INTO representations(id,content_version_id,kind,parser_name,config_hash,parent_representation_id,"
            "text_content,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                legacy_id,
                representation["content_version_id"],
                "extraction_legacy",
                representation["parser_name"],
                representation["config_hash"],
                representation["id"],
                representation["text_content"],
                representation["created_at"],
            ),
        )
        chunks = connection.execute(
            "SELECT source_id,content_version_id,ordinal,text_content,text_hash,created_at "
            "FROM search_chunks WHERE representation_id=? ORDER BY ordinal",
            (representation_id,),
        ).fetchall()
        for chunk in chunks:
            connection.execute(
                "INSERT INTO search_chunks(id,source_id,content_version_id,representation_id,ordinal,"
                "text_content,text_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    identifier(),
                    chunk["source_id"],
                    chunk["content_version_id"],
                    legacy_id,
                    chunk["ordinal"],
                    chunk["text_content"],
                    chunk["text_hash"],
                    chunk["created_at"],
                ),
            )
        return legacy_id

    def _preserve_duplicate_evidence_identities(self, connection: sqlite3.Connection) -> None:
        duplicates = connection.execute(
            "SELECT representation_id,locator_hash,excerpt_hash FROM evidence "
            "GROUP BY representation_id,locator_hash,excerpt_hash HAVING COUNT(*)>1"
        ).fetchall()
        for duplicate in duplicates:
            rows = connection.execute(
                "SELECT id FROM evidence WHERE representation_id=? AND locator_hash=? AND excerpt_hash=? "
                "ORDER BY created_at,id",
                (duplicate["representation_id"], duplicate["locator_hash"], duplicate["excerpt_hash"]),
            ).fetchall()
            for row in rows[1:]:
                legacy_id = self._clone_legacy_representation(connection, duplicate["representation_id"])
                connection.execute("UPDATE evidence SET representation_id=? WHERE id=?", (legacy_id, row["id"]))

    def _preserve_duplicate_citations(self, connection: sqlite3.Connection) -> None:
        while True:
            duplicate = connection.execute(
                "SELECT evidence_id FROM citations GROUP BY evidence_id HAVING COUNT(*)>1 LIMIT 1"
            ).fetchone()
            if duplicate is None:
                return
            citations = connection.execute(
                "SELECT id FROM citations WHERE evidence_id=? ORDER BY created_at,id", (duplicate["evidence_id"],)
            ).fetchall()
            evidence = connection.execute("SELECT * FROM evidence WHERE id=?", (duplicate["evidence_id"],)).fetchone()
            if evidence is None:
                raise ValueError("历史 citation evidence 不存在")
            citation = citations[-1]
            legacy_id = self._clone_legacy_representation(connection, evidence["representation_id"])
            cloned_evidence_id = identifier()
            connection.execute(
                "INSERT INTO evidence(id,content_version_id,artifact_sha256,representation_id,parser_config_hash,"
                "locator_json,locator_hash,excerpt,excerpt_hash,is_validated,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cloned_evidence_id,
                    evidence["content_version_id"],
                    evidence["artifact_sha256"],
                    legacy_id,
                    evidence["parser_config_hash"],
                    evidence["locator_json"],
                    evidence["locator_hash"],
                    evidence["excerpt"],
                    evidence["excerpt_hash"],
                    evidence["is_validated"],
                    evidence["created_at"],
                ),
            )
            connection.execute("UPDATE citations SET evidence_id=? WHERE id=?", (cloned_evidence_id, citation["id"]))

    def _ensure_derived_bundle_constraints(self, connection: sqlite3.Connection) -> None:
        evidence_columns = {row["name"] for row in connection.execute("PRAGMA table_info(evidence)")}
        if "locator_hash" not in evidence_columns:
            connection.execute("ALTER TABLE evidence ADD COLUMN locator_hash TEXT")
        rows = connection.execute("SELECT id, locator_json FROM evidence WHERE locator_hash IS NULL OR locator_hash='' ").fetchall()
        for row in rows:
            try:
                locator = json.loads(row["locator_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("历史 evidence locator 无效") from exc
            if not isinstance(locator, dict):
                raise ValueError("历史 evidence locator 无效")
            locator_json, locator_hash = self._locator_json_and_hash(locator)
            connection.execute(
                "UPDATE evidence SET locator_json=?, locator_hash=? WHERE id=?",
                (locator_json, locator_hash, row["id"]),
            )
        self._preserve_duplicate_evidence_identities(connection)
        self._preserve_duplicate_citations(connection)
        duplicate_extractions = connection.execute(
            "SELECT content_version_id,parser_name,config_hash FROM representations WHERE kind='extraction' "
            "GROUP BY content_version_id,parser_name,config_hash HAVING COUNT(*)>1"
        ).fetchall()
        for duplicate in duplicate_extractions:
            rows = connection.execute(
                "SELECT id FROM representations WHERE content_version_id=? AND kind='extraction' "
                "AND parser_name=? AND config_hash=? ORDER BY created_at,id",
                (duplicate["content_version_id"], duplicate["parser_name"], duplicate["config_hash"]),
            ).fetchall()
            for row in rows[1:]:
                connection.execute("UPDATE representations SET kind='extraction_legacy' WHERE id=?", (row["id"],))
        representations = connection.execute(
            "SELECT r.id,r.content_version_id,r.text_content,v.source_id FROM representations r "
            "JOIN content_versions v ON v.id=r.content_version_id"
        ).fetchall()
        for representation in representations:
            expected_chunks = self._search_chunk_pairs(representation["text_content"])
            actual_chunks = connection.execute(
                "SELECT ordinal,text_content,text_hash FROM search_chunks WHERE representation_id=? ORDER BY ordinal",
                (representation["id"],),
            ).fetchall()
            if not actual_chunks:
                for ordinal, (chunk_text, text_hash) in enumerate(expected_chunks):
                    connection.execute(
                        "INSERT INTO search_chunks(id,source_id,content_version_id,representation_id,ordinal,"
                        "text_content,text_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (identifier(), representation["source_id"], representation["content_version_id"],
                         representation["id"], ordinal, chunk_text, text_hash, now()),
                    )
        evidence_rows = connection.execute("SELECT id FROM evidence").fetchall()
        for evidence in evidence_rows:
            connection.execute(
                "INSERT INTO citations(id,evidence_id,created_at) "
                "SELECT ?,?,? WHERE NOT EXISTS (SELECT 1 FROM citations WHERE evidence_id=?)",
                (identifier(), evidence["id"], now(), evidence["id"]),
            )
        self._preserve_duplicate_citations(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_extraction_representation_identity "
            "ON representations(content_version_id, parser_name, config_hash) WHERE kind='extraction'"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_representation_locator_excerpt "
            "ON evidence(representation_id, locator_hash, excerpt_hash)"
        )
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_citations_evidence ON citations(evidence_id)")

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
        if self._evidence_table_needs_rebuild():
            self._rebuild_nullable_evidence_table()
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)", (now(),)
            )
            # Older databases allowed multiple records to point at one archive.
            # Retain the newest record before adding the uniqueness constraint.
            duplicate_names = connection.execute(
                "SELECT archive_name FROM backups GROUP BY archive_name HAVING COUNT(*) > 1"
            ).fetchall()
            for duplicate in duplicate_names:
                rows = connection.execute(
                    "SELECT id FROM backups WHERE archive_name=? ORDER BY created_at DESC, id DESC",
                    (duplicate["archive_name"],),
                ).fetchall()
                for row in rows[1:]:
                    connection.execute("DELETE FROM backups WHERE id=?", (row["id"],))
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_backups_archive_name ON backups(archive_name)")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(2, ?)", (now(),)
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)")}
            if "source_date" not in columns:
                connection.execute("ALTER TABLE sources ADD COLUMN source_date TEXT")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sources_source_date ON sources(source_date)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sources_imported_at ON sources(imported_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_type_created_at ON audit_events(event_type, created_at)")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(3, ?)", (now(),)
            )
            migration_versions = {
                row["version"] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 4 not in migration_versions:
                job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
                for column, definition in (
                    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
                    ("lease_token", "TEXT"),
                    ("lease_expires_at", "TEXT"),
                ):
                    if column not in job_columns:
                        connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
                connection.execute(
                    "UPDATE jobs SET retry_count=MAX(attempt_count - 1, 0) "
                    "WHERE retry_count=0 AND attempt_count>0"
                )
                attempt_columns = {row["name"] for row in connection.execute("PRAGMA table_info(job_attempts)")}
                if "lease_token" not in attempt_columns:
                    connection.execute("ALTER TABLE job_attempts ADD COLUMN lease_token TEXT")
                duplicate_attempts = connection.execute(
                    "SELECT job_id FROM job_attempts GROUP BY job_id,attempt_number HAVING COUNT(*)>1"
                ).fetchall()
                for duplicate in duplicate_attempts:
                    attempts = connection.execute(
                        "SELECT id FROM job_attempts WHERE job_id=? ORDER BY attempt_number,started_at,id",
                        (duplicate["job_id"],),
                    ).fetchall()
                    for ordinal, attempt in enumerate(attempts, start=1):
                        connection.execute(
                            "UPDATE job_attempts SET attempt_number=? WHERE id=?",
                            (ordinal, attempt["id"]),
                        )
                connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_running_lease ON jobs(state, lease_expires_at)")
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_attempts_job_attempt_number "
                    "ON job_attempts(job_id, attempt_number)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(4, ?)", (now(),)
                )
            migration_versions = {
                row["version"] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 5 not in migration_versions:
                self._ensure_derived_bundle_constraints(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(5, ?)", (now(),)
                )
            if 6 not in migration_versions:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS video_analyses ("
                    "id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), "
                    "analyzer_name TEXT NOT NULL, config_hash TEXT NOT NULL, metadata_json TEXT NOT NULL, "
                    "created_at TEXT NOT NULL, UNIQUE(content_version_id, analyzer_name, config_hash))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS video_frames ("
                    "id TEXT PRIMARY KEY, video_analysis_id TEXT NOT NULL REFERENCES video_analyses(id), "
                    "artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256), ordinal INTEGER NOT NULL, "
                    "time_ms INTEGER NOT NULL, width INTEGER, height INTEGER, created_at TEXT NOT NULL, "
                    "UNIQUE(video_analysis_id, ordinal))"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS idx_video_analyses_version ON video_analyses(content_version_id, created_at DESC)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_video_frames_analysis ON video_frames(video_analysis_id, ordinal)")
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(6, ?)", (now(),)
                )
            migration_versions = {
                row["version"] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 7 not in migration_versions:
                # 链接下载出处记录（REQ-047.5）：对老库幂等补表。
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS video_download_provenance ("
                    "id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id) UNIQUE, "
                    "platform TEXT NOT NULL, url_sanitized TEXT NOT NULL, yt_dlp_version TEXT NOT NULL, "
                    "format_profile TEXT NOT NULL, cookie_used INTEGER NOT NULL, config_hash TEXT NOT NULL, "
                    "created_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(7, ?)", (now(),)
                )
            migration_versions = {
                row["version"] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 8 not in migration_versions:
                # 关键帧抽样来源（场景切换/等间隔）：老行回填默认 'even'。
                frame_columns = {row["name"] for row in connection.execute("PRAGMA table_info(video_frames)")}
                if "reason" not in frame_columns:
                    connection.execute("ALTER TABLE video_frames ADD COLUMN reason TEXT NOT NULL DEFAULT 'even'")
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(8, ?)", (now(),)
                )
            migration_versions = {
                row["version"] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 9 not in migration_versions:
                # 分类体系重构：固定分类拆分为 领域（多选）× 体裁（单选）；旧值按映射迁移
                # （多体裁全部保留，≤1 规则不适用于迁移），未知值忽略。
                source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)")}
                if "domains_json" not in source_columns:
                    connection.execute("ALTER TABLE sources ADD COLUMN domains_json TEXT NOT NULL DEFAULT '[]'")
                if "genres_json" not in source_columns:
                    connection.execute("ALTER TABLE sources ADD COLUMN genres_json TEXT NOT NULL DEFAULT '[]'")
                if "categories_json" in source_columns:
                    for source in connection.execute("SELECT id, categories_json FROM sources").fetchall():
                        try:
                            legacy = json.loads(source["categories_json"])
                        except (TypeError, json.JSONDecodeError):
                            legacy = []
                        domains, genres = split_legacy_categories(
                            [item for item in legacy if isinstance(item, str)] if isinstance(legacy, list) else []
                        )
                        connection.execute(
                            "UPDATE sources SET domains_json=?, genres_json=? WHERE id=?",
                            (json.dumps(domains), json.dumps(genres), source["id"]),
                        )
                    connection.execute("ALTER TABLE sources DROP COLUMN categories_json")
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(9, ?)", (now(),)
                )
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
                "ai_auto_pipeline": "on",
                "last_backup_date": "",
                "last_integrity_sample_date": "",
            }
            for key, value in defaults.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?, ?, ?)", (key, value, now())
                )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def get_settings(self) -> dict[str, str]:
        with self.connection() as connection:
            return {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM settings")}

    def update_settings(self, values: dict[str, str | int]) -> dict[str, str]:
        with self.connection() as connection:
            stamp = now()
            for key, value in values.items():
                if value is not None:
                    connection.execute("UPDATE settings SET value=?, updated_at=? WHERE key=?", (str(value), stamp, key))
        return self.get_settings()

    def audit(self, event_type: str, entity_id: str | None, result: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES(?, ?, ?, ?, ?)",
                (identifier(), event_type, entity_id, result, now()),
            )

    def prune_source_permanent_delete_audit_events(self, retention_days: int = 366) -> int:
        if retention_days < 366:
            raise ValueError("永久删除审计保留期不得少于 366 天")
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self.connection() as connection:
            return connection.execute(
                "DELETE FROM audit_events WHERE event_type=? AND created_at < ?",
                ("source_permanent_delete", cutoff),
            ).rowcount

    def create_artifact(self, sha256: str, byte_size: int) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifacts(sha256, byte_size, stored_at) VALUES(?, ?, ?)",
                (sha256, byte_size, now()),
            )

    @staticmethod
    def _metadata_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        fields = ("title", "author", "language", "notes", "source_date", "rights", "domains_json", "genres_json", "tags_json")
        return {field: row[field] for field in fields}

    def _record_metadata_revision(self, connection: sqlite3.Connection, source_id: str, stamp: str) -> None:
        source = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None:
            return
        ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 AS ordinal FROM source_metadata_revisions WHERE source_id=?",
            (source_id,),
        ).fetchone()["ordinal"]
        connection.execute(
            "INSERT INTO source_metadata_revisions(id,source_id,ordinal,snapshot_json,created_at) VALUES(?,?,?,?,?)",
            (identifier(), source_id, ordinal, json.dumps(self._metadata_snapshot(source), ensure_ascii=False), stamp),
        )

    @staticmethod
    def _configured_max_attempts(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT value FROM settings WHERE key='max_retry_attempts'").fetchone()
        try:
            return max(0, min(10, int(row["value"]))) if row else 2
        except (TypeError, ValueError):
            return 2

    def create_source_with_version(
        self,
        *, source_type: str, title: str, author: str | None, language: str, notes: str | None,
        rights: str, domains: list[str], genres: list[str], tags: list[str], artifact_sha256: str, original_name: str,
        media_type: str | None, byte_size: int, source_date: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        source_id = identifier()
        version_id = identifier()
        stamp = now()
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifacts(sha256, byte_size, stored_at) VALUES(?, ?, ?)",
                (artifact_sha256, byte_size, stamp),
            )
            connection.execute(
                """INSERT INTO sources(id,source_type,title,author,language,notes,source_date,rights,domains_json,genres_json,tags_json,processing_state,imported_at,updated_at,deleted_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (source_id, source_type, title, author, language, notes, source_date, rights, json.dumps(domains), json.dumps(genres), json.dumps(tags), "queued", stamp, stamp),
            )
            connection.execute(
                """INSERT INTO content_versions(id,source_id,artifact_sha256,ordinal,original_name,media_type,completeness,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (version_id, source_id, artifact_sha256, 1, original_name, media_type, "pending", stamp),
            )
            self._record_metadata_revision(connection, source_id, stamp)
        return self.get_source(source_id) or {}, self.get_version(version_id) or {}

    def create_ingest(
        self,
        *, source_type: str, title: str, author: str | None, language: str, notes: str | None,
        rights: str, domains: list[str], genres: list[str], tags: list[str], artifact_sha256: str, original_name: str,
        media_type: str | None, byte_size: int, job_payload: dict[str, Any], priority: int,
        audit_event: str, source_date: str | None = None, job_kind: str = "parse",
        download_provenance: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Persist every logical ingest record in one transaction.

        ``download_provenance`` (REQ-047.5) is written in the same transaction
        as the source/content version/artifact and the queued follow-up job;
        ``video_download_provenance.source_id`` is UNIQUE so a source carries at
        most one provenance row. The caller compensates the physical artifact
        only when this transaction fails and it created the content-addressed
        file itself.
        """
        source_id = identifier()
        version_id = identifier()
        job_id = identifier()
        stamp = now()
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifacts(sha256, byte_size, stored_at) VALUES(?, ?, ?)",
                (artifact_sha256, byte_size, stamp),
            )
            connection.execute(
                """INSERT INTO sources(id,source_type,title,author,language,notes,source_date,rights,domains_json,genres_json,tags_json,processing_state,imported_at,updated_at,deleted_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (source_id, source_type, title, author, language, notes, source_date, rights, json.dumps(domains), json.dumps(genres), json.dumps(tags), "queued", stamp, stamp),
            )
            connection.execute(
                """INSERT INTO content_versions(id,source_id,artifact_sha256,ordinal,original_name,media_type,completeness,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (version_id, source_id, artifact_sha256, 1, original_name, media_type, "pending", stamp),
            )
            connection.execute(
                """INSERT INTO jobs(id,kind,source_id,content_version_id,artifact_sha256,config_hash,payload_json,priority,state,progress,message,attempt_count,max_attempts,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,? ,0,NULL,0,?,?,?)""",
                (job_id, job_kind, source_id, version_id, artifact_sha256, None, json.dumps(job_payload), priority, "queued", self._configured_max_attempts(connection), stamp, stamp),
            )
            connection.execute(
                "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES(?, ?, ?, ?, ?)",
                (identifier(), audit_event, source_id, "queued", stamp),
            )
            if download_provenance is not None:
                connection.execute(
                    "INSERT INTO video_download_provenance(id,source_id,platform,url_sanitized,"
                    "yt_dlp_version,format_profile,cookie_used,config_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        identifier(),
                        source_id,
                        download_provenance["platform"],
                        download_provenance["url_sanitized"],
                        download_provenance["yt_dlp_version"],
                        download_provenance["format_profile"],
                        int(download_provenance["cookie_used"]),
                        download_provenance["config_hash"],
                        stamp,
                    ),
                )
            self._record_metadata_revision(connection, source_id, stamp)
            source = self._row(connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()) or {}
            version = self._row(connection.execute("SELECT * FROM content_versions WHERE id=?", (version_id,)).fetchone()) or {}
            job = self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()) or {}
        return source, version, job

    def persist_video_analysis(
        self,
        *,
        version_id: str,
        analyzer_name: str,
        config_hash: str,
        metadata: dict[str, Any],
        frames: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connection() as connection:
            version = connection.execute(
                "SELECT id FROM content_versions WHERE id=?", (version_id,)
            ).fetchone()
            if version is None:
                raise KeyError("内容版本不存在")
            metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            analysis = self._row(connection.execute(
                "SELECT * FROM video_analyses WHERE content_version_id=? AND analyzer_name=? AND config_hash=?",
                (version_id, analyzer_name, config_hash),
            ).fetchone())
            if analysis is None:
                analysis_id = identifier()
                connection.execute(
                    "INSERT INTO video_analyses(id,content_version_id,analyzer_name,config_hash,metadata_json,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (analysis_id, version_id, analyzer_name, config_hash, metadata_json, now()),
                )
                analysis = self._row(connection.execute(
                    "SELECT * FROM video_analyses WHERE id=?", (analysis_id,)
                ).fetchone())
            elif analysis["metadata_json"] != metadata_json:
                raise ValueError("同一视频分析身份的元数据不一致")
            assert analysis is not None
            for frame in frames:
                connection.execute(
                    "INSERT OR IGNORE INTO artifacts(sha256,byte_size,stored_at) VALUES(?,?,?)",
                    (frame["artifact_sha256"], frame["byte_size"], now()),
                )
                existing = self._row(connection.execute(
                    "SELECT * FROM video_frames WHERE video_analysis_id=? AND ordinal=?",
                    (analysis["id"], frame["ordinal"]),
                ).fetchone())
                if existing is None:
                    connection.execute(
                        "INSERT INTO video_frames(id,video_analysis_id,artifact_sha256,ordinal,time_ms,width,height,reason,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            identifier(), analysis["id"], frame["artifact_sha256"], frame["ordinal"],
                            frame["time_ms"], frame.get("width"), frame.get("height"),
                            frame.get("reason") or "even", now(),
                        ),
                    )
                elif any(existing[field] != frame.get(field) for field in ("artifact_sha256", "ordinal", "time_ms", "width", "height")):
                    raise ValueError("同一视频分析身份的关键帧不一致")
                elif existing["reason"] != (frame.get("reason") or "even"):
                    raise ValueError("同一视频分析身份的关键帧不一致")
            actual = self._rows(connection.execute(
                "SELECT * FROM video_frames WHERE video_analysis_id=? ORDER BY ordinal", (analysis["id"],)
            ).fetchall())
            if len(actual) != len(frames):
                raise ValueError("视频关键帧集合不完整")
            return {"analysis": analysis, "frames": actual}

    def video_analysis_for_version(self, version_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            analysis = self._row(connection.execute(
                "SELECT * FROM video_analyses WHERE content_version_id=? ORDER BY created_at DESC LIMIT 1", (version_id,)
            ).fetchone())
            if analysis is not None:
                analysis["frames"] = self._rows(connection.execute(
                    "SELECT * FROM video_frames WHERE video_analysis_id=? ORDER BY ordinal", (analysis["id"],)
                ).fetchall())
            return analysis

    def list_video_analyses(self, version_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            analyses = self._rows(connection.execute(
                "SELECT * FROM video_analyses WHERE content_version_id=? ORDER BY created_at DESC", (version_id,)
            ).fetchall())
            for analysis in analyses:
                analysis["frames"] = self._rows(connection.execute(
                    "SELECT * FROM video_frames WHERE video_analysis_id=? ORDER BY ordinal", (analysis["id"],)
                ).fetchall())
            return analyses

    def list_video_frames(self, video_analysis_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute(
                "SELECT * FROM video_frames WHERE video_analysis_id=? ORDER BY ordinal", (video_analysis_id,)
            ).fetchall())

    def list_video_artifacts(self, source_id: str) -> list[str]:
        with self.connection() as connection:
            return [row["artifact_sha256"] for row in connection.execute(
                "SELECT frame.artifact_sha256 FROM video_frames frame "
                "JOIN video_analyses analysis ON analysis.id=frame.video_analysis_id "
                "JOIN content_versions version ON version.id=analysis.content_version_id WHERE version.source_id=?",
                (source_id,),
            )]

    def list_sources(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        with self.connection() as connection:
            return self._rows(connection.execute(f"SELECT * FROM sources {where} ORDER BY updated_at DESC, title COLLATE NOCASE ASC").fetchall())

    def get_source(self, source_id: str, include_deleted: bool = True) -> dict[str, Any] | None:
        with self.connection() as connection:
            query = "SELECT * FROM sources WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL")
            return self._row(connection.execute(query, (source_id,)).fetchone())

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute("SELECT * FROM content_versions WHERE id=?", (version_id,)).fetchone())

    def versions_for_source(self, source_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM content_versions WHERE source_id=? ORDER BY ordinal DESC", (source_id,)).fetchall())

    def update_source_metadata(self, source_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"title", "author", "language", "notes", "source_date", "domains_json", "genres_json", "tags_json"}
        fields = [(key, value) for key, value in values.items() if key in allowed]
        if not fields:
            return self.get_source(source_id)
        assignments = ", ".join(f"{key}=?" for key, _ in fields) + ", updated_at=?"
        with self.connection() as connection:
            stamp = now()
            updated = connection.execute(f"UPDATE sources SET {assignments} WHERE id=?", [value for _, value in fields] + [stamp, source_id]).rowcount
            if updated:
                self._record_metadata_revision(connection, source_id, stamp)
        return self.get_source(source_id)

    def update_rights(self, source_id: str, rights: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            stamp = now()
            updated = connection.execute("UPDATE sources SET rights=?, updated_at=? WHERE id=?", (rights, stamp, source_id)).rowcount
            if updated:
                self._record_metadata_revision(connection, source_id, stamp)
        return self.get_source(source_id)

    def metadata_revisions_for_source(self, source_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute(
                "SELECT * FROM source_metadata_revisions WHERE source_id=? ORDER BY ordinal DESC", (source_id,)
            ).fetchall())

    def add_relation(self, source_id: str, related_source_id: str, relation_type: str) -> dict[str, Any]:
        relation_id = identifier()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO source_relations(id,source_id,related_source_id,relation_type,created_at) VALUES(?,?,?,?,?)",
                (relation_id, source_id, related_source_id, relation_type, now()),
            )
            return self._row(connection.execute("SELECT * FROM source_relations WHERE id=?", (relation_id,)).fetchone()) or {}

    def relations_for_source(self, source_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM source_relations WHERE source_id=? OR related_source_id=?", (source_id, source_id)).fetchall())

    def delete_relation(self, relation_id: str) -> bool:
        with self.connection() as connection:
            return bool(connection.execute("DELETE FROM source_relations WHERE id=?", (relation_id,)).rowcount)

    def update_processing(self, source_id: str, state: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE sources SET processing_state=?, updated_at=? WHERE id=?", (state, now(), source_id))

    def set_version_completeness(self, version_id: str, completeness: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE content_versions SET completeness=? WHERE id=?", (completeness, version_id))

    def persist_representation_bundle(
        self,
        *,
        version_id: str,
        artifact_sha256: str,
        kind: str,
        parser_name: str,
        config_hash: str,
        text: str,
        parent_id: str | None,
        chunks: list[tuple[str, str]],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connection() as connection:
            version = connection.execute(
                "SELECT source_id,artifact_sha256 FROM content_versions WHERE id=?", (version_id,)
            ).fetchone()
            if version is None:
                raise KeyError("内容版本不存在")
            if version["artifact_sha256"] != artifact_sha256:
                raise ValueError("证据 artifact 与内容版本不一致")
            representation = None
            if kind == "extraction":
                representation = self._row(connection.execute(
                    "SELECT * FROM representations WHERE content_version_id=? AND kind='extraction' "
                    "AND parser_name=? AND config_hash=?",
                    (version_id, parser_name, config_hash),
                ).fetchone())
                if representation is not None and representation["text_content"] != text:
                    raise ValueError("同一抽取身份的表示内容不一致")
            if representation is None:
                representation_id = identifier()
                stamp = now()
                insert = "INSERT OR IGNORE INTO" if kind == "extraction" else "INSERT INTO"
                connection.execute(
                    f"{insert} representations(id,content_version_id,kind,parser_name,config_hash,"
                    "parent_representation_id,text_content,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (representation_id, version_id, kind, parser_name, config_hash, parent_id, text, stamp),
                )
                if kind == "extraction":
                    representation = self._row(connection.execute(
                        "SELECT * FROM representations WHERE content_version_id=? AND kind='extraction' "
                        "AND parser_name=? AND config_hash=?",
                        (version_id, parser_name, config_hash),
                    ).fetchone())
                else:
                    representation = self._row(connection.execute(
                        "SELECT * FROM representations WHERE id=?", (representation_id,)
                    ).fetchone())
                assert representation is not None
                if representation["text_content"] != text:
                    raise ValueError("同一抽取身份的表示内容不一致")
            representation_id = representation["id"]
            for ordinal, (chunk_text, text_hash) in enumerate(chunks):
                existing = connection.execute(
                    "SELECT text_content,text_hash FROM search_chunks WHERE representation_id=? AND ordinal=?",
                    (representation_id, ordinal),
                ).fetchone()
                if existing is None:
                    chunk_id = identifier()
                    connection.execute(
                        "INSERT OR IGNORE INTO search_chunks(id,source_id,content_version_id,representation_id,ordinal,"
                        "text_content,text_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (chunk_id, version["source_id"], version_id, representation_id, ordinal,
                         chunk_text, text_hash, now()),
                    )
                    existing = connection.execute(
                        "SELECT text_content,text_hash FROM search_chunks WHERE representation_id=? AND ordinal=?",
                        (representation_id, ordinal),
                    ).fetchone()
                    assert existing is not None
                if existing["text_content"] != chunk_text or existing["text_hash"] != text_hash:
                    raise ValueError("表示检索块与既有派生数据不一致")
            actual_chunk_count = connection.execute(
                "SELECT COUNT(*) AS count FROM search_chunks WHERE representation_id=?", (representation_id,)
            ).fetchone()["count"]
            if actual_chunk_count != len(chunks):
                raise ValueError("表示检索块集合不完整")
            evidence_items: list[dict[str, Any]] = []
            citations: list[dict[str, Any]] = []
            for item in evidence:
                locator_json, locator_hash = self._locator_json_and_hash(item["locator"])
                existing = self._row(connection.execute(
                    "SELECT * FROM evidence WHERE representation_id=? AND locator_hash=? AND excerpt_hash=?",
                    (representation_id, locator_hash, item["excerpt_hash"]),
                ).fetchone())
                if existing is None:
                    evidence_id = identifier()
                    connection.execute(
                        "INSERT OR IGNORE INTO evidence(id,content_version_id,artifact_sha256,representation_id,parser_config_hash,"
                        "locator_json,locator_hash,excerpt,excerpt_hash,is_validated,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (evidence_id, version_id, artifact_sha256, representation_id, config_hash, locator_json,
                         locator_hash, item["excerpt"], item["excerpt_hash"], int(item.get("is_validated", True)), now()),
                    )
                    existing = self._row(connection.execute(
                        "SELECT * FROM evidence WHERE representation_id=? AND locator_hash=? AND excerpt_hash=?",
                        (representation_id, locator_hash, item["excerpt_hash"]),
                    ).fetchone())
                    assert existing is not None
                elif (
                    existing["content_version_id"] != version_id
                    or existing["artifact_sha256"] != artifact_sha256
                    or existing["parser_config_hash"] != config_hash
                    or existing["locator_json"] != locator_json
                    or existing["excerpt"] != item["excerpt"]
                    or bool(existing["is_validated"]) != bool(item.get("is_validated", True))
                ):
                    raise ValueError("表示证据与既有派生数据不一致")
                evidence_items.append(existing)
                citation = self._row(connection.execute(
                    "SELECT * FROM citations WHERE evidence_id=?", (existing["id"],)
                ).fetchone())
                if citation is None:
                    citation_id = identifier()
                    connection.execute(
                        "INSERT OR IGNORE INTO citations(id,evidence_id,created_at) VALUES(?,?,?)",
                        (citation_id, existing["id"], now()),
                    )
                    citation = self._row(connection.execute(
                        "SELECT * FROM citations WHERE evidence_id=?", (existing["id"],)
                    ).fetchone())
                    assert citation is not None
                citations.append(citation)
            actual_evidence_count = connection.execute(
                "SELECT COUNT(*) AS count FROM evidence WHERE representation_id=?", (representation_id,)
            ).fetchone()["count"]
            if actual_evidence_count != len(evidence):
                raise ValueError("表示证据集合不完整")
            return {
                "representation": representation,
                "evidence": evidence_items[0],
                "evidence_items": evidence_items,
                "citation": citations[0],
                "citations": citations,
                "search_chunks": self._rows(connection.execute(
                    "SELECT * FROM search_chunks WHERE representation_id=? ORDER BY ordinal", (representation_id,)
                ).fetchall()),
            }

    def representation_bundle_complete(
        self,
        representation_id: str,
        *,
        version_id: str,
        artifact_sha256: str,
        kind: str,
        parser_name: str,
        config_hash: str,
        text: str,
        chunks: list[tuple[str, str]],
        evidence: list[dict[str, Any]],
    ) -> bool:
        with self.connection() as connection:
            representation = connection.execute(
                "SELECT * FROM representations WHERE id=?", (representation_id,)
            ).fetchone()
            if representation is None or any((
                representation["content_version_id"] != version_id,
                representation["kind"] != kind,
                representation["parser_name"] != parser_name,
                representation["config_hash"] != config_hash,
                representation["text_content"] != text,
            )):
                return False
            actual_chunks = connection.execute(
                "SELECT ordinal,text_content,text_hash FROM search_chunks WHERE representation_id=? ORDER BY ordinal",
                (representation_id,),
            ).fetchall()
            if [(row["text_content"], row["text_hash"]) for row in actual_chunks] != chunks:
                return False
            actual_evidence = connection.execute(
                "SELECT id,content_version_id,artifact_sha256,parser_config_hash,locator_json,locator_hash,excerpt,"
                "excerpt_hash,is_validated FROM evidence WHERE representation_id=?", (representation_id,)
            ).fetchall()
            if len(actual_evidence) != len(evidence):
                return False
            expected_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
            for item in evidence:
                locator_json, locator_hash = self._locator_json_and_hash(item["locator"])
                expected_by_identity[(locator_hash, item["excerpt_hash"])] = {
                    **item, "locator_json": locator_json, "locator_hash": locator_hash,
                }
            actual_ids: list[str] = []
            for item in actual_evidence:
                expected = expected_by_identity.get((item["locator_hash"], item["excerpt_hash"]))
                if expected is None or (
                    item["content_version_id"] != version_id
                    or item["artifact_sha256"] != artifact_sha256
                    or item["parser_config_hash"] != config_hash
                    or item["locator_json"] != expected["locator_json"]
                    or item["excerpt"] != expected["excerpt"]
                    or bool(item["is_validated"]) != bool(expected.get("is_validated", True))
                ):
                    return False
                actual_ids.append(item["id"])
            citation_count = connection.execute(
                "SELECT COUNT(*) AS count FROM citations WHERE evidence_id IN "
                f"({','.join('?' for _ in actual_ids)})",
                actual_ids,
            ).fetchone()["count"] if actual_ids else 0
            return citation_count == len(actual_ids)

    def create_representation(
        self, version_id: str, kind: str, parser_name: str, config_hash: str, text: str, parent_id: str | None = None
    ) -> dict[str, Any]:
        representation_id = identifier()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO representations(id,content_version_id,kind,parser_name,config_hash,parent_representation_id,text_content,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (representation_id, version_id, kind, parser_name, config_hash, parent_id, text, now()),
            )
            return self._row(connection.execute("SELECT * FROM representations WHERE id=?", (representation_id,)).fetchone()) or {}

    def find_extraction_representation(self, version_id: str, parser_name: str, config_hash: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute(
                "SELECT * FROM representations WHERE content_version_id=? AND kind='extraction' AND parser_name=? AND config_hash=? ORDER BY created_at LIMIT 1",
                (version_id, parser_name, config_hash),
            ).fetchone())

    def representations_for_version(self, version_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM representations WHERE content_version_id=? ORDER BY created_at ASC", (version_id,)).fetchall())

    def get_representation(self, representation_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute("SELECT * FROM representations WHERE id=?", (representation_id,)).fetchone())

    def create_search_chunks(self, source_id: str, version_id: str, representation_id: str, chunks: list[tuple[str, str]]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        with self.connection() as connection:
            for ordinal, (text, text_hash) in enumerate(chunks):
                chunk_id = identifier()
                connection.execute(
                    """INSERT INTO search_chunks(id,source_id,content_version_id,representation_id,ordinal,text_content,text_hash,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (chunk_id, source_id, version_id, representation_id, ordinal, text, text_hash, now()),
                )
                created.append(self._row(connection.execute("SELECT * FROM search_chunks WHERE id=?", (chunk_id,)).fetchone()) or {})
        return created

    def search_chunks_for_representation(self, representation_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM search_chunks WHERE representation_id=? ORDER BY ordinal", (representation_id,)).fetchall())

    def create_evidence(
        self, *, version_id: str, artifact_sha256: str, representation_id: str, parser_config_hash: str,
        locator: dict[str, Any], excerpt: str, excerpt_hash: str, is_validated: bool = True
    ) -> dict[str, Any]:
        evidence_id = identifier()
        locator_json, locator_hash = self._locator_json_and_hash(locator)
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO evidence(id,content_version_id,artifact_sha256,representation_id,parser_config_hash,locator_json,locator_hash,excerpt,excerpt_hash,is_validated,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (evidence_id, version_id, artifact_sha256, representation_id, parser_config_hash, locator_json, locator_hash, excerpt, excerpt_hash, int(is_validated), now()),
            )
            return self._row(connection.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()) or {}

    def evidence_for_representation(self, representation_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM evidence WHERE representation_id=? ORDER BY created_at", (representation_id,)).fetchall())

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone())

    def create_citation(self, evidence_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO citations(id,evidence_id,created_at) VALUES(?,?,?)",
                (identifier(), evidence_id, now()),
            )
            return self._row(connection.execute(
                "SELECT * FROM citations WHERE evidence_id=?", (evidence_id,)
            ).fetchone()) or {}

    def citations_for_evidence(self, evidence_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM citations WHERE evidence_id=? ORDER BY created_at", (evidence_id,)).fetchall())

    def citation_details(self, citation_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute(
                """SELECT c.id, c.created_at, e.id AS evidence_id, e.locator_json, e.excerpt, e.representation_id,
                          s.id AS source_id, s.title, s.processing_state, r.kind AS representation_kind
                   FROM citations c JOIN evidence e ON e.id=c.evidence_id
                   JOIN content_versions v ON v.id=e.content_version_id
                   JOIN sources s ON s.id=v.source_id JOIN representations r ON r.id=e.representation_id WHERE c.id=?""",
                (citation_id,),
            ).fetchone())

    def create_knowledge(self, kind: str, statement: str, evidence_ids: list[str]) -> dict[str, Any]:
        knowledge_id = identifier()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO knowledge(id,kind,statement,status,created_at,published_at) VALUES(?,?,?,?,?,NULL)",
                (knowledge_id, kind, statement, "draft", now()),
            )
            for evidence_id in sorted(set(evidence_ids)):
                connection.execute("INSERT INTO knowledge_evidence(knowledge_id,evidence_id) VALUES(?,?)", (knowledge_id, evidence_id))
            return self._row(connection.execute("SELECT * FROM knowledge WHERE id=?", (knowledge_id,)).fetchone()) or {}

    def get_knowledge(self, knowledge_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = self._row(connection.execute("SELECT * FROM knowledge WHERE id=?", (knowledge_id,)).fetchone())
            if row is not None:
                row["evidence_ids"] = [r["evidence_id"] for r in connection.execute("SELECT evidence_id FROM knowledge_evidence WHERE knowledge_id=?", (knowledge_id,))]
            return row

    def list_knowledge(self, published_only: bool = False) -> list[dict[str, Any]]:
        with self.connection() as connection:
            where = "WHERE status='published'" if published_only else ""
            rows = self._rows(connection.execute(f"SELECT * FROM knowledge {where} ORDER BY created_at DESC").fetchall())
            for row in rows:
                row["evidence_ids"] = [item["evidence_id"] for item in connection.execute("SELECT evidence_id FROM knowledge_evidence WHERE knowledge_id=?", (row["id"],))]
            return rows

    def publish_knowledge(self, knowledge_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE knowledge SET status='published', published_at=? WHERE id=?", (now(), knowledge_id))
        return self.get_knowledge(knowledge_id)

    def create_job(self, kind: str, source_id: str | None, version_id: str | None, artifact_sha256: str | None, config_hash: str | None, payload: dict[str, Any], priority: int = 0) -> dict[str, Any]:
        job_id = identifier()
        stamp = now()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO jobs(id,kind,source_id,content_version_id,artifact_sha256,config_hash,payload_json,priority,state,progress,message,attempt_count,max_attempts,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,? ,0,NULL,0,?,?,?)""",
                (job_id, kind, source_id, version_id, artifact_sha256, config_hash, json.dumps(payload), priority, "queued", self._configured_max_attempts(connection), stamp, stamp),
            )
            return self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()) or {}

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall())

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    @staticmethod
    def _configured_job_lease_seconds(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT value FROM settings WHERE key='job_lease_seconds'").fetchone()
        try:
            return max(60, min(86_400, int(row["value"]))) if row else 300
        except (TypeError, ValueError):
            return 300

    def _lease_expires_at(self, connection: sqlite3.Connection, stamp: str) -> str:
        return (
            datetime.fromisoformat(stamp)
            + timedelta(seconds=self._configured_job_lease_seconds(connection))
        ).isoformat()

    def _lease_is_active_sql(self) -> str:
        return "lease_expires_at > clock_timestamp()" if self.backend == "postgresql" else "julianday(lease_expires_at) > julianday('now')"

    def _recover_stale_jobs(self, connection: sqlite3.Connection) -> None:
        stamp = now()
        legacy_cutoff = (
            datetime.now(UTC)
            - timedelta(seconds=self._configured_job_lease_seconds(connection))
        ).isoformat()
        lock = " FOR UPDATE SKIP LOCKED" if self.backend == "postgresql" else ""
        rows = connection.execute(
            "SELECT id,attempt_count,retry_count,max_attempts,lease_token FROM jobs "
            "WHERE state='running' AND ("
            "(lease_token IS NOT NULL AND lease_expires_at <= ?) OR "
            "(lease_token IS NULL AND COALESCE(heartbeat_at,started_at,updated_at) < ?)"
            ")" + lock,
            (stamp, legacy_cutoff),
        ).fetchall()
        for row in rows:
            state = "retry_wait" if row["retry_count"] < row["max_attempts"] else "failed"
            fields = [
                "state=?",
                "message=?",
                "updated_at=?",
                "lease_token=NULL",
                "lease_expires_at=NULL",
            ]
            values: list[Any] = [state, "工作进程失联，已按租约回收", stamp]
            if state == "failed":
                fields.append("completed_at=?")
                values.append(stamp)
            if row["lease_token"] is None:
                ownership_clause = (
                    "lease_token IS NULL AND "
                    "COALESCE(heartbeat_at,started_at,updated_at) < ?"
                )
                values.extend([row["id"], legacy_cutoff])
            else:
                ownership_clause = "lease_token=? AND lease_expires_at <= ?"
                values.extend([row["id"], row["lease_token"], stamp])
            updated = connection.execute(
                f"UPDATE jobs SET {', '.join(fields)} WHERE id=? AND state='running' AND {ownership_clause}",
                values,
            ).rowcount
            if not updated:
                continue
            if row["lease_token"] is None:
                attempt_ownership = "lease_token IS NULL"
                attempt_values: list[Any] = ["failed", stamp, "lease_expired", row["id"]]
            else:
                attempt_ownership = "lease_token=?"
                attempt_values = ["failed", stamp, "lease_expired", row["id"], row["lease_token"]]
            connection.execute(
                "UPDATE job_attempts SET state=?,ended_at=?,outcome=? "
                "WHERE job_id=? AND ended_at IS NULL AND " + attempt_ownership,
                attempt_values,
            )

    def claim_next_job(self) -> dict[str, Any] | None:
        with self.connection() as connection:
            if self.backend == "sqlite":
                connection.execute("BEGIN IMMEDIATE")
            self._recover_stale_jobs(connection)
            selection_lock = " FOR UPDATE SKIP LOCKED" if self.backend == "postgresql" else ""
            row = connection.execute(
                "SELECT id FROM jobs WHERE state IN ('queued','retry_wait') "
                "ORDER BY priority DESC, created_at ASC LIMIT 1" + selection_lock
            ).fetchone()
            if row is None:
                return None
            job_id = row["id"]
            stamp = now()
            lease_token = identifier()
            lease_expires_at = self._lease_expires_at(connection, stamp)
            updated = connection.execute(
                "UPDATE jobs SET state='running', attempt_count=attempt_count+1, "
                "retry_count=CASE WHEN state='retry_wait' THEN retry_count+1 ELSE retry_count END, "
                "started_at=?, heartbeat_at=?, lease_token=?, lease_expires_at=?, updated_at=?, "
                "completed_at=NULL "
                "WHERE id=? AND state IN ('queued','retry_wait')",
                (stamp, stamp, lease_token, lease_expires_at, stamp, job_id),
            ).rowcount
            if not updated:
                return None
            current = self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            assert current is not None
            connection.execute(
                "INSERT INTO job_attempts(id,job_id,attempt_number,lease_token,state,started_at) VALUES(?,?,?,?,?,?)",
                (identifier(), job_id, current["attempt_count"], lease_token, "running", stamp),
            )
            return current

    def update_job(
        self,
        job_id: str,
        lease_token: str,
        *,
        state: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        done: bool = False,
        outcome: str | None = None,
        settings: dict[str, str | int] | None = None,
    ) -> bool:
        with self.connection() as connection:
            stamp = now()
            fields: list[str] = ["updated_at=?", "heartbeat_at=?"]
            values: list[Any] = [stamp, stamp]
            if state is not None:
                fields.append("state=?")
                values.append(state)
            if progress is not None:
                fields.append("progress=?")
                values.append(max(0, min(100, progress)))
            if message is not None:
                fields.append("message=?")
                values.append(message)
            if not done:
                fields.append("lease_expires_at=?")
                values.append(self._lease_expires_at(connection, stamp))
                return bool(
                    connection.execute(
                        f"UPDATE jobs SET {', '.join(fields)} "
                        "WHERE id=? AND state='running' AND lease_token=? "
                        f"AND {self._lease_is_active_sql()}",
                        [*values, job_id, lease_token],
                    ).rowcount
                )
            fields.extend(("lease_token=NULL", "lease_expires_at=NULL"))
            if state in {"succeeded", "failed", "blocked", "cancelled"}:
                fields.append("completed_at=?")
                values.append(stamp)
            updated = connection.execute(
                f"UPDATE jobs SET {', '.join(fields)} "
                "WHERE id=? AND state='running' AND lease_token=? "
                f"AND {self._lease_is_active_sql()}",
                [*values, job_id, lease_token],
            ).rowcount
            if not updated:
                return False
            if settings:
                for key, value in settings.items():
                    connection.execute(
                        "UPDATE settings SET value=?, updated_at=? WHERE key=?",
                        (str(value), stamp, key),
                    )
            attempt_state = state if state in {"succeeded", "failed", "blocked", "cancelled"} else "failed"
            connection.execute(
                "UPDATE job_attempts SET state=?, ended_at=?, outcome=? "
                "WHERE job_id=? AND lease_token=? AND ended_at IS NULL",
                (attempt_state, stamp, outcome or attempt_state, job_id, lease_token),
            )
            return True

    def touch_job(self, job_id: str, lease_token: str) -> bool:
        with self.connection() as connection:
            stamp = now()
            return bool(
                connection.execute(
                    "UPDATE jobs SET heartbeat_at=?, lease_expires_at=?, updated_at=? "
                    "WHERE id=? AND state='running' AND lease_token=? "
                    f"AND {self._lease_is_active_sql()}",
                    (
                        stamp,
                        self._lease_expires_at(connection, stamp),
                        stamp,
                        job_id,
                        lease_token,
                    ),
                ).rowcount
            )

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE jobs SET cancel_requested_at=?, updated_at=? WHERE id=? AND state IN ('queued','retry_wait','running')", (now(), now(), job_id))
        return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            stamp = now()
            updated = connection.execute(
                "UPDATE jobs SET state='queued', progress=0, message=NULL, retry_count=0, "
                "cancel_requested_at=NULL, lease_token=NULL, lease_expires_at=NULL, started_at=NULL, "
                "heartbeat_at=NULL, completed_at=NULL, priority=100, updated_at=? "
                "WHERE id=? AND state IN ('failed','blocked','cancelled')",
                (stamp, job_id),
            ).rowcount
            if updated:
                return self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            exists = connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        if exists is None:
            return None
        raise ValueError("仅失败、阻塞或已取消的作业可以手动重试")

    def job_cancel_requested(self, job_id: str) -> bool:
        with self.connection() as connection:
            row = connection.execute("SELECT cancel_requested_at FROM jobs WHERE id=?", (job_id,)).fetchone()
            return bool(row and row["cancel_requested_at"])

    def create_external_card(self, card_type: str, url: str, title: str, author: str | None, notes: str | None, tags: list[str]) -> dict[str, Any]:
        card_id = identifier()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO external_cards(id,card_type,url,title,author,notes,tags_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (card_id, card_type, url, title, author, notes, json.dumps(sorted(set(tags))), now()),
            )
            return self._row(connection.execute("SELECT * FROM external_cards WHERE id=?", (card_id,)).fetchone()) or {}

    def list_external_cards(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self._rows(connection.execute("SELECT * FROM external_cards ORDER BY created_at DESC").fetchall())
        for row in rows:
            row["url"] = redact_url_userinfo(row["url"])
        return rows

    def create_topic(self, name: str, source_ids: list[str]) -> dict[str, Any]:
        topic_id = identifier()
        with self.connection() as connection:
            connection.execute("INSERT INTO topics(id,name,created_at) VALUES(?,?,?)", (topic_id, name, now()))
            for source_id in sorted(set(source_ids)):
                connection.execute("INSERT INTO topic_sources(topic_id,source_id) VALUES(?,?)", (topic_id, source_id))
            return self._row(connection.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()) or {}

    def list_topics(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            topics = self._rows(connection.execute("SELECT * FROM topics ORDER BY name COLLATE NOCASE").fetchall())
            for topic in topics:
                topic["source_ids"] = [row["source_id"] for row in connection.execute("SELECT source_id FROM topic_sources WHERE topic_id=?", (topic["id"],))]
            return topics

    def add_source_to_topic(self, topic_id: str, source_id: str) -> bool:
        with self.connection() as connection:
            topic = connection.execute("SELECT 1 FROM topics WHERE id=?", (topic_id,)).fetchone()
            source = connection.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone()
            if topic is None or source is None:
                return False
            connection.execute("INSERT OR IGNORE INTO topic_sources(topic_id,source_id) VALUES(?,?)", (topic_id, source_id))
            return True

    def rename_topic(self, topic_id: str, name: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            updated = connection.execute("UPDATE topics SET name=? WHERE id=?", (name, topic_id)).rowcount
            if not updated:
                return None
            return self._row(connection.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone())

    def remove_source_from_topic(self, topic_id: str, source_id: str) -> bool:
        with self.connection() as connection:
            return bool(connection.execute("DELETE FROM topic_sources WHERE topic_id=? AND source_id=?", (topic_id, source_id)).rowcount)

    def delete_topic(self, topic_id: str) -> bool:
        with self.connection() as connection:
            connection.execute("DELETE FROM topic_sources WHERE topic_id=?", (topic_id,))
            return bool(connection.execute("DELETE FROM topics WHERE id=?", (topic_id,)).rowcount)

    def source_ids_for_topic(self, topic_id: str) -> set[str]:
        with self.connection() as connection:
            return {row["source_id"] for row in connection.execute("SELECT source_id FROM topic_sources WHERE topic_id=?", (topic_id,))}

    def same_work_candidates(self, source_id: str) -> list[dict[str, Any]]:
        """未声明为同一作品的潜在重复来源：同一 artifact 内容或规范化标题相同。"""
        with self.connection() as connection:
            source = connection.execute("SELECT id,title FROM sources WHERE id=? AND deleted_at IS NULL", (source_id,)).fetchone()
            if source is None:
                return []
            declared = {
                row["related_source_id"] if row["source_id"] == source_id else row["source_id"]
                for row in connection.execute(
                    "SELECT source_id,related_source_id FROM source_relations WHERE relation_type='user_declared_same_work' AND (source_id=? OR related_source_id=?)",
                    (source_id, source_id),
                )
            }
            candidates: dict[str, dict[str, Any]] = {}
            for row in connection.execute(
                """SELECT DISTINCT s.id, s.title FROM content_versions cv
                   JOIN content_versions other ON other.artifact_sha256=cv.artifact_sha256 AND other.source_id!=cv.source_id
                   JOIN sources s ON s.id=other.source_id
                   WHERE cv.source_id=? AND s.deleted_at IS NULL""",
                (source_id,),
            ).fetchall():
                if row["id"] not in declared:
                    candidates[row["id"]] = {"id": row["id"], "title": row["title"], "reason": "same_artifact"}
            normalized = " ".join(source["title"].split()).casefold()
            if normalized:
                for row in connection.execute("SELECT id,title FROM sources WHERE id!=? AND deleted_at IS NULL", (source_id,)).fetchall():
                    if row["id"] in declared or row["id"] in candidates:
                        continue
                    if " ".join(row["title"].split()).casefold() == normalized:
                        candidates[row["id"]] = {"id": row["id"], "title": row["title"], "reason": "same_title"}
            return sorted(candidates.values(), key=lambda item: (item["title"].casefold(), item["id"]))

    def soft_delete_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE sources SET deleted_at=?, updated_at=? WHERE id=? AND deleted_at IS NULL", (now(), now(), source_id))
        return self.get_source(source_id)

    def restore_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE sources SET deleted_at=NULL, updated_at=? WHERE id=?", (now(), source_id))
        return self.get_source(source_id)

    def source_artifacts(self, source_id: str) -> list[str]:
        with self.connection() as connection:
            return [row["artifact_sha256"] for row in connection.execute("SELECT artifact_sha256 FROM content_versions WHERE source_id=?", (source_id,))]

    def delete_artifact_if_unreferenced(self, sha256: str) -> bool:
        """Remove only an artifact with no remaining logical reference.

        Returning true when no reference exists permits callers to compensate a
        newly-created file even when its transaction never inserted the DB row.
        """
        with self.connection() as connection:
            referenced = connection.execute(
                """SELECT 1 FROM content_versions WHERE artifact_sha256=?
                   UNION SELECT 1 FROM video_frames WHERE artifact_sha256=?
                   UNION SELECT 1 FROM evidence WHERE artifact_sha256=?
                   UNION SELECT 1 FROM jobs WHERE artifact_sha256=? LIMIT 1""",
                (sha256, sha256, sha256, sha256),
            ).fetchone()
            if referenced:
                return False
            connection.execute("DELETE FROM artifacts WHERE sha256=?", (sha256,))
            return True

    def purge_source(self, source_id: str) -> list[str]:
        """Delete logical data and return artifacts no longer referenced by active sources."""
        with self.connection() as connection:
            source = connection.execute("SELECT deleted_at FROM sources WHERE id=?", (source_id,)).fetchone()
            if not source:
                raise KeyError("来源不存在")
            if source["deleted_at"] is None:
                raise ValueError("必须先软删除来源")
            hashes = [r["artifact_sha256"] for r in connection.execute("SELECT artifact_sha256 FROM content_versions WHERE source_id=?", (source_id,))]
            hashes.extend(r["artifact_sha256"] for r in connection.execute(
                "SELECT frame.artifact_sha256 FROM video_frames frame "
                "JOIN video_analyses analysis ON analysis.id=frame.video_analysis_id "
                "JOIN content_versions version ON version.id=analysis.content_version_id WHERE version.source_id=?",
                (source_id,),
            ))
            connection.execute("DELETE FROM knowledge_evidence WHERE evidence_id IN (SELECT id FROM evidence WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?))", (source_id,))
            connection.execute("DELETE FROM citations WHERE evidence_id IN (SELECT id FROM evidence WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?))", (source_id,))
            connection.execute("DELETE FROM evidence WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM search_chunks WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM representations WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM job_attempts WHERE job_id IN (SELECT id FROM jobs WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM jobs WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM source_relations WHERE source_id=? OR related_source_id=?", (source_id, source_id))
            connection.execute("DELETE FROM topic_sources WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM video_frames WHERE video_analysis_id IN (SELECT id FROM video_analyses WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?))", (source_id,))
            connection.execute("DELETE FROM video_analyses WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM content_versions WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM source_metadata_revisions WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM video_download_provenance WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM sources WHERE id=?", (source_id,))
            orphaned: list[str] = []
            for sha256 in hashes:
                reference_count = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM content_versions WHERE artifact_sha256=?) + "
                    "(SELECT COUNT(*) FROM video_frames WHERE artifact_sha256=?) AS n",
                    (sha256, sha256),
                ).fetchone()["n"]
                if not reference_count:
                    connection.execute("DELETE FROM artifacts WHERE sha256=?", (sha256,))
                    orphaned.append(sha256)
            return orphaned

    def rows_for_export(self) -> dict[str, list[dict[str, Any]]]:
        with self.connection() as connection:
            rows = {table: self._rows(connection.execute(f"SELECT * FROM {table}").fetchall()) for table in EXPORT_TABLES}
        for card in rows["external_cards"]:
            card["url"] = redact_url_userinfo(card["url"])
        return rows

    def insert_export_rows(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        """Insert validated portable logical rows in foreign-key-safe order."""
        with self.connection() as connection:
            for table in EXPORT_TABLES:
                for row in rows_by_table.get(table, []):
                    columns = list(row)
                    placeholders = ",".join("?" for _ in columns)
                    connection.execute(
                        f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
                        [row[column] for column in columns],
                    )

    def rows_for_backup(self) -> dict[str, list[dict[str, Any]]]:
        with self.connection() as connection:
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

    def create_backup_record(self, archive_name: str, manifest_sha256: str, state: str = "succeeded") -> dict[str, Any]:
        backup_id = identifier()
        with self.connection() as connection:
            connection.execute("INSERT INTO backups(id,archive_name,manifest_sha256,state,created_at) VALUES(?,?,?,?,?)", (backup_id, archive_name, manifest_sha256, state, now()))
            return self._row(connection.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()) or {}

    def delete_backup_record(self, backup_id: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM backups WHERE id=?", (backup_id,))

    def update_backup_state(self, backup_id: str, state: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE backups SET state=? WHERE id=?", (state, backup_id))

    def list_backups(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM backups ORDER BY created_at DESC").fetchall())
